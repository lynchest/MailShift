"""
main.py – MailShift CLI entry point.

Usage examples
--------------
# Interactive (prompts for provider, mode, credentials):
    python main.py

# Non-interactive with all flags:
    python main.py --provider gmail --mode fast --username you@gmail.com \
        --password "app-password" --dry-run

# Actually delete (no dry-run):
    python main.py --provider gmail --mode pro --username you@gmail.com \
        --password "app-password" --no-dry-run
"""

from __future__ import annotations

import getpass
import sys
from typing import Optional

import click
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from config import AppConfig, Mode, OllamaConfig, Provider, build_imap_config
from engine import MailEngine, MailMeta, ScanResult, ScanStats

console = Console()

BANNER = (
    " __  __       _ _ ____  _  __ _   \n"
    r"|  \/  | __ _(_) / ___||(_)/ _| |_ " + "\n"
    r"| |\/| |/ _` | | \___ \| | | |_| __|" + "\n"
    r"| |  | | (_| | | |___) | | |  _| |_ " + "\n"
    r"|_|  |_|\__,_|_|_|____/|_|_|_|  \__|" + "\n"
)


# ---------------------------------------------------------------------------
# Rich helpers
# ---------------------------------------------------------------------------


def _print_banner() -> None:
    console.print(
        Panel(
            Text(BANNER, style="bold cyan", justify="center"),
            subtitle="[dim]Privacy-first newsletter purger[/dim]",
            border_style="cyan",
            box=box.DOUBLE_EDGE,
        )
    )


def _build_results_table(results: list[ScanResult]) -> Table:
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


def _build_stats_panel(stats: ScanStats, dry_run: bool) -> Panel:
    """Build a summary statistics panel."""
    mode_tag = "[yellow]DRY RUN[/yellow]" if dry_run else "[red]LIVE[/red]"
    content = (
        f"[bold]Total scanned:[/bold]          {stats.total_scanned}\n"
        f"[bold]Marked for deletion:[/bold]    {stats.marked_for_deletion}\n"
        f"[bold]Space saved:[/bold]            {stats.space_saved_mb:.2f} MB\n"
        f"[bold]Errors:[/bold]                 {len(stats.errors)}\n"
        f"[bold]Mode:[/bold]                   {mode_tag}"
    )
    return Panel(
        content,
        title="[bold green]Scan Statistics[/bold green]",
        border_style="green",
        box=box.ROUNDED,
    )


# ---------------------------------------------------------------------------
# Interactive credential prompts
# ---------------------------------------------------------------------------


def _prompt_provider() -> Provider:
    console.print("\n[bold cyan]Select mail provider:[/bold cyan]")
    console.print("  [1] Gmail  (IMAP over SSL, App Password)")
    console.print("  [2] Proton (via Proton Bridge at 127.0.0.1)")
    choice = Prompt.ask("Provider", choices=["1", "2"], default="1")
    return Provider.GMAIL if choice == "1" else Provider.PROTON


def _prompt_mode() -> Mode:
    console.print("\n[bold cyan]Select scan mode:[/bold cyan]")
    console.print("  [1] Fast – heuristic keyword matching")
    console.print("  [2] Pro  – heuristic + local Ollama LLM (requires Ollama running)")
    choice = Prompt.ask("Mode", choices=["1", "2"], default="1")
    return Mode.FAST if choice == "1" else Mode.PRO


