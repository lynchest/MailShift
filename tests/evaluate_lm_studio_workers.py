"""
evaluate_lm_studio_workers.py

LM Studio benchmark wrapper built on top of the shared benchmark matrix helper.

Runs benchmark sweeps over at least three profiles by default (low/medium/high)
and writes both JSON and TXT outputs.

Usage:
  py -3.14 tests/evaluate_lm_studio_workers.py
  py -3.14 tests/evaluate_lm_studio_workers.py --workers 1,2,4,8
  py -3.14 tests/evaluate_lm_studio_workers.py --profiles low,medium,high,host
  py -3.14 tests/evaluate_lm_studio_workers.py --model gemma-4-26b-a4b
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

from mailshift.config.config import LMStudioConfig
from mailshift.ui.styles import console

from benchmark_matrix_helpers import (
    DATASET_DEFAULT_LIMIT,
    DEFAULT_PROFILE_NAMES,
    build_runtime_config,
    check_backend_health,
    load_dataset,
    parse_csv_tokens,
    parse_positive_int_csv,
    resolve_profiles,
    run_worker_matrix_benchmark,
    save_matrix_results,
)


DATASET_PATH = Path(__file__).parent / "test_ai_dataset.json"
RESULTS_TXT_FILE = Path(__file__).parent / "eval_lm_studio_workers_results.txt"
RESULTS_JSON_FILE = Path(__file__).parent / "eval_lm_studio_workers_results.json"


def _resolve_workers(raw_workers: str) -> list[int] | None:
    if not raw_workers.strip():
        return None

    workers = parse_positive_int_csv(raw_workers)
    if not workers:
        raise ValueError("--workers degeri gecersiz. Ornek: --workers 1,2,4,8")

    return workers


def evaluate(
    worker_list: list[int] | None = None,
    profiles_raw: str = ",".join(DEFAULT_PROFILE_NAMES),
    model_name: str = "",
    dataset_limit: int = DATASET_DEFAULT_LIMIT,
    stop_on_worse: bool = True,
    output_txt: Path = RESULTS_TXT_FILE,
    output_json: Path = RESULTS_JSON_FILE,
) -> None:
    dataset = load_dataset(DATASET_PATH, limit=dataset_limit)
    profiles = resolve_profiles(parse_csv_tokens(profiles_raw) or DEFAULT_PROFILE_NAMES)

    cfg = build_runtime_config("lm_studio", model_name)
    health_ok, health_msg = check_backend_health("lm_studio", cfg)
    if not health_ok:
        console.print(f"[bold red]LM Studio check failed: {health_msg}[/bold red]")
        return

    console.print("=" * 80)
    console.print("[bold white]LM STUDIO WORKER MATRIX BENCHMARK[/bold white]")
    console.print("=" * 80)
    console.print(f"Model: [green]{cfg.model or '(empty)'}[/green]")
    console.print(f"Profiles: [green]{', '.join(profile.name for profile in profiles)}[/green]")
    console.print(f"Dataset size: [green]{len(dataset)}[/green]")
    if worker_list:
        console.print(f"Workers override: [green]{', '.join(str(w) for w in worker_list)}[/green]")
    else:
        console.print("Workers override: [dim]auto per selected profile[/dim]")
    console.print(f"[dim]{health_msg}[/dim]\n")

    matrix_result = run_worker_matrix_benchmark(
        dataset=dataset,
        profiles=profiles,
        backend_models=[("lm_studio", cfg.model)],
        workers_override=worker_list,
        stop_on_worse=stop_on_worse,
    )

    report = save_matrix_results(
        matrix_result,
        output_json=output_json,
        output_txt=output_txt,
    )

    for case in matrix_result["cases"]:
        if not case["health_ok"]:
            console.print(
                f"[yellow]SKIP[/yellow] profile={case['profile']} -> {case['health_msg']}"
            )
            continue

        best = case["best"]
        console.print(
            f"profile={case['profile']:<6} | best_worker={best['workers']:<2} | "
            f"pro={best['pro_accuracy']:.2%} | f1={best['f1']:.4f} | "
            f"throughput={best['throughput']:.2f}/s"
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
    parser = argparse.ArgumentParser(description="LM Studio worker benchmark matrix")
    parser.add_argument(
        "--workers",
        type=str,
        default="",
        help="Comma-separated worker list. Example: --workers 1,2,4,8",
    )
    parser.add_argument(
        "--profiles",
        type=str,
        default=",".join(DEFAULT_PROFILE_NAMES),
        help="Comma-separated profile list: low,medium,high,host",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=LMStudioConfig().model,
        help="LM Studio model override",
    )
    parser.add_argument(
        "--dataset-limit",
        type=int,
        default=DATASET_DEFAULT_LIMIT,
        help="Dataset size limit",
    )
    parser.add_argument(
        "--no-early-stop",
        action="store_true",
        help="Disable early-stop when current worker run is worse than previous",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=RESULTS_JSON_FILE,
        help=f"JSON output path (default: {RESULTS_JSON_FILE.name})",
    )
    parser.add_argument(
        "--output-txt",
        type=Path,
        default=RESULTS_TXT_FILE,
        help=f"TXT output path (default: {RESULTS_TXT_FILE.name})",
    )

    args = parser.parse_args()
    parsed_workers = _resolve_workers(args.workers)

    evaluate(
        worker_list=parsed_workers,
        profiles_raw=args.profiles,
        model_name=args.model,
        dataset_limit=max(0, args.dataset_limit),
        stop_on_worse=not args.no_early_stop,
        output_txt=args.output_txt,
        output_json=args.output_json,
    )
