import json
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from ui import console
from models import MailMeta, ScanResult
from config import OllamaConfig
from fast_analyzer import fast_analyze
from pro_analyzer import pro_analyze, check_ollama_health

NUM_WORKERS = 8

def _evaluate_item(item, cfg):
    meta = MailMeta(
        uid=item["id"],
        subject=item["subject"],
        sender=item["sender"],
        body_preview=item["body_preview"]
    )
    
    # 1. Fast Analysis
    fast_res = fast_analyze(meta)
    
    # 2. AI Analysis (isolated)
    ai_res = pro_analyze(meta, cfg)
    
    # 3. Full Pro Mode logic: Fast first, then AI if Fast is SIL
    # This mimics the engine.py logic:
    # res = fast_analyze(meta)
    # if need_llm and res.decision == "SIL":
    #     res = pro_analyze(meta, self.cfg.ollama)
    
    pro_mode_res = fast_res
    run_ai = False
    if fast_res.decision == "SIL":
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
        "pro": pro_mode_res.decision,
        "pro_reason": pro_mode_res.reason,
        "run_ai": run_ai
    }

def evaluate():
    dataset_path = Path(__file__).parent / "test_ai_dataset.json"
    if not dataset_path.exists():
        console.print(f"[bold red]Dataset not found at {dataset_path}[/bold red]")
        return
        
    with open(dataset_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    # Check Ollama health
    cfg = OllamaConfig()
    is_ok, msg = check_ollama_health(cfg.base_url, cfg.model)
    if not is_ok:
        console.print(f"[bold red]Ollama check failed: {msg}[/bold red]")
        return
        
    console.print(f"[bold cyan]Starting Full Pro Mode Simulation on {len(dataset)} messages...[/bold cyan]")
    console.print(f"Model: [green]{cfg.model}[/green] | Workers: [green]{NUM_WORKERS}[/green]\n")
    
    results = []
    start_time = time.time()
    
    with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
        futures = {executor.submit(_evaluate_item, item, cfg): item for item in dataset}
        for future in as_completed(futures):
            results.append(future.result())
            
    end_time = time.time()
    
    # Metrics
    total = len(dataset)
    fast_correct = sum(1 for r in results if r["fast"] == r["expected"])
    ai_correct = sum(1 for r in results if r["ai"] == r["expected"])
    pro_correct = sum(1 for r in results if r["pro"] == r["expected"])
    
    ai_calls = sum(1 for r in results if r["run_ai"])
    ai_rescued = sum(1 for r in results if r["run_ai"] and r["fast"] == "SIL" and r["pro"] == "TUT" and r["expected"] == "TUT")
    ai_failed_rescue = sum(1 for r in results if r["run_ai"] and r["fast"] == "SIL" and r["pro"] == "SIL" and r["expected"] == "TUT")
    ai_broke_correct_sil = sum(1 for r in results if r["run_ai"] and r["fast"] == "SIL" and r["pro"] == "TUT" and r["expected"] == "SIL")

    # Reporting
    console.print("="*60)
    console.print(f"[bold white]FULL PRO MODE PERFORMANCE REPORT[/bold white]")
    console.print("="*60)
    console.print(f"Total Emails Searched: {total}")
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
    console.print(f"Total Time:           {end_time - start_time:.2f}s")
    console.print("=" * 60)

    # Detailed failures for Pro Mode
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
            else:
                console.print(f"      AI was:    SKIPPED (Fast Mode kept it)")
            console.print("")

    # Save report
    # Strip rich tags for file
    import re
    def clean(text): return re.sub(r'\[.*?\]', '', text)
    
    with open(Path(__file__).parent / "eval_pro_mode_results.txt", "w", encoding="utf-8") as f:
        f.write("FULL PRO MODE PERFORMANCE REPORT\n")
        f.write("="*30 + "\n")
        f.write(f"Fast Mode: {fast_correct/total:.2%}\n")
        f.write(f"Pure AI:   {ai_correct/total:.2%}\n")
        f.write(f"PRO MODE:  {pro_correct/total:.2%}\n\n")
        
        for r in sorted(results, key=lambda x: int(x["item"]["id"])):
            status = "✅" if r["pro"] == r["expected"] else "❌"
            f.write(f"{status} ID: {r['item']['id']:<2} | Exp: {r['expected']} | Pro: {r['pro']} | Fast: {r['fast']} | run_ai: {r['run_ai']}\n")
            f.write(f"   Subj: {r['item']['subject']}\n")
            f.write(f"   Fast Reason: {r['fast_reason']}\n")
            if r['run_ai']:
                f.write(f"   AI Reason:   {r['ai_reason']}\n")
            f.write("-" * 20 + "\n")

if __name__ == "__main__":
    evaluate()