def _prompt_credentials(provider: Provider) -> tuple[str, str]:
    label = "Gmail address" if provider == Provider.GMAIL else "Proton Bridge username"
    username = Prompt.ask(f"\n[bold cyan]{label}[/bold cyan]")
    
    if provider == Provider.GMAIL:
        console.print("\n[bold cyan]Şifre durumu:[/bold cyan]")
        console.print("  [1] Şifremi girdim / zaten var")
        console.print("  [2] Yönlendir (App Password oluştur)")
        choice = Prompt.ask("Seçim", choices=["1", "2"], default="1")
        
        if choice == "2":
            console.print("\n[bold yellow]Önce App Password oluşturmanız gerekiyor:[/bold yellow]")
            console.print("→ https://myaccount.google.com/apppasswords")
            console.print("[dim]Bu sayfada oturum açın, 'Uygulama şifresi oluştur' deyin,[/dim]")
            console.print("[dim]Mail'i seçip oluşturduğunuz 16 haneli şifreyi kullanın.[/dim]\n")
            Prompt.ask("[dim]Devam etmek için Enter'a basın[/dim]")
    
    password = getpass.getpass(
        "Password (App Password / Bridge password): "
    )
    return username, password


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--provider", type=click.Choice(["gmail", "proton"]), default=None, help="Mail provider.")
@click.option("--mode", type=click.Choice(["fast", "pro"]), default=None, help="Scan mode.")
@click.option("--username", default=None, help="IMAP username / email address.")
@click.option("--password", default=None, help="IMAP password (App Password, etc.).")
@click.option("--dry-run/--no-dry-run", default=True, show_default=True, help="Dry run (default: enabled).")
@click.option("--scan-limit", default=None, type=int, help="Max number of messages to scan.")
@click.option("--workers", default=8, show_default=True, help="Number of concurrent IMAP workers.")
@click.option("--ollama-url", default="http://localhost:11434", show_default=True, help="Ollama API base URL.")
@click.option("--ollama-model", default="qwen2.5:3b", show_default=True, help="Ollama model name.")
def main(
    provider: Optional[str],
    mode: Optional[str],
    username: Optional[str],
    password: Optional[str],
    dry_run: bool,
    scan_limit: Optional[int],
    workers: int,
    ollama_url: str,
    ollama_model: str,
) -> None:
    """MailShift – privacy-first newsletter purger for Gmail and Proton Mail."""

    _print_banner()

    # ---- resolve provider / mode (interactive if not provided) ----
    resolved_provider = Provider(provider) if provider else _prompt_provider()
    resolved_mode = Mode(mode) if mode else _prompt_mode()

    # ---- credentials ----
    if not username or not password:
        username, password = _prompt_credentials(resolved_provider)

    # ---- build config ----
    imap_cfg = build_imap_config(resolved_provider, username, password)
    ollama_cfg = OllamaConfig(base_url=ollama_url, model=ollama_model)
    cfg = AppConfig(
        provider=resolved_provider,
        mode=resolved_mode,
        imap=imap_cfg,
        ollama=ollama_cfg,
        dry_run=dry_run,
        max_workers=workers,
        scan_limit=scan_limit,
    )

    # ---- summary of what we're about to do ----
    console.print(
        Panel(
            f"[bold]Provider:[/bold] {resolved_provider.value.capitalize()}\n"
            f"[bold]Mode:[/bold]     {resolved_mode.value.capitalize()}\n"
            f"[bold]User:[/bold]     {username}\n"
            f"[bold]Dry run:[/bold]  {'[yellow]Yes – no emails will be deleted[/yellow]' if dry_run else '[red]No – emails WILL be deleted[/red]'}",
            title="[bold blue]Configuration[/bold blue]",
            border_style="blue",
            box=box.ROUNDED,
        )
    )

    # ---- connect ----
    with console.status("[cyan]Connecting to IMAP server…[/cyan]", spinner="dots"):
        try:
            engine = MailEngine(cfg)
            engine.connect()
        except Exception as exc:
            console.print(f"[bold red]Connection failed:[/bold red] {exc}")
            sys.exit(1)

    try:
        # ---- list UIDs ----
        with console.status("[cyan]Listing messages…[/cyan]", spinner="dots"):
            uids = engine.list_uids()

        if not uids:
            console.print("[yellow]No messages found in INBOX.[/yellow]")
            return

        console.print(f"[green]Found [bold]{len(uids)}[/bold] message(s) to scan.[/green]")

        # ---- fetch headers with progress bar ----
        mails: list[MailMeta] = []

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]Fetching[/bold cyan]"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("[dim]{task.fields[current]}[/dim]"),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task(
                "fetch", total=len(uids), current="Starting…"
            )

            def _on_fetch(meta: MailMeta) -> None:
                mails.append(meta)
                progress.update(
                    task,
                    advance=1,
                    current=f"{meta.sender[:30]}" if meta.sender else "(unknown sender)",
                )

            engine.fetch_headers_concurrent(uids, progress_cb=_on_fetch)

        console.print(f"[green]Fetched [bold]{len(mails)}[/bold] message headers.[/green]")

        # ---- analyze with progress bar ----
        scan_results: list[ScanResult] = []
        stats: Optional[ScanStats] = None

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]Analyzing[/bold cyan]"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("[dim]{task.fields[current]}[/dim]"),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task(
                "analyze", total=len(mails), current="Starting…"
            )

            def _on_analyze(result: ScanResult) -> None:
                scan_results.append(result)
                icon = "🗑" if result.decision == "SIL" else "✅"
                subj = result.mail.subject[:40] or "(no subject)"
                progress.update(task, advance=1, current=f"{icon} {subj}")

            _, stats = engine.analyze(mails, progress_cb=_on_analyze)

        # ---- display results ----
        to_delete = [r for r in scan_results if r.decision == "SIL"]

        if not to_delete:
            console.print(
                Panel(
                    "[green]No junk messages detected. Your inbox looks clean! 🎉[/green]",
                    border_style="green",
                    box=box.ROUNDED,
                )
            )
        else:
            console.print(_build_results_table(to_delete))

        console.print(_build_stats_panel(stats, dry_run))

        # ---- confirmation + deletion ----
        if not dry_run and to_delete:
            confirmed = Confirm.ask(
                f"\n[bold red]Delete {len(to_delete)} message(s)?[/bold red]"
            )
            if confirmed:
                delete_uids = [r.mail.uid for r in to_delete]
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[bold red]Deleting[/bold red]"),
                    BarColumn(),
                    TaskProgressColumn(),
                    TimeElapsedColumn(),
                    console=console,
                    transient=True,
                ) as progress:
                    del_task = progress.add_task("delete", total=len(delete_uids))
                    deleted = engine.delete_mails(
                        delete_uids,
                        progress_cb=lambda _uid: progress.advance(del_task),
                    )
            console.print(
                f"[bold green]Deleted {len(deleted)} message(s).[/bold green]"
            )
            console.print(
                f"[bold yellow]⚠️ Please empty your Trash folder to permanently delete the messages.[/bold yellow]"
            )
            else:
                console.print("[yellow]Deletion cancelled.[/yellow]")
        elif dry_run and to_delete:
            console.print(
                "[yellow]Dry run – no messages were deleted. "
                "Pass [bold]--no-dry-run[/bold] to enable deletion.[/yellow]"
            )

    finally:
        engine.disconnect()


if __name__ == "__main__":
    main()
