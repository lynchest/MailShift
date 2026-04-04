"""
evaluate_worker_matrix.py

General worker benchmark matrix runner for MailShift.

Runs benchmark sweeps across:
- hardware profiles (low/medium/high/host)
- backends (ollama, lm_studio)
- models (per-backend lists)

Usage examples:
  py -3.14 tests/evaluate_worker_matrix.py
  py -3.14 tests/evaluate_worker_matrix.py --profiles low,medium,high --backends ollama,lm_studio
  py -3.14 tests/evaluate_worker_matrix.py --ollama-models qwen3.5:0.8B,qwen3.5:2B --lm-studio-models gemma-4-26b-a4b
  py -3.14 tests/evaluate_worker_matrix.py --workers 1,2,4,8
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add 'src' to sys.path so we can import 'mailshift'
ROOT_DIR = Path(__file__).parent.parent.absolute()
SRC_PATH = str(ROOT_DIR / "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

from mailshift.config.config import LMStudioConfig, OllamaConfig
from mailshift.ui.styles import console

from benchmark_matrix_helpers import (
    DATASET_DEFAULT_LIMIT,
    DEFAULT_BACKENDS,
    DEFAULT_PROFILE_NAMES,
    load_dataset,
    normalize_backend_name,
    parse_csv_tokens,
    parse_positive_int_csv,
    resolve_profiles,
    run_worker_matrix_benchmark,
    save_matrix_results,
)


DEFAULT_JSON_OUTPUT = Path(__file__).parent / "eval_worker_matrix_results.json"
DEFAULT_TXT_OUTPUT = Path(__file__).parent / "eval_worker_matrix_report.txt"
DEFAULT_DATASET = Path(__file__).parent / "test_ai_dataset.json"


def _resolve_backends(raw_backends: str) -> list[str]:
    tokens = parse_csv_tokens(raw_backends)
    candidates = tokens or DEFAULT_BACKENDS

    ordered = []
    seen = set()
    for backend in candidates:
        normalized = normalize_backend_name(backend)
        if normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)

    return ordered


def _resolve_backend_models(
    backends: list[str],
    models_raw: str,
    ollama_models_raw: str,
    lm_studio_models_raw: str,
) -> list[tuple[str, str]]:
    generic_models = parse_csv_tokens(models_raw)
    ollama_models = parse_csv_tokens(ollama_models_raw)
    lm_studio_models = parse_csv_tokens(lm_studio_models_raw)

    default_ollama = OllamaConfig().model
    default_lm_studio = LMStudioConfig().model

    matrix: list[tuple[str, str]] = []
    for backend in backends:
        if backend == "ollama":
            models = ollama_models or generic_models or [default_ollama]
        else:
            models = lm_studio_models or generic_models or [default_lm_studio]

        for model_name in models:
            matrix.append((backend, model_name))

    return matrix


def evaluate_matrix(
    dataset_limit: int,
    profiles_raw: str,
    backends_raw: str,
    models_raw: str,
    ollama_models_raw: str,
    lm_studio_models_raw: str,
    workers_raw: str,
    stop_on_worse: bool,
    output_json: Path,
    output_txt: Path,
) -> None:
    dataset = load_dataset(DEFAULT_DATASET, limit=dataset_limit)
    profiles = resolve_profiles(parse_csv_tokens(profiles_raw) or DEFAULT_PROFILE_NAMES)
    backends = _resolve_backends(backends_raw)
    backend_models = _resolve_backend_models(backends, models_raw, ollama_models_raw, lm_studio_models_raw)
    workers_override = parse_positive_int_csv(workers_raw)

    console.print("=" * 80)
    console.print("[bold white]WORKER BENCHMARK MATRIX[/bold white]")
    console.print("=" * 80)
    console.print(f"Dataset size: [green]{len(dataset)}[/green]")
    console.print(f"Profiles: [green]{', '.join(profile.name for profile in profiles)}[/green]")
    console.print(
        "Backend/Model pairs: "
        f"[green]{', '.join(f'{backend}:{model}' for backend, model in backend_models)}[/green]"
    )
    if workers_override:
        console.print(f"Workers override: [green]{', '.join(str(value) for value in workers_override)}[/green]")
    else:
        console.print("Workers override: [dim]auto per profile/backend/model[/dim]")
    console.print("")

    matrix_result = run_worker_matrix_benchmark(
        dataset=dataset,
        profiles=profiles,
        backend_models=backend_models,
        workers_override=workers_override or None,
        stop_on_worse=stop_on_worse,
    )

    report = save_matrix_results(matrix_result, output_json=output_json, output_txt=output_txt)

    for case in matrix_result["cases"]:
        case_title = (
            f"profile={case['profile']} | backend={case['backend']} | model={case['model']}"
        )
        if not case["health_ok"]:
            console.print(f"[yellow]SKIP[/yellow] {case_title} -> {case['health_msg']}")
            continue

        best = case["best"]
        console.print(
            f"[cyan]DONE[/cyan] {case_title} -> "
            f"best worker={best['workers']} | pro={best['pro_accuracy']:.2%} | "
            f"f1={best['f1']:.4f} | throughput={best['throughput']:.2f}/s"
        )

    console.print("\n" + "-" * 80)
    console.print(f"[dim]JSON saved to {output_json}[/dim]")
    console.print(f"[dim]TXT report saved to {output_txt}[/dim]")
    console.print("-" * 80)

    summary_lines = [line for line in report.splitlines() if line.startswith("  profile=")]
    if summary_lines:
        console.print("[bold white]Best worker summary[/bold white]")
        for line in summary_lines:
            console.print(line)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MailShift worker benchmark matrix runner")
    parser.add_argument(
        "--dataset-limit",
        type=int,
        default=DATASET_DEFAULT_LIMIT,
        help="Dataset size limit (default: 300)",
    )
    parser.add_argument(
        "--profiles",
        type=str,
        default=",".join(DEFAULT_PROFILE_NAMES),
        help="Comma-separated profile list: low,medium,high,host",
    )
    parser.add_argument(
        "--backends",
        type=str,
        default=",".join(DEFAULT_BACKENDS),
        help="Comma-separated backend list: ollama,lm_studio",
    )
    parser.add_argument(
        "--models",
        type=str,
        default="",
        help="Optional generic model list applied to all selected backends",
    )
    parser.add_argument(
        "--ollama-models",
        type=str,
        default="",
        help="Optional Ollama-only model list",
    )
    parser.add_argument(
        "--lm-studio-models",
        type=str,
        default="",
        help="Optional LM Studio-only model list",
    )
    parser.add_argument(
        "--workers",
        type=str,
        default="",
        help="Optional worker list override. Example: --workers 1,2,4,8",
    )
    parser.add_argument(
        "--no-early-stop",
        action="store_true",
        help="Disable early-stop when current worker is worse than previous",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=DEFAULT_JSON_OUTPUT,
        help=f"JSON output path (default: {DEFAULT_JSON_OUTPUT.name})",
    )
    parser.add_argument(
        "--output-txt",
        type=Path,
        default=DEFAULT_TXT_OUTPUT,
        help=f"TXT output path (default: {DEFAULT_TXT_OUTPUT.name})",
    )

    args = parser.parse_args()

    evaluate_matrix(
        dataset_limit=max(0, args.dataset_limit),
        profiles_raw=args.profiles,
        backends_raw=args.backends,
        models_raw=args.models,
        ollama_models_raw=args.ollama_models,
        lm_studio_models_raw=args.lm_studio_models,
        workers_raw=args.workers,
        stop_on_worse=not args.no_early_stop,
        output_json=args.output_json,
        output_txt=args.output_txt,
    )
