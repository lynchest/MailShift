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

import csv
import getpass
import json
import os
import sys
from datetime import datetime
from pathlib import Path
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

from config import (
    AppConfig,
    DEFAULT_SYSTEM_PROMPT,
    Mode,
    OllamaConfig,
    Provider,
    add_to_blacklist,
    add_to_whitelist,
    build_imap_config,
    list_keywords,
    remove_from_blacklist,
    remove_from_whitelist,
)
from hardware import (
    calculate_optimal_workers,
    format_system_info,
    get_system_info,
)
from engine import (
    MailEngine,
    MailMeta,
    ScanResult,
    ScanStats,
    load_mails_cache,
    save_mails_cache,
)

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


def _save_cleanup_log(
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
    
    with open(log_filename, "w", encoding="utf-8") as f:
        json.dump(log_data, f, ensure_ascii=False, indent=2)
    
    return log_filename


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
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(export_data)
        
        console.print(f"[green]Exported {len(export_data)} results to {output_path}[/green]")
    
    elif suffix == ".json":
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(export_data, f, ensure_ascii=False, indent=2)
        
        console.print(f"[green]Exported {len(export_data)} results to {output_path}[/green]")
    
    else:
        console.print(f"[red]Unsupported file format: {suffix}. Use .csv or .json[/red]")


def _print_history() -> None:
    """Display cleanup history from all log files."""
    import glob
    
    log_files = sorted(glob.glob("cleanup_log_*.json"), reverse=True)
    
    if not log_files:
        console.print("[yellow]No cleanup history found.[/yellow]")
        return
    
    for log_file in log_files:
        try:
            with open(log_file, encoding="utf-8") as f:
                data = json.load(f)
            
            timestamp = data.get("timestamp", "unknown")
            provider = data.get("provider", "unknown")
            mode = data.get("mode", "unknown")
            stats = data.get("stats", {})
            deleted_msgs = data.get("deleted_messages", [])
            
            console.print(f"\n[bold cyan]📅 {timestamp}[/bold cyan]")
            console.print(f"   Provider: {provider} | Mode: {mode}")
            console.print(f"   Deleted: {stats.get('deleted_count', 0)} | Space saved: {stats.get('space_saved_mb', 0):.2f} MB")
            
            if deleted_msgs:
                table = Table(box=box.ROUNDED, show_lines=True)
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
                
                if len(deleted_msgs) > 10:
                    console.print(table)
                    console.print(f"   [dim]... and {len(deleted_msgs) - 10} more[/dim]")
                else:
                    console.print(table)
        except Exception as e:
            console.print(f"[red]Error reading {log_file}: {e}[/red]")


# ---------------------------------------------------------------------------
# Interactive credential prompts
# ---------------------------------------------------------------------------


def _prompt_provider() -> Provider:
    console.print("\n[bold cyan]Select mail provider:[/bold cyan]")
    console.print("  [1] Gmail  (IMAP over SSL, App Password)")
    console.print("  [2] Proton (via Proton Bridge at 127.0.0.1)")
    console.print("  [3] Custom (your own IMAP server)")
    choice = Prompt.ask("Provider", choices=["1", "2", "3"], default="1")
    if choice == "1":
        return Provider.GMAIL
    elif choice == "2":
        return Provider.PROTON
    return Provider.CUSTOM


def _prompt_custom_imap_settings() -> tuple[str, int, bool]:
    """Prompt for custom IMAP server settings."""
    host = Prompt.ask("\n[bold cyan]IMAP Host[/bold cyan] (e.g., imap.example.com)")
    port = Prompt.ask("[bold cyan]IMAP Port[/bold cyan]", default="993")
    use_ssl = Prompt.ask("[bold cyan]Use SSL?[/bold cyan]", choices=["y", "n"], default="y")
    return host, int(port), use_ssl.lower() == "y"


def _get_ollama_models(base_url: str = "http://localhost:11434") -> list[str]:
    """Get available models from Ollama API."""
    try:
        import requests
        resp = requests.get(f"{base_url}/api/tags", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def _prompt_mode() -> tuple[Mode, str]:
    console.print("\n[bold cyan]Select scan mode:[/bold cyan]")
    console.print("  [1] Fast – heuristic keyword matching")
    console.print("  [2] Pro  – heuristic + local Ollama LLM (requires Ollama running)")
    choice = Prompt.ask("Mode", choices=["1", "2"], default="1")
    mode = Mode.FAST if choice == "1" else Mode.PRO
    
    if mode == Mode.PRO:
        console.print("\n[bold cyan]Select Ollama model:[/bold cyan]")
        
        available_models = _get_ollama_models()
        
        console.print("  [1] Qwen3.5:2B  (recommended)")
        
        if available_models:
            console.print("  [bold]Available models:[/bold]")
            for idx, model in enumerate(available_models, start=2):
                console.print(f"  [{idx}] {model}")
        
        if available_models:
            choices = ["1"] + [str(i) for i in range(2, len(available_models) + 2)]
            model_choice = Prompt.ask("Model", choices=choices, default="1")
        else:
            model_choice = Prompt.ask("Model", choices=["1"], default="1")
        
        if model_choice == "1":
            model = "qwen2.5:2b"
        else:
            idx = int(model_choice) - 2
            model = available_models[idx]
        
        return mode, model
    
    return mode, "qwen2.5:2b"


def _prompt_credentials(provider: Provider) -> tuple[str, str]:
    if provider == Provider.GMAIL:
        label = "Gmail address"
    elif provider == Provider.PROTON:
        label = "Proton Bridge username"
    else:
        label = "IMAP Username"
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
@click.option("--provider", type=click.Choice(["gmail", "proton", "custom"]), default=None, help="Mail provider.")
@click.option("--mode", type=click.Choice(["fast", "pro"]), default=None, help="Scan mode.")
@click.option("--username", default=None, help="IMAP username / email address.")
@click.option("--password", default=None, help="IMAP password (App Password, etc.).")
@click.option("--host", default=None, help="Custom IMAP server host.")
@click.option("--port", default=None, type=int, help="Custom IMAP server port.")
@click.option("--use-ssl/--no-ssl", default=True, help="Use SSL for IMAP connection.")
@click.option("--dry-run/--no-dry-run", default=True, show_default=True, help="Dry run (default: enabled).")
@click.option("--scan-limit", default=None, type=int, help="Max number of messages to scan.")
@click.option("--workers", default=8, show_default=True, help="Number of concurrent IMAP workers.")
@click.option("--ollama-url", default="http://localhost:11434", show_default=True, help="Ollama API base URL.")
@click.option("--ollama-model", default="qwen2.5:3b", show_default=True, help="Ollama model name.")
@click.option("--ollama-prompt", default=None, help="Custom system prompt for Ollama (use quotes).")
@click.option("--uninstall", is_flag=True, help="Completely remove MailShift from this system.")
@click.option("--history", is_flag=True, help="Show cleanup history from log files.")
@click.option("--add-whitelist", "add_whitelist", default=None, help="Add a keyword to the whitelist.")
@click.option("--remove-whitelist", "remove_whitelist", default=None, help="Remove a keyword from the whitelist.")
@click.option("--add-blacklist", "add_blacklist", default=None, help="Add a keyword to the blacklist.")
@click.option("--remove-blacklist", "remove_blacklist", default=None, help="Remove a keyword from the blacklist.")
@click.option("--list-keywords", "list_keywords_flag", is_flag=True, help="List all whitelist and blacklist keywords.")
@click.option("--export", "export_file", default=None, help="Export scan results to CSV or JSON file.")
def main(
    provider: Optional[str],
    mode: Optional[str],
    username: Optional[str],
    password: Optional[str],
    host: Optional[str],
    port: Optional[int],
    use_ssl: bool,
    dry_run: bool,
    scan_limit: Optional[int],
    workers: int,
    ollama_url: str,
    ollama_model: str,
    ollama_prompt: Optional[str],
    uninstall: bool,
    history: bool,
    add_whitelist: Optional[str],
    remove_whitelist: Optional[str],
    add_blacklist: Optional[str],
    remove_blacklist: Optional[str],
    list_keywords_flag: bool,
    export_file: Optional[str],
) -> None:
    """MailShift – privacy-first newsletter purger for Gmail and Proton Mail."""

    if history:
        _print_history()
        return

    if list_keywords_flag:
        whitelist, blacklist = list_keywords()
        console.print(Panel(
            f"[bold cyan]Whitelist ({len(whitelist)} keywords):[/bold cyan]\n" +
            ", ".join(whitelist) if whitelist else "[dim]Empty[/dim]",
            title="[bold green]Keywords[/bold green]",
            border_style="green",
            box=box.ROUNDED,
        ))
        console.print(Panel(
            f"[bold red]Blacklist ({len(blacklist)} keywords):[/bold red]\n" +
            ", ".join(blacklist) if blacklist else "[dim]Empty[/dim]",
            border_style="red",
            box=box.ROUNDED,
        ))
        return

    if add_whitelist:
        if add_to_whitelist(add_whitelist):
            console.print(f"[green]Added '{add_whitelist}' to whitelist.[/green]")
        else:
            console.print(f"[yellow]'{add_whitelist}' already exists in whitelist.[/yellow]")
        return

    if remove_whitelist:
        if remove_from_whitelist(remove_whitelist):
            console.print(f"[green]Removed '{remove_whitelist}' from whitelist.[/green]")
        else:
            console.print(f"[yellow]'{remove_whitelist}' not found in whitelist.[/yellow]")
        return

    if add_blacklist:
        if add_to_blacklist(add_blacklist):
            console.print(f"[green]Added '{add_blacklist}' to blacklist.[/green]")
        else:
            console.print(f"[yellow]'{add_blacklist}' already exists in blacklist.[/yellow]")
        return

    if remove_blacklist:
        if remove_from_blacklist(remove_blacklist):
            console.print(f"[green]Removed '{remove_blacklist}' from blacklist.[/green]")
        else:
            console.print(f"[yellow]'{remove_blacklist}' not found in blacklist.[/yellow]")
        return

    if uninstall:
        console.print(Panel(
            "[bold red]MailShift Tam Kaldırma[/bold red]",
            border_style="red",
            box=box.ROUNDED,
        ))
        
        confirm = Confirm.ask(
            "\n[bold yellow]Bu işlem MailShift'i ve tüm verilerini siler:[/bold yellow]\n"
            "  • Python paketleri (click, rich, imap-tools)\n"
            "  • Önbellek dosyaları (.json cache)\n"
            "  • Python __pycache__ klasörleri\n"
            "  • Proje dosyaları\n\n"
            "[bold]Devam?[/bold]",
            default=False,
        )
        
        if not confirm:
            console.print("[yellow]İşlem iptal edildi.[/yellow]")
            return
        
        import shutil
        import os
        
        base_path = os.path.dirname(os.path.abspath(__file__))
        
        console.print("\n[cyan]Kaldırma işlemi başlıyor...[/cyan]\n")
        
        items_removed = []
        
        cache_files = ["whitelist.json", "blacklist.json", "mails_cache.json"]
        for f in cache_files:
            fp = os.path.join(base_path, f)
            if os.path.exists(fp):
                os.remove(fp)
                items_removed.append(f"  • {f}")
        
        pycache = os.path.join(base_path, "__pycache__")
        if os.path.exists(pycache):
            shutil.rmtree(pycache)
            items_removed.append("  • __pycache__/")
        
        git_dir = os.path.join(base_path, ".git")
        if os.path.exists(git_dir):
            shutil.rmtree(git_dir)
            items_removed.append("  • .git/")
        
        py_files = [f for f in os.listdir(base_path) if f.endswith(".py")]
        for pf in py_files:
            fp = os.path.join(base_path, pf)
            os.remove(fp)
            items_removed.append(f"  • {pf}")
        
        other_files = ["requirements.txt", "README.md", "LICENSE", ".gitignore"]
        for of in other_files:
            fp = os.path.join(base_path, of)
            if os.path.exists(fp):
                os.remove(fp)
                items_removed.append(f"  • {of}")
        
        tests_dir = os.path.join(base_path, "tests")
        if os.path.exists(tests_dir):
            shutil.rmtree(tests_dir)
            items_removed.append("  • tests/")
        
        console.print("[green]Kaldırılan öğeler:[/green]")
        for item in items_removed:
            console.print(item)
        
        console.print(Panel(
            "[bold green]✓ MailShift başarıyla kaldırıldı![/bold green]\n\n"
            "[dim]Klasörü manuel olarak silebilirsiniz:[/dim]\n"
            f"[dim]{base_path}[/dim]",
            border_style="green",
            box=box.ROUNDED,
        ))
        return

    _print_banner()

    # ---- resolve provider / mode (interactive if not provided) ----
    resolved_provider = Provider(provider) if provider else _prompt_provider()
    resolved_mode, selected_model = (Mode(mode), ollama_model) if mode else _prompt_mode()

    # ---- credentials ----
    if not username or not password:
        username, password = _prompt_credentials(resolved_provider)

    # ---- custom IMAP settings ----
    custom_host = host
    custom_port = port
    custom_use_ssl = use_ssl if resolved_provider == Provider.CUSTOM else None
    
    if resolved_provider == Provider.CUSTOM and not custom_host:
        custom_host, custom_port, custom_use_ssl = _prompt_custom_imap_settings()

    # ---- build config ----
    imap_cfg = build_imap_config(
        resolved_provider,
        username,
        password,
        host=custom_host,
        port=custom_port,
        use_ssl=custom_use_ssl,
    )
    selected_ollama_model = selected_model if not mode else ollama_model
    ollama_cfg = OllamaConfig(
        base_url=ollama_url,
        model=selected_ollama_model,
        system_prompt=ollama_prompt if ollama_prompt else DEFAULT_SYSTEM_PROMPT,
    )
    
    if workers:
        resolved_workers = workers
    else:
        resolved_workers = calculate_optimal_workers(selected_ollama_model, resolved_mode.value)
    
    cfg = AppConfig(
        provider=resolved_provider,
        mode=resolved_mode,
        imap=imap_cfg,
        ollama=ollama_cfg,
        dry_run=dry_run,
        max_workers=resolved_workers,
        scan_limit=scan_limit,
    )

    # ---- summary of what we're about to do ----
    sys_info = get_system_info()
    model_display = (
        f"{resolved_mode.value.capitalize()} ({selected_ollama_model})"
        if resolved_mode == Mode.PRO
        else "Fast (heuristic)"
    )
    hardware_info = format_system_info(sys_info, resolved_mode.value, selected_ollama_model)
    
    console.print(
        Panel(
            f"[bold]Provider:[/bold] {resolved_provider.value.capitalize()}\n"
            f"[bold]Mode:[/bold]     {model_display}\n"
            f"[bold]User:[/bold]     {username}\n"
            f"[bold]Workers:[/bold]  {resolved_workers}\n"
            f"{hardware_info}\n"
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

        save_mails_cache(mails)

        # ---- check cache for Pro mode ----
        if resolved_mode == Mode.PRO:
            cached_mails = load_mails_cache()
            if cached_mails and len(cached_mails) == len(mails):
                use_cache = Confirm.ask(
                    f"\n[bold cyan]Onbellek bulundu![/bold cyan]\n"
                    f"Onceki Fast mode'dan {len(cached_mails)} mail bulundu.\n"
                    f"IMAP'den yeniden cekmek yerine onbellek kullanilsin mi?",
                    default=True,
                )
                if use_cache:
                    console.print("[cyan]Onbellek kullaniliyor, sadece icerik cekiliyor...[/cyan]")
                    mails = cached_mails

                    with Progress(
                        SpinnerColumn(),
                        TextColumn("[bold cyan]Fetching body content[/bold cyan]"),
                        BarColumn(),
                        TaskProgressColumn(),
                        TimeElapsedColumn(),
                        console=console,
                        transient=True,
                    ) as progress:
                        task = progress.add_task("fetch_body", total=len(mails))
                        mails = engine.fetch_body_for_cached_mails(
                            mails,
                            progress_cb=lambda m: progress.advance(task),
                        )

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

        if export_file:
            export_scan_results(to_delete, export_file)

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
                    
                    deleted_results = [r for r in to_delete if r.mail.uid in deleted]
                    log_file = _save_cleanup_log(
                        deleted_results,
                        stats,
                        resolved_provider.value,
                        resolved_mode.value,
                    )
                    
                    console.print(
                        f"[bold green]Deleted {len(deleted)} message(s).[/bold green]"
                    )
                    console.print(
                        f"[bold yellow]⚠️ Please empty your Trash folder to permanently delete the messages.[/bold yellow]"
                    )
                    console.print(
                        f"[dim]Results saved to: {log_file}[/dim]"
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
