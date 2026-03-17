import json
import time
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Add 'src' to sys.path so we can import 'mailshift'
root_dir = Path(__file__).parent.parent.absolute()
src_path = str(root_dir / "src")
if src_path not in sys.path:
    sys.path.insert(0, src_path)

from mailshift.ui.styles import console
from mailshift.models.models import MailMeta
from mailshift.config.config import OllamaConfig
from mailshift.core.analyzers.pro import pro_analyze, check_ollama_health

NUM_WORKERS = 4

def _evaluate_item(item, cfg):
    meta = MailMeta(
        uid=item["id"],
        subject=item["subject"],
        sender=item["sender"],
        body_preview=item["body_preview"]
    )
    
    res = pro_analyze(meta, cfg)
    
    expected = item["expected_decision"]
    actual = res.decision
    
    return {
        "item": item,
        "expected": expected,
        "actual": actual,
        "reason": res.reason,
        "correct": expected == actual
    }

def evaluate():
    dataset_path = Path(__file__).parent / "test_ai_dataset.json"
    if not dataset_path.exists():
        console.print(f"[bold red]Dataset not found at {dataset_path}[/bold red]")
        return
        
    with open(dataset_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    # Check Ollama health first
    cfg = OllamaConfig()
    is_ok, msg = check_ollama_health(cfg.base_url, cfg.model)
    if not is_ok:
        console.print(f"[bold red]Ollama check failed: {msg}[/bold red]")
        console.print(f"Please ensure Ollama is running and the model {cfg.model} is pulled.")
        return
        
    console.print(f"[bold green]{msg}[/bold green]")
    console.print(f"[bold cyan]Starting AI Evaluation on {len(dataset)} messages with {NUM_WORKERS} workers...[/bold cyan]\n")
    
    correct_count = 0
    total_count = len(dataset)
    failures = []
    
    report = []

    start_time = time.time()
    
    with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
        futures = {executor.submit(_evaluate_item, item, cfg): item for item in dataset}
        
        for future in as_completed(futures):
            result = future.result()
            item = result["item"]
            expected = result["expected"]
            actual = result["actual"]
            
            if result["correct"]:
                correct_count += 1
                msg = f"✅ PASS | ID: {item['id']:<2} | Expected: {expected} | Actual: {actual} | Reason: {result['reason']}"
                console.print(msg)
                report.append(msg)
            else:
                failures.append(item)
                msg = f"❌ FAIL | ID: {item['id']:<2} | Expected: {expected} | Actual: {actual} | Reason: {result['reason']}"
                console.print(msg)
                report.append(msg)
            
    end_time = time.time()
    
    accuracy = (correct_count / total_count) * 100
    
    report.append("\n" + "="*50)
    report.append("[bold]EVALUATION RESULTS[/bold]")
    report.append("="*50)
    report.append(f"Total Messages: {total_count}")
    report.append(f"Correct: {correct_count}")
    report.append(f"Incorrect: {len(failures)}")
    report.append(f"Accuracy: {accuracy:.2f}%")
    report.append(f"Time Taken: {end_time - start_time:.2f} seconds")
    
    if failures:
        report.append("\n[bold red]Failed Cases Analysis:[/bold red]")
        for f in failures:
            report.append(f"- Subject: '{f['subject']}'")
            report.append(f"  Category: {f['category']}, Expected: {f['expected_decision']}")
            
    # print to console
    for line in report:
        console.print(line)
        
    # Write full log to file (text only, strip rich tags for simplicity or just write)
    import re
    with open(Path(__file__).parent / "eval_results.txt", "w", encoding="utf-8") as f:
        # We need the full log including per-message details, 
        # so let's just write the summary for now to see what fails
        f.write("\n".join(re.sub(r'\[.*?\]', '', line) for line in report))

if __name__ == "__main__":
    evaluate()
