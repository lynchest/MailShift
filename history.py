import csv
import json
from datetime import datetime
from pathlib import Path

from rich import box
from rich.panel import Panel
from rich.table import Table

from models import ScanResult, ScanStats
from ui import console

def save_cleanup_log(
    deleted_results: list[ScanResult],
    stats: ScanStats,
    provider: str,
    mode: str,
) -> str:
    """Save cleanup results to a log file. Returns the log file path."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = f"cleanup_log_{timestamp}.json"
    
    log_data = {
        "timestamp": datetime.now().isoformat(),
        "provider": provider,
        "mode": mode,
        "stats": {
            "total_scanned": stats.total_scanned,
            "marked_for_deletion": stats.marked_for_deletion,
            "space_saved_mb": round(stats.space_saved_mb, 2),
            "deleted_count": len(deleted_results),
        },
        "deleted_messages": [
            {
                "uid": r.mail.uid,
                "sender": r.mail.sender,
                "subject": r.mail.subject,
                "date": r.mail.date,
                "size_bytes": r.mail.size_bytes,
                "reason": r.reason,
            }
            for r in deleted_results
        ],
    }
    
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    log_path = logs_dir / log_filename
    
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(log_data, f, ensure_ascii=False, indent=2)
    
    return str(log_path)


def export_scan_results(results: list[ScanResult], output_path: str) -> None:
    """Export scan results to CSV or JSON file."""
    path = Path(output_path)
    suffix = path.suffix.lower()
    
    export_data = [
        {
            "uid": r.mail.uid,
            "sender": r.mail.sender,
            "subject": r.mail.subject,
            "date": r.mail.date,
            "size_bytes": r.mail.size_bytes,
            "decision": r.decision,
            "reason": r.reason,
        }
        for r in results
    ]
    
    if suffix == ".csv":
        if not export_data:
            console.print("[yellow]No results to export.[/yellow]")
            return
        
        fieldnames = ["uid", "sender", "subject", "date", "size_bytes", "decision", "reason"]
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(export_data)
        
        console.print(f"[green]Exported {len(export_data)} results to {output_path}[/green]")
    
    elif suffix == ".json":
        with open(path, "w", encoding="utf-8") as f:
            json.dump(export_data, f, ensure_ascii=False, indent=2)
        
        console.print(f"[green]Exported {len(export_data)} results to {output_path}[/green]")
    else:
        console.print(f"[red]Unsupported file format: {suffix}. Use .csv or .json[/red]")


def print_history() -> None:
    """Display cleanup history from all log files."""
    logs_dir = Path("logs")
    if not logs_dir.exists():
        log_files = []
    else:
        log_files = sorted(logs_dir.glob("cleanup_log_*.json"), reverse=True)

    if not log_files:
        console.print(Panel(
            "[yellow]Henüz temizlik geçmişi bulunamadı.[/yellow]",
            title="[bold cyan]Cleanup History[/bold cyan]",
            border_style="cyan",
            box=box.ROUNDED,
        ))
        return

    console.print(Panel(
        f"[bold]{len(log_files)}[/bold] temizlik oturumu bulundu.",
        title="[bold cyan]Cleanup History[/bold cyan]",
        border_style="cyan",
        box=box.ROUNDED,
    ))

    for log_file in log_files:
        try:
            with open(log_file, encoding="utf-8") as f:
                data = json.load(f)

            timestamp = data.get("timestamp", "unknown")
            provider = data.get("provider", "unknown")
            mode = data.get("mode", "unknown")
            stats = data.get("stats", {})
            deleted_msgs = data.get("deleted_messages", [])

            header = (
                f"[bold]Provider:[/bold] {provider}  [bold]Mode:[/bold] {mode}\n"
                f"[bold]Silinen:[/bold] {stats.get('deleted_count', 0)}  "
                f"[bold]Kazanılan alan:[/bold] {stats.get('space_saved_mb', 0):.2f} MB"
            )

            if deleted_msgs:
                table = Table(box=box.SIMPLE_HEAVY, show_lines=False, padding=(0, 1))
                table.add_column("#", style="dim", width=3)
                table.add_column("From", style="cyan", max_width=30)
                table.add_column("Subject", style="white", max_width=40)
                table.add_column("Reason", style="magenta", max_width=15)

                for idx, msg in enumerate(deleted_msgs[:10], start=1):
                    table.add_row(
                        str(idx),
                        msg.get("sender", "")[:30],
                        msg.get("subject", "")[:40],
                        msg.get("reason", ""),
                    )

                from rich.console import Group
                body = Group(header, table)
                if len(deleted_msgs) > 10:
                    from rich.text import Text
                    body = Group(header, table, Text(f"  … ve {len(deleted_msgs) - 10} mesaj daha", style="dim"))
            else:
                body = header

            console.print(Panel(
                body,
                title=f"[dim]{timestamp}[/dim]",
                border_style="blue",
                box=box.ROUNDED,
            ))
        except Exception as e:
            console.print(f"[red]Hata – {log_file.name}: {e}[/red]")
