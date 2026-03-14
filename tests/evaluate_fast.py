import json
import time
from pathlib import Path

from ui import console
from models import MailMeta
from fast_analyzer import fast_analyze

def _evaluate_item(item):
    meta = MailMeta(
        uid=item["id"],
        subject=item["subject"],
        sender=item["sender"],
        body_preview=item["body_preview"]
    )
    
    res = fast_analyze(meta)
    
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

    console.print(f"[bold cyan]Starting Fast Mode Evaluation on {len(dataset)} messages...[/bold cyan]\n")
    
    correct_count = 0
    total_count = len(dataset)
    failures = []
    
    report = []

    start_time = time.time()
    
    for item in dataset:
        result = _evaluate_item(item)
        item = result["item"]
        expected = result["expected"]
        actual = result["actual"]
        
        if result["correct"]:
            correct_count += 1
            msg = f"✅ PASS | ID: {item['id']:<2} | Expected: {expected} | Actual: {actual} | Reason: {result['reason']}"
            console.print(msg)
            report.append(msg)
        else:
            failures.append({
                "item": item,
                "actual": actual,
                "reason": result["reason"]
            })
            msg = f"❌ FAIL | ID: {item['id']:<2} | Expected: {expected} | Actual: {actual} | Reason: {result['reason']}"
            console.print(msg)
            report.append(msg)
            
    end_time = time.time()
    
    accuracy = (correct_count / total_count) * 100
    
    report.append("\n" + "="*50)
    report.append("[bold]FAST EVALUATION RESULTS[/bold]")
    report.append("="*50)
    report.append(f"Total Messages: {total_count}")
    report.append(f"Correct: {correct_count}")
    report.append(f"Incorrect: {len(failures)}")
    report.append(f"Accuracy: {accuracy:.2f}%")
    report.append(f"Time Taken: {end_time - start_time:.4f} seconds")
    
    if failures:
        report.append("\n[bold red]Failed Cases Analysis:[/bold red]")
        for f in failures:
            item = f["item"]
            report.append(f"- ID: {item['id']} | Subject: '{item['subject']}'")
            report.append(f"  Category: {item['category']}, Expected: {item['expected_decision']}, Actual: {f['actual']}")
            report.append(f"  Reason: {f['reason']}")

    # print to console
    for line in report:
        console.print(line)
        
    # Write full log to file
    import re
    with open(Path(__file__).parent / "eval_fast_results.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(re.sub(r'\[.*?\]', '', line) for line in report))

if __name__ == "__main__":
    evaluate()
