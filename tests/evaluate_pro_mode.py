"""
evaluate_pro_mode.py – Full Pro Mode evaluation harness.

Supports:
  --backend ollama|lm_studio   (default: ollama)

Reports:
  - Fast / Pure-AI / Pro Mode accuracy
  - Confusion matrix (TP / FP / TN / FN)
  - Per-email AI latency statistics
  - AI rescue / broke / missed metrics
  - Regression diff against previous run
"""

import argparse
import json
import os
import time
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Add 'src' to sys.path so we can import 'mailshift'
src_path = str(Path(__file__).parent.parent.absolute() / "src")
if src_path not in sys.path:
    sys.path.insert(0, src_path)

from mailshift.ui.styles import console
from mailshift.models.models import MailMeta, ScanResult
from mailshift.config.config import OllamaConfig, LMStudioConfig
from mailshift.core.analyzers.fast import fast_analyze
from mailshift.core.analyzers.fast import extract_fast_category
from mailshift.core.analyzers.pro import pro_analyze, check_ollama_health, check_lm_studio_health
from mailshift.utils.hardware import calculate_optimal_workers

RESULTS_FILE = Path(__file__).parent / "eval_pro_mode_results.txt"


def _resolve_workers(backend: str, cfg, cli_workers: int | None = None) -> int:
    if cli_workers is not None and cli_workers > 0:
        return cli_workers

    if backend == "ollama":
        env_parallel = os.getenv("OLLAMA_NUM_PARALLEL", "").strip()
        if env_parallel.isdigit() and int(env_parallel) > 0:
            return int(env_parallel)
            
    return calculate_optimal_workers(cfg.model, mode="pro", backend=backend)


def _evaluate_item(item, cfg, backend):
    meta = MailMeta(
        uid=item["id"],
        subject=item["subject"],
        sender=item["sender"],
        body_preview=item["body_preview"]
    )

    # 1. Fast Analysis
    fast_res = fast_analyze(meta)

    # 2. Full Pro Mode logic: Fast first, then AI if Fast is SIL
    # Only call AI for emails that fast mode marked as SIL (real scenario behavior)
    pro_mode_res = fast_res
    run_ai = False
    ai_res = ScanResult(mail=meta, decision="TUT", reason="")
    ai_elapsed = 0.0

    if fast_res.decision == "SIL":
        ai_start = time.perf_counter()
        ai_res = pro_analyze(
            meta, cfg,
            backend=backend,
            fast_reason=fast_res.reason if fast_res.decision == "SIL" else "",
            fast_category=extract_fast_category(fast_res.reason) if fast_res.decision == "SIL" else "",
        )
        ai_elapsed = time.perf_counter() - ai_start
        pro_mode_res = ai_res
        run_ai = True

    expected = item["expected_decision"]

    return {
        "item": item,
        "expected": expected,
        "fast": fast_res.decision,
        "fast_reason": fast_res.reason,
        "ai": ai_res.decision,
        "ai_reason": ai_res.reason,
        "ai_latency": ai_elapsed,
        "pro": pro_mode_res.decision,
        "pro_reason": pro_mode_res.reason,
        "run_ai": run_ai,
    }


def _confusion_matrix(results):
    """Compute TP/FP/TN/FN for Pro Mode (positive class = SIL)."""
    tp = fp = tn = fn = 0
    for r in results:
        if r["expected"] == "SIL" and r["pro"] == "SIL":
            tp += 1
        elif r["expected"] == "TUT" and r["pro"] == "SIL":
            fp += 1
        elif r["expected"] == "TUT" and r["pro"] == "TUT":
            tn += 1
        elif r["expected"] == "SIL" and r["pro"] == "TUT":
            fn += 1
    return tp, fp, tn, fn


def _precision_recall_f1(tp, fp, fn):
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1


def _load_previous_results(path):
    """Load previous run results for regression diff."""
    if not path.exists():
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    prev = {}
    for line in lines:
        if line.startswith(("✅", "❌")):
            parts = line.split("|")
            uid = None
            decision = None
            for p in parts:
                p = p.strip()
                if p.startswith("ID:"):
                    uid = p.split(":")[1].strip()
                elif p.startswith("Pro:"):
                    decision = p.split(":")[1].strip()
            if uid and decision:
                prev[uid] = decision
    return prev if prev else None


def _latency_stats(results):
    """Per-email AI latency statistics."""
    latencies = [r["ai_latency"] for r in results]
    if not latencies:
        return 0.0, 0.0, 0.0, 0.0
    avg = sum(latencies) / len(latencies)
    mn = min(latencies)
    mx = max(latencies)
    # p95
    sorted_l = sorted(latencies)
    p95_idx = int(len(sorted_l) * 0.95)
    p95 = sorted_l[min(p95_idx, len(sorted_l) - 1)]
    return avg, mn, mx, p95


