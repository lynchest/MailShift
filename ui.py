from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from models import ScanResult, ScanStats

console = Console()


def clear_console() -> None:
    """Terminale temizle komutu göndererek daha minimal bir yapı sunar."""
    console.clear()
    print_banner()


BANNER = (
    " __  __      _ _ ____  _  __ _   \n"
    r"|  \/  | __ _(_) / ___||(_)/ _| |_ " + "\n"
    r"| |\/| |/ _` | | \___ \| | | |_| __|" + "\n"
    r"| |  | | (_| | | |___) | | |  _| |_ " + "\n"
    r"|_|  |_|\__,_|_|_|____/|_|_|_|  \__|" + "\n"
)


def print_banner() -> None:
    console.print(
        Panel(
            Text(BANNER, style="bold cyan", justify="center"),
            subtitle="[dim]Privacy-first newsletter purger[/dim]",
            border_style="cyan",
            box=box.DOUBLE_EDGE,
        )
    )


def build_results_table(results: list[ScanResult]) -> Table:
    """Build a Rich table of messages marked for deletion."""
    table = Table(
        title="[bold red]Messages marked for deletion[/bold red]",
        box=box.ROUNDED,
        border_style="red",
        show_lines=True,
    )
    table.add_column("#", style="dim", width=4)
    table.add_column("From", style="cyan", max_width=35, no_wrap=True)
    table.add_column("Subject", style="white", max_width=50, no_wrap=True)
    table.add_column("Date", style="dim", max_width=22)
    table.add_column("Size", style="yellow", justify="right")
    table.add_column("Reason", style="magenta", max_width=20)

    for idx, result in enumerate(results, start=1):
        m = result.mail
        size_str = (
            f"{m.size_bytes / 1024:.1f} KB"
            if m.size_bytes < 1024 * 1024
            else f"{m.size_bytes / (1024*1024):.2f} MB"
        )
        table.add_row(
            str(idx),
            m.sender[:35] if m.sender else "[dim]unknown[/dim]",
            m.subject[:50] if m.subject else "[dim](no subject)[/dim]",
            m.date[:22] if m.date else "",
            size_str,
            result.reason,
        )
    return table


def build_stats_panel(stats: ScanStats, dry_run: bool) -> Panel:
    """Build a summary statistics panel."""
    mode_tag = "[yellow]DRY RUN[/yellow]" if dry_run else "[red]LIVE[/red]"
    t = Table(box=None, show_header=False, padding=(0, 1))
    t.add_column(style="bold", min_width=24)
    t.add_column()
    t.add_row("Total scanned:", str(stats.total_scanned))
    t.add_row("Marked for deletion:", str(stats.marked_for_deletion))
    t.add_row("Space saved:", f"{stats.space_saved_mb:.2f} MB")
    t.add_row("Errors:", str(len(stats.errors)))
    t.add_row("Mode:", mode_tag)
    return Panel(
        t,
        title="[bold green]Scan Statistics[/bold green]",
        border_style="green",
        box=box.ROUNDED,
    )
