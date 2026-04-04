"""
Shared matrix benchmark helpers for backend/model/worker evaluation.

This module centralizes benchmark execution so backend-specific scripts can
reuse the same data flow, metrics, and report generation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Add 'src' to sys.path so we can import 'mailshift'
ROOT_DIR = Path(__file__).parent.parent.absolute()
SRC_PATH = str(ROOT_DIR / "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

from mailshift.config.config import LMStudioConfig, OllamaConfig
from mailshift.core.analyzers.fast import extract_fast_category, fast_analyze
from mailshift.core.analyzers.pro import (
    check_lm_studio_health,
    check_ollama_health,
    is_llm_error_reason,
    is_llm_timeout_reason,
    pro_analyze,
)
from mailshift.models.models import MailMeta, ScanResult
from mailshift.utils.hardware import SystemInfo, calculate_optimal_workers, get_system_info


DATASET_DEFAULT_LIMIT = 300
DEFAULT_BACKENDS = ["ollama", "lm_studio"]
DEFAULT_PROFILE_NAMES = ["low", "medium", "high"]


@dataclass(frozen=True)
class BenchmarkProfile:
    name: str
    description: str
    system_info: SystemInfo


def parse_csv_tokens(raw: str) -> list[str]:
    return [token.strip() for token in (raw or "").split(",") if token.strip()]


def parse_positive_int_csv(raw: str) -> list[int]:
    values = []
    for token in parse_csv_tokens(raw):
        if token.isdigit() and int(token) > 0:
            values.append(int(token))
    return sorted(set(values))


def load_dataset(dataset_path: Path, limit: int = DATASET_DEFAULT_LIMIT) -> list[dict[str, Any]]:
    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset not found at {dataset_path}")

    with open(dataset_path, "r", encoding="utf-8") as handle:
        dataset = json.load(handle)

    if limit > 0:
        return dataset[:limit]
    return dataset


def get_builtin_profiles() -> dict[str, BenchmarkProfile]:
    return {
        "low": BenchmarkProfile(
            name="low",
            description="4 CPU / 8 GB RAM / CPU-only",
            system_info=SystemInfo(
                cpu_count=4,
                total_ram_gb=8.0,
                available_ram_gb=6.0,
                has_gpu=False,
                gpu_name="None",
                vram_total_gb=0.0,
                vram_available_gb=0.0,
                gpu_driver="None",
            ),
        ),
        "medium": BenchmarkProfile(
            name="medium",
            description="8 CPU / 16 GB RAM / 8 GB VRAM",
            system_info=SystemInfo(
                cpu_count=8,
                total_ram_gb=16.0,
                available_ram_gb=12.0,
                has_gpu=True,
                gpu_name="Generic Mid GPU",
                vram_total_gb=8.0,
                vram_available_gb=6.0,
                gpu_driver="generic",
            ),
        ),
        "high": BenchmarkProfile(
            name="high",
            description="16 CPU / 32 GB RAM / 24 GB VRAM",
            system_info=SystemInfo(
                cpu_count=16,
                total_ram_gb=32.0,
                available_ram_gb=24.0,
                has_gpu=True,
                gpu_name="Generic High GPU",
                vram_total_gb=24.0,
                vram_available_gb=20.0,
                gpu_driver="generic",
            ),
        ),
        "host": BenchmarkProfile(
            name="host",
            description="Current host machine snapshot",
            system_info=get_system_info(),
        ),
    }


def resolve_profiles(profile_names: list[str]) -> list[BenchmarkProfile]:
    profiles = get_builtin_profiles()
    if not profile_names:
        profile_names = DEFAULT_PROFILE_NAMES

    resolved = []
    for name in profile_names:
        normalized = name.strip().lower()
        if normalized not in profiles:
            raise ValueError(
                f"Unknown profile '{name}'. Valid values: {', '.join(sorted(profiles.keys()))}"
            )
        resolved.append(profiles[normalized])
    return resolved


def normalize_backend_name(backend: str) -> str:
    return "lm_studio" if str(backend).strip().lower() == "lm_studio" else "ollama"


def build_runtime_config(backend: str, model_name: str) -> LMStudioConfig | OllamaConfig:
    backend_name = normalize_backend_name(backend)
    if backend_name == "lm_studio":
        cfg = LMStudioConfig()
        if model_name:
            return cfg.model_copy(update={"model": model_name})
        return cfg

    cfg = OllamaConfig()
    if model_name:
        return cfg.model_copy(update={"model": model_name})
    return cfg


def check_backend_health(backend: str, cfg: LMStudioConfig | OllamaConfig) -> tuple[bool, str]:
    backend_name = normalize_backend_name(backend)
    if backend_name == "lm_studio":
        return check_lm_studio_health(cfg.base_url, cfg.model)
    return check_ollama_health(cfg.base_url, cfg.model)


def default_worker_candidates(model_name: str, backend: str, profile: BenchmarkProfile) -> list[int]:
    backend_name = normalize_backend_name(backend)
    auto_workers = calculate_optimal_workers(
        model_name,
        mode="pro",
        backend=backend_name,
        system_info=profile.system_info,
    )

    upper_probe = min(16 if backend_name == "lm_studio" else 12, max(auto_workers + 2, auto_workers * 2))
    candidates = {
        1,
        max(1, auto_workers // 2),
        max(1, auto_workers - 1),
        auto_workers,
        min(upper_probe, auto_workers + 1),
        upper_probe,
    }

    return sorted(value for value in candidates if value > 0)


def _evaluate_item(item: dict[str, Any], cfg: LMStudioConfig | OllamaConfig, backend: str) -> dict[str, Any]:
    meta = MailMeta(
        uid=str(item.get("id", "")),
        subject=item.get("subject", ""),
        sender=item.get("sender", ""),
        body_preview=item.get("body_preview", ""),
    )

    fast_res = fast_analyze(meta)
    pro_res = fast_res
    ai_res = ScanResult(mail=meta, decision="TUT", reason="")
    ai_elapsed = 0.0
    run_ai = False

    if fast_res.decision == "SIL":
        run_ai = True
        ai_start = time.perf_counter()
        ai_res = pro_analyze(
            meta,
            cfg,
            backend=normalize_backend_name(backend),
            fast_reason=fast_res.reason,
            fast_category=extract_fast_category(fast_res.reason),
        )
        ai_elapsed = time.perf_counter() - ai_start
        pro_res = ai_res

    reason = ai_res.reason if run_ai else ""
    timeout_flag = bool(run_ai and is_llm_timeout_reason(reason))
    error_flag = bool(run_ai and is_llm_error_reason(reason) and not timeout_flag)

    expected = item.get("expected_decision", "TUT")
    return {
        "expected": expected,
        "fast": fast_res.decision,
        "pro": pro_res.decision,
        "run_ai": run_ai,
        "ai_latency": ai_elapsed,
        "timeout": timeout_flag,
        "error": error_flag,
    }


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    index = int((len(sorted_values) - 1) * q)
    return sorted_values[max(0, min(index, len(sorted_values) - 1))]


def _compute_metrics(results: list[dict[str, Any]], elapsed_s: float) -> dict[str, Any]:
    total = len(results)
    fast_correct = sum(1 for row in results if row["fast"] == row["expected"])
    pro_correct = sum(1 for row in results if row["pro"] == row["expected"])

    tp = sum(1 for row in results if row["expected"] == "SIL" and row["pro"] == "SIL")
    fp = sum(1 for row in results if row["expected"] == "TUT" and row["pro"] == "SIL")
    fn = sum(1 for row in results if row["expected"] == "SIL" and row["pro"] == "TUT")

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    ai_rows = [row for row in results if row["run_ai"]]
    latencies = [row["ai_latency"] for row in ai_rows]
    timeout_count = sum(1 for row in ai_rows if row["timeout"])
    error_count = sum(1 for row in ai_rows if row["error"])

    ai_calls = len(ai_rows)
    avg_ai_latency = sum(latencies) / ai_calls if ai_calls else 0.0
    p95_ai_latency = _percentile(latencies, 0.95)
    timeout_rate = timeout_count / ai_calls if ai_calls else 0.0
    error_rate = error_count / ai_calls if ai_calls else 0.0
    throughput = total / elapsed_s if elapsed_s > 0 else 0.0

    return {
        "total": total,
        "fast_accuracy": fast_correct / total if total else 0.0,
        "pro_accuracy": pro_correct / total if total else 0.0,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "ai_calls": ai_calls,
        "avg_ai_latency": avg_ai_latency,
        "p95_ai_latency": p95_ai_latency,
        "timeout_count": timeout_count,
        "timeout_rate": timeout_rate,
        "error_count": error_count,
        "error_rate": error_rate,
        "elapsed_s": elapsed_s,
        "throughput": throughput,
    }


def run_single_worker_sweep(
    worker_count: int,
    dataset: list[dict[str, Any]],
    backend: str,
    cfg: LMStudioConfig | OllamaConfig,
) -> dict[str, Any]:
    started = time.perf_counter()
    results: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(_evaluate_item, item, cfg, backend) for item in dataset]
        for future in as_completed(futures):
            results.append(future.result())

    elapsed_s = time.perf_counter() - started
    metrics = _compute_metrics(results, elapsed_s)
    metrics["workers"] = worker_count
    return metrics


def is_worse_than_previous(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    if current["pro_accuracy"] < previous["pro_accuracy"]:
        return True
    if current["pro_accuracy"] > previous["pro_accuracy"]:
        return False

    if current["f1"] < previous["f1"]:
        return True
    if current["f1"] > previous["f1"]:
        return False

    if current["throughput"] < previous["throughput"] * 0.98:
        return True
    if current["throughput"] > previous["throughput"]:
        return False

    if current["timeout_rate"] > previous["timeout_rate"]:
        return True
    if current["timeout_rate"] < previous["timeout_rate"]:
        return False

    return current["error_rate"] > previous["error_rate"]


def pick_best_run(runs: list[dict[str, Any]]) -> dict[str, Any]:
    if not runs:
        raise ValueError("No benchmark runs to compare")

    return sorted(
        runs,
        key=lambda row: (
            row["pro_accuracy"],
            row["f1"],
            row["throughput"],
            -row["timeout_rate"],
            -row["error_rate"],
        ),
        reverse=True,
    )[0]


def run_worker_sweep_for_case(
    dataset: list[dict[str, Any]],
    backend: str,
    cfg: LMStudioConfig | OllamaConfig,
    workers_to_test: list[int],
    stop_on_worse: bool = True,
) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    stopped_early = False

    for workers in workers_to_test:
        run_metrics = run_single_worker_sweep(workers, dataset, backend, cfg)
        runs.append(run_metrics)

        if stop_on_worse and len(runs) >= 2:
            previous = runs[-2]
            current = runs[-1]
            if is_worse_than_previous(previous, current):
                stopped_early = True
                break

    return {
        "runs": runs,
        "best": pick_best_run(runs) if runs else None,
        "stopped_early": stopped_early,
    }


def render_comparison_report(matrix_result: dict[str, Any]) -> str:
    lines = []
    lines.append("WORKER BENCHMARK MATRIX REPORT")
    lines.append("=" * 80)
    lines.append(f"Created at:   {matrix_result['created_at']}")
    lines.append(f"Dataset size: {matrix_result['dataset_size']}")
    lines.append(f"Cases:        {len(matrix_result['cases'])}")
    lines.append("")

    host_info = matrix_result.get("host_system", {})
    lines.append("Host snapshot")
    lines.append(
        f"  CPU={host_info.get('cpu_count', 0)} | "
        f"RAM={host_info.get('available_ram_gb', 0.0):.1f}/{host_info.get('total_ram_gb', 0.0):.1f} GB | "
        f"GPU={host_info.get('gpu_name', 'None')} | "
        f"VRAM={host_info.get('vram_available_gb', 0.0):.1f}/{host_info.get('vram_total_gb', 0.0):.1f} GB"
    )
    lines.append("-" * 80)

    for case in matrix_result["cases"]:
        lines.append(
            f"profile={case['profile']} ({case['profile_description']}) | "
            f"backend={case['backend']} | model={case['model']}"
        )

        if not case["health_ok"]:
            lines.append(f"  SKIPPED: {case['health_msg']}")
            lines.append("-" * 80)
            continue

        if case.get("stopped_early"):
            lines.append("  EARLY_STOP: current worker result was worse than previous test")

        for run in case["runs"]:
            winner_tag = " <= BEST" if run["workers"] == case["best"]["workers"] else ""
            lines.append(
                f"  workers={run['workers']:<3} | pro={run['pro_accuracy']:.2%} | "
                f"f1={run['f1']:.4f} | time={run['elapsed_s']:.2f}s | "
                f"throughput={run['throughput']:.2f}/s | avg_ai={run['avg_ai_latency']:.2f}s | "
                f"p95_ai={run['p95_ai_latency']:.2f}s | timeout={run['timeout_rate']:.1%} | "
                f"error={run['error_rate']:.1%}{winner_tag}"
            )

        best = case["best"]
        lines.append(
            f"  BEST={best['workers']} | pro={best['pro_accuracy']:.2%} | "
            f"f1={best['f1']:.4f} | throughput={best['throughput']:.2f}/s"
        )
        lines.append("-" * 80)

    lines.append("Best worker summary")
    for case in matrix_result["cases"]:
        if not case["health_ok"] or not case.get("best"):
            lines.append(
                f"  profile={case['profile']} backend={case['backend']} model={case['model']} -> SKIPPED"
            )
            continue

        best = case["best"]
        lines.append(
            f"  profile={case['profile']} backend={case['backend']} model={case['model']} -> "
            f"workers={best['workers']} (pro={best['pro_accuracy']:.2%}, f1={best['f1']:.4f}, "
            f"throughput={best['throughput']:.2f}/s)"
        )

    return "\n".join(lines) + "\n"


def save_matrix_results(
    matrix_result: dict[str, Any],
    output_json: Path,
    output_txt: Path,
) -> str:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_txt.parent.mkdir(parents=True, exist_ok=True)

    with open(output_json, "w", encoding="utf-8") as handle:
        json.dump(matrix_result, handle, ensure_ascii=False, indent=2)

    report = render_comparison_report(matrix_result)
    with open(output_txt, "w", encoding="utf-8") as handle:
        handle.write(report)

    return report


def run_worker_matrix_benchmark(
    dataset: list[dict[str, Any]],
    profiles: list[BenchmarkProfile],
    backend_models: list[tuple[str, str]],
    workers_override: list[int] | None = None,
    stop_on_worse: bool = True,
) -> dict[str, Any]:
    host_info = get_system_info()
    cases = []

    for profile in profiles:
        for backend, model_name in backend_models:
            backend_name = normalize_backend_name(backend)
            cfg = build_runtime_config(backend_name, model_name)
            health_ok, health_msg = check_backend_health(backend_name, cfg)

            case_result: dict[str, Any] = {
                "profile": profile.name,
                "profile_description": profile.description,
                "profile_system": asdict(profile.system_info),
                "backend": backend_name,
                "model": cfg.model,
                "health_ok": bool(health_ok),
                "health_msg": health_msg,
                "workers_tested": [],
                "runs": [],
                "best": None,
                "stopped_early": False,
            }

            if not health_ok:
                cases.append(case_result)
                continue

            worker_list = workers_override or default_worker_candidates(cfg.model, backend_name, profile)
            case_result["workers_tested"] = worker_list

            sweep = run_worker_sweep_for_case(
                dataset=dataset,
                backend=backend_name,
                cfg=cfg,
                workers_to_test=worker_list,
                stop_on_worse=stop_on_worse,
            )

            case_result["runs"] = sweep["runs"]
            case_result["best"] = sweep["best"]
            case_result["stopped_early"] = sweep["stopped_early"]
            cases.append(case_result)

    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset_size": len(dataset),
        "host_system": asdict(host_info),
        "profiles": [profile.name for profile in profiles],
        "backend_models": [
            {"backend": normalize_backend_name(backend), "model": model}
            for backend, model in backend_models
        ],
        "cases": cases,
    }
