"""
evaluate_lm_studio_workers.py

LM Studio backend icin, config dosyasindaki varsayilan model ile
ilk 200 dataset kaydi uzerinde farkli worker sayilarini karsilastirir.

Calisma:
  py -3.14 tests/evaluate_lm_studio_workers.py
  py -3.14 tests/evaluate_lm_studio_workers.py --workers 1,2,4,8
"""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Add 'src' to sys.path so we can import 'mailshift'
root_dir = Path(__file__).parent.parent.absolute()
src_path = str(root_dir / "src")
if src_path not in sys.path:
    sys.path.insert(0, src_path)

from mailshift.config.config import LMStudioConfig
from mailshift.core.analyzers.fast import extract_fast_category, fast_analyze
from mailshift.core.analyzers.pro import check_lm_studio_health, pro_analyze
from mailshift.models.models import MailMeta, ScanResult
from mailshift.ui.styles import console
from mailshift.utils.hardware import calculate_optimal_workers

DATASET_LIMIT = 300
RESULTS_FILE = Path(__file__).parent / "eval_lm_studio_workers_results.txt"


def _parse_workers(raw: str) -> list[int]:
    workers = []
    for token in (raw or "").split(","):
        token = token.strip()
        if token.isdigit() and int(token) > 0:
            workers.append(int(token))
    unique_sorted = sorted(set(workers))
    if not unique_sorted:
        raise ValueError("--workers degeri gecersiz. Ornek: --workers 1,2,4,8")
    return unique_sorted


def _default_workers(model_name: str) -> list[int]:
    auto_workers = calculate_optimal_workers(model_name, mode="pro", backend="lm_studio")
    candidates = {1, 2, 4, auto_workers}
    if auto_workers > 2:
        candidates.add(max(1, auto_workers // 2))
    
    # Genişletilmiş worker listesi (daha fazla paralel test imkanı için)
    candidates.update({8, 12, 16, 24, 32})
    
    # auto_workers'ın bir üstünü de ekleyelim
    candidates.add(auto_workers + 1)
    
    return sorted(candidates)


def _evaluate_item(item: dict, cfg: LMStudioConfig) -> dict:
    meta = MailMeta(
        uid=item["id"],
        subject=item["subject"],
        sender=item["sender"],
        body_preview=item["body_preview"],
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
            backend="lm_studio",
            fast_reason=fast_res.reason,
            fast_category=extract_fast_category(fast_res.reason),
        )
        ai_elapsed = time.perf_counter() - ai_start
        pro_res = ai_res

    expected = item["expected_decision"]
    return {
        "expected": expected,
        "fast": fast_res.decision,
        "pro": pro_res.decision,
        "run_ai": run_ai,
        "ai_latency": ai_elapsed,
        "ai": ai_res.decision,
    }


def _compute_metrics(results: list[dict], elapsed_s: float) -> dict:
    total = len(results)
    fast_correct = sum(1 for r in results if r["fast"] == r["expected"])
    pro_correct = sum(1 for r in results if r["pro"] == r["expected"])

    tp = sum(1 for r in results if r["expected"] == "SIL" and r["pro"] == "SIL")
    fp = sum(1 for r in results if r["expected"] == "TUT" and r["pro"] == "SIL")
    fn = sum(1 for r in results if r["expected"] == "SIL" and r["pro"] == "TUT")

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    ai_calls = sum(1 for r in results if r["run_ai"])
    latencies = [r["ai_latency"] for r in results if r["run_ai"]]
    avg_ai_latency = sum(latencies) / len(latencies) if latencies else 0.0

    if latencies:
        sorted_lat = sorted(latencies)
        p95_idx = int(len(sorted_lat) * 0.95)
        p95_ai_latency = sorted_lat[min(p95_idx, len(sorted_lat) - 1)]
    else:
        p95_ai_latency = 0.0

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
        "elapsed_s": elapsed_s,
        "throughput": throughput,
    }


def _run_single(worker_count: int, dataset: list[dict], cfg: LMStudioConfig) -> dict:
    started = time.perf_counter()
    results = []

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(_evaluate_item, item, cfg) for item in dataset]
        for future in as_completed(futures):
            results.append(future.result())

    elapsed_s = time.perf_counter() - started
    metrics = _compute_metrics(results, elapsed_s)
    metrics["workers"] = worker_count
    return metrics


def _pick_best(runs: list[dict]) -> dict:
    # 1) En yuksek Pro accuracy
    # 2) Esitlikte en yuksek F1
    # 3) Esitlikte en dusuk toplam sure
    return sorted(
        runs,
        key=lambda r: (r["pro_accuracy"], r["f1"], -r["elapsed_s"]),
        reverse=True,
    )[0]


def _is_worse_than_previous(previous: dict, current: dict) -> bool:
    """Return True if current run is worse than previous run.

    Comparison priority:
    1) Lower pro_accuracy is worse
    2) If equal, lower F1 is worse
    3) If equal, higher elapsed time is worse
    """
    if current["pro_accuracy"] < previous["pro_accuracy"]:
        return True
    if current["pro_accuracy"] > previous["pro_accuracy"]:
        return False

    if current["f1"] < previous["f1"]:
        return True
    if current["f1"] > previous["f1"]:
        return False

    return current["elapsed_s"] > previous["elapsed_s"]