def evaluate(backend="ollama", workers: int | None = None):
    dataset_path = Path(__file__).parent / "test_ai_dataset.json"
    if not dataset_path.exists():
        console.print(f"[bold red]Dataset not found at {dataset_path}[/bold red]")
        return

    with open(dataset_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    # Health check based on backend
    if backend == "lm_studio":
        cfg = LMStudioConfig()
        is_ok, msg = check_lm_studio_health(cfg.base_url, cfg.model)
        backend_label = "LM Studio"
    else:
        cfg = OllamaConfig()
        is_ok, msg = check_ollama_health(cfg.base_url, cfg.model)
        backend_label = "Ollama"

    if not is_ok:
        console.print(f"[bold red]{backend_label} check failed: {msg}[/bold red]")
        return

    num_workers = _resolve_workers(backend, cfg, workers)

    console.print(f"[bold cyan]Starting Full Pro Mode Simulation on {len(dataset)} messages...[/bold cyan]")
    console.print(f"Backend: [green]{backend_label}[/green] | Model: [green]{cfg.model}[/green] | Workers: [green]{num_workers}[/green]\n")

    # Load previous results for regression diff
    prev_results = _load_previous_results(RESULTS_FILE)

    results = []
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {executor.submit(_evaluate_item, item, cfg, backend): item for item in dataset}
        for future in as_completed(futures):
            results.append(future.result())

    end_time = time.time()
    total_time = end_time - start_time

    # ── Metrics ──────────────────────────────────────────────────────────
    total = len(dataset)
    fast_correct = sum(1 for r in results if r["fast"] == r["expected"])
    ai_correct = sum(1 for r in results if r["ai"] == r["expected"])
    pro_correct = sum(1 for r in results if r["pro"] == r["expected"])

    ai_calls = sum(1 for r in results if r["run_ai"])
    ai_rescued = sum(1 for r in results if r["run_ai"] and r["fast"] == "SIL" and r["pro"] == "TUT" and r["expected"] == "TUT")
    ai_failed_rescue = sum(1 for r in results if r["run_ai"] and r["fast"] == "SIL" and r["pro"] == "SIL" and r["expected"] == "TUT")
    ai_broke_correct_sil = sum(1 for r in results if r["run_ai"] and r["fast"] == "SIL" and r["pro"] == "TUT" and r["expected"] == "SIL")

    # Confusion matrix
    tp, fp, tn, fn = _confusion_matrix(results)
    precision, recall, f1 = _precision_recall_f1(tp, fp, fn)

    # Latency stats
    avg_lat, min_lat, max_lat, p95_lat = _latency_stats(results)

    # ── Console report ───────────────────────────────────────────────────
    console.print("=" * 60)
    console.print(f"[bold white]FULL PRO MODE PERFORMANCE REPORT[/bold white]")
    console.print("=" * 60)
    console.print(f"Backend:              {backend_label}")
    console.print(f"Model:                {cfg.model}")
    console.print(f"Total Emails Tested:  {total}")
    console.print(f"Fast Mode Accuracy:   {fast_correct/total:>6.2%} ({fast_correct}/{total})")
    console.print(f"Pure AI Accuracy:     {ai_correct/total:>6.2%} ({ai_correct}/{total})")
    console.print(f"PRO MODE (Mixed):    [bold yellow]{pro_correct/total:>6.2%}[/bold yellow] ({pro_correct}/{total})")
    console.print("-" * 60)
    console.print(f"AI Calls avoided:     {total - ai_calls} (saved by Fast Mode TUT)")
    console.print(f"AI Calls made:        {ai_calls}")
    console.print(f"AI Rescued (FP->TUT): {ai_rescued} (Fast would have deleted unfairly)")
    console.print(f"AI Missed (FP->SIL):  {ai_failed_rescue} (Both Fast & AI wanted to delete)")
    console.print(f"AI Broke (SIL->TUT):  {ai_broke_correct_sil} (AI kept what should be deleted)")
    console.print("-" * 60)

    # Confusion matrix
    console.print("[bold white]Confusion Matrix (Pro Mode, positive=SIL):[/bold white]")
    console.print(f"  TP (SIL correct):   {tp}")
    console.print(f"  FP (SIL wrong):     {fp}")
    console.print(f"  TN (TUT correct):   {tn}")
    console.print(f"  FN (TUT wrong):     {fn}")
    console.print(f"  Precision:          {precision:.4f}")
    console.print(f"  Recall:             {recall:.4f}")
    console.print(f"  F1 Score:           {f1:.4f}")
    console.print("-" * 60)

    # Latency stats
    console.print("[bold white]AI Latency (per email):[/bold white]")
    console.print(f"  Avg:  {avg_lat:.2f}s")
    console.print(f"  Min:  {min_lat:.2f}s")
    console.print(f"  Max:  {max_lat:.2f}s")
    console.print(f"  P95:  {p95_lat:.2f}s")
    console.print("-" * 60)
    console.print(f"Total Time:           {total_time:.2f}s")
    console.print("=" * 60)

    # ── Detailed failures ────────────────────────────────────────────────
    failures = [r for r in results if r["pro"] != r["expected"]]
    if failures:
        console.print("\n[bold red]Pro Mode Mistakes:[/bold red]")
        for f in sorted(failures, key=lambda x: int(x["item"]["id"])):
            item = f["item"]
            console.print(f"ID: {item['id']:<2} | Subject: [dim]{item['subject'][:40]}[/dim]")
            console.print(f"      Expected: [bold]{f['expected']}[/bold], Pro Result: [bold red]{f['pro']}[/bold red]")
            console.print(f"      Fast said: {f['fast']} ({f['fast_reason']})")
            if f['run_ai']:
                console.print(f"      AI said:   {f['ai']} ({f['ai_reason']})")
                console.print(f"      AI time:   {f['ai_latency']:.2f}s")
            else:
                console.print(f"      AI was:    SKIPPED (Fast Mode kept it)")
            console.print("")

    # ── Regression diff ──────────────────────────────────────────────────
    if prev_results:
        regressions = []
        improvements = []
        for r in sorted(results, key=lambda x: int(x["item"]["id"])):
            uid = str(r["item"]["id"])
            if uid in prev_results:
                prev_dec = prev_results[uid]
                curr_ok = r["pro"] == r["expected"]
                prev_ok = prev_dec == r["expected"]
                if prev_ok and not curr_ok:
                    regressions.append(r)
                elif not prev_ok and curr_ok:
                    improvements.append(r)

        if regressions:
            console.print(f"\n[bold red]REGRESSIONS vs previous run: {len(regressions)}[/bold red]")
            for r in regressions:
                console.print(f"  ID {r['item']['id']}: was correct, now {r['pro']} (expected {r['expected']})")
        if improvements:
            console.print(f"\n[bold green]IMPROVEMENTS vs previous run: {len(improvements)}[/bold green]")
            for r in improvements:
                console.print(f"  ID {r['item']['id']}: was wrong, now correct ({r['pro']})")
        if not regressions and not improvements:
            console.print("\n[dim]No regressions or improvements vs previous run.[/dim]")

    # ── Save report to file ──────────────────────────────────────────────
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        f.write("FULL PRO MODE PERFORMANCE REPORT\n")
        f.write("=" * 50 + "\n")
        f.write(f"Backend:   {backend_label}\n")
        f.write(f"Model:     {cfg.model}\n")
        f.write(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Fast Mode: {fast_correct/total:.2%}\n")
        f.write(f"Pure AI:   {ai_correct/total:.2%}\n")
        f.write(f"PRO MODE:  {pro_correct/total:.2%}\n\n")

        f.write("Confusion Matrix (positive=SIL)\n")
        f.write(f"  TP: {tp}  FP: {fp}  TN: {tn}  FN: {fn}\n")
        f.write(f"  Precision: {precision:.4f}  Recall: {recall:.4f}  F1: {f1:.4f}\n\n")

        f.write("AI Latency (per email)\n")
        f.write(f"  Avg: {avg_lat:.2f}s  Min: {min_lat:.2f}s  Max: {max_lat:.2f}s  P95: {p95_lat:.2f}s\n\n")

        f.write(f"Total Time: {total_time:.2f}s\n")
        f.write("=" * 50 + "\n\n")

        for r in sorted(results, key=lambda x: int(x["item"]["id"])):
            status = "✅" if r["pro"] == r["expected"] else "❌"
            f.write(f"{status} ID: {r['item']['id']:<2} | Exp: {r['expected']} | Pro: {r['pro']} | Fast: {r['fast']} | run_ai: {r['run_ai']} | ai_latency: {r['ai_latency']:.2f}s\n")
            f.write(f"   Subj: {r['item']['subject']}\n")
            f.write(f"   Fast Reason: {r['fast_reason']}\n")
            if r['run_ai']:
                f.write(f"   AI Reason:   {r['ai_reason']}\n")
            f.write("-" * 20 + "\n")

    console.print(f"\n[dim]Report saved to {RESULTS_FILE}[/dim]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MailShift Pro Mode Evaluator")
    parser.add_argument(
        "--backend",
        choices=["ollama", "lm_studio"],
        default="ollama",
        help="LLM backend to use (default: ollama)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Parallel worker count override (default: backend-aware auto)",
    )
    args = parser.parse_args()
    evaluate(backend=args.backend, workers=args.workers)