def evaluate(worker_list: list[int] | None = None) -> None:
    dataset_path = Path(__file__).parent / "test_ai_dataset.json"
    if not dataset_path.exists():
        console.print(f"[bold red]Dataset not found at {dataset_path}[/bold red]")
        return

    with open(dataset_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)[:DATASET_LIMIT]

    cfg = LMStudioConfig()
    health_ok, health_msg = check_lm_studio_health(cfg.base_url, cfg.model)
    if not health_ok:
        console.print(f"[bold red]LM Studio check failed: {health_msg}[/bold red]")
        return

    workers_to_test = worker_list or _default_workers(cfg.model)

    console.print("=" * 72)
    console.print("[bold white]LM STUDIO WORKER BENCHMARK (FIRST 200 EMAILS)[/bold white]")
    console.print("=" * 72)
    console.print(f"Model (config default): [green]{cfg.model or '(empty)'}[/green]")
    console.print(f"Backend: [green]LM Studio[/green]")
    console.print(f"Dataset size: [green]{len(dataset)}[/green]")
    console.print(f"Workers to test: [green]{', '.join(str(w) for w in workers_to_test)}[/green]")
    console.print(f"[dim]{health_msg}[/dim]\n")

    all_runs = []
    stopped_early = False
    for workers in workers_to_test:
        console.print(f"[bold cyan]Running with workers={workers}...[/bold cyan]")
        run_metrics = _run_single(workers, dataset, cfg)
        all_runs.append(run_metrics)
        console.print(
            f"  Pro Acc: [yellow]{run_metrics['pro_accuracy']:.2%}[/yellow] | "
            f"F1: {run_metrics['f1']:.4f} | "
            f"Time: {run_metrics['elapsed_s']:.2f}s | "
            f"Throughput: {run_metrics['throughput']:.2f} mail/s"
        )

        if len(all_runs) >= 2:
            prev = all_runs[-2]
            curr = all_runs[-1]
            if _is_worse_than_previous(prev, curr):
                console.print(
                    "[bold yellow]Early stop:[/bold yellow] "
                    f"workers={curr['workers']} sonucu, "
                    f"workers={prev['workers']} sonucundan daha kotu."
                )
                stopped_early = True
                break

    best = _pick_best(all_runs)

    console.print("\n" + "-" * 72)
    console.print("[bold white]Comparison Summary[/bold white]")
    for run in sorted(all_runs, key=lambda r: r["workers"]):
        winner_tag = " <= BEST" if run["workers"] == best["workers"] else ""
        console.print(
            f"workers={run['workers']:<3} | "
            f"pro={run['pro_accuracy']:.2%} | "
            f"f1={run['f1']:.4f} | "
            f"time={run['elapsed_s']:.2f}s | "
            f"avg_ai={run['avg_ai_latency']:.2f}s | "
            f"p95_ai={run['p95_ai_latency']:.2f}s"
            f"{winner_tag}"
        )

    console.print("-" * 72)
    console.print(
        f"[bold green]Best worker count: {best['workers']}[/bold green] "
        f"(pro={best['pro_accuracy']:.2%}, f1={best['f1']:.4f}, time={best['elapsed_s']:.2f}s)"
    )

    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        f.write("LM STUDIO WORKER BENCHMARK (FIRST 200 EMAILS)\n")
        f.write("=" * 72 + "\n")
        f.write(f"Model (config default): {cfg.model or '(empty)'}\n")
        f.write(f"Dataset size: {len(dataset)}\n")
        f.write(f"Workers tested: {', '.join(str(w) for w in workers_to_test)}\n\n")
        if stopped_early:
            f.write("EARLY_STOP: current worker result was worse than previous test.\n\n")
        for run in sorted(all_runs, key=lambda r: r["workers"]):
            f.write(
                f"workers={run['workers']} | "
                f"pro={run['pro_accuracy']:.2%} | "
                f"f1={run['f1']:.4f} | "
                f"time={run['elapsed_s']:.2f}s | "
                f"throughput={run['throughput']:.2f}/s | "
                f"avg_ai={run['avg_ai_latency']:.2f}s | "
                f"p95_ai={run['p95_ai_latency']:.2f}s\n"
            )
        f.write("\n")
        f.write(
            f"BEST={best['workers']} | "
            f"pro={best['pro_accuracy']:.2%} | "
            f"f1={best['f1']:.4f} | "
            f"time={best['elapsed_s']:.2f}s\n"
        )

    console.print(f"\n[dim]Report saved to {RESULTS_FILE}[/dim]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LM Studio worker benchmark (first 200 emails)")
    parser.add_argument(
        "--workers",
        type=str,
        default="",
        help="Comma-separated worker list. Example: --workers 1,2,4,8",
    )
    args = parser.parse_args()

    parsed_workers = _parse_workers(args.workers) if args.workers.strip() else None
    evaluate(worker_list=parsed_workers)
