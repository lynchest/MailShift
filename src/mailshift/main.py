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

import sys
import io
import re
import socket
import time
import threading
import unicodedata
from datetime import date
from typing import Optional, List
from concurrent.futures import ThreadPoolExecutor, as_completed

# Windows Terminal Fix
if sys.platform == "win32":
    import os
    if getattr(sys.stdout, "encoding", "").lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if getattr(sys.stderr, "encoding", "").lower() != "utf-8":
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    if "TERM" not in os.environ:
        os.environ["TERM"] = "xterm-256color"

import click
from rich import box
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.prompt import Confirm, Prompt

from .config.config import (
    AppConfig,
    DEFAULT_SYSTEM_PROMPT,
    LMStudioConfig,
    Mode,
    OllamaConfig,
    Provider,
    build_imap_config,
)
from .utils.hardware import (
    calculate_optimal_workers,
    format_system_info,
    get_system_info,
)
from .core.engine import MailEngine, MailMeta, ScanResult, ScanStats
from .core.session import AnalyzeProgressHandler, FetchProgressHandler, LLMWorker
from .db.database import save_mails_cache, load_mails_cache_by_uids
from .core.analyzers.pro import (
    check_ollama_health,
    check_lm_studio_health,
    close_ollama_session,
    unload_lm_studio_models,
)
from .core.analyzers.fast import fast_analyze
from .ui.styles import (
    console,
    build_results_table,
    build_stats_panel,
    clear_console,
)
from .utils.history import save_cleanup_log, export_scan_results, print_history
from .utils.logger import log
from .ui.cli import (
    handle_keywords,
    handle_uninstall,
    prompt_credentials,
    prompt_custom_imap_settings,
    prompt_mode,
    prompt_provider,
    cleanup_ollama_if_it_was_started_by_us,
    cleanup_lm_studio_if_it_was_started_by_us,
)


def _prompt_unsubscribe(to_delete: list) -> None:
    """
    After a scan, offer the user three unsubscribe options for emails
    that carry a List-Unsubscribe HTTP URL.

    Option 1 – auto-unsubscribe from all detected subscriptions.
    Option 2 – pick senders interactively from a numbered list.
    Option 3 – export all links to a file for manual processing.
    """
    from .utils.unsubscribe import build_unsubscribe_entries, perform_unsubscribe, export_unsubscribe_links

    entries = build_unsubscribe_entries(to_delete)
    if not entries:
        return

    console.print(Panel(
        f"[bold]{len(entries)}[/bold] abonelik bağlantısı tespit edildi.\n"
        "[dim]Bu aboneliklerden ayrılmak ister misiniz?[/dim]",
        title="[bold magenta]Abonelik İptali[/bold magenta]",
        border_style="magenta",
        box=box.ROUNDED,
    ))
    console.print(
        "  [bold magenta][1][/bold magenta] Tümünden otomatik abonelik iptali\n"
        "  [bold magenta][2][/bold magenta] Seçerek abonelik iptali\n"
        "  [bold magenta][3][/bold magenta] Abonelik linklerini dosyaya aktar\n"
        "  [bold magenta][4][/bold magenta] Atla\n"
    )
    choice = Prompt.ask("[bold]Seçiminiz[/bold]", choices=["1", "2", "3", "4"], default="4")

    if choice == "1":
        _do_unsubscribe_all(entries, perform_unsubscribe)
    elif choice == "2":
        _do_unsubscribe_select(entries, perform_unsubscribe)
    elif choice == "3":
        _do_export_unsubscribe(entries, export_unsubscribe_links)


def _do_unsubscribe_all(entries, perform_fn) -> None:
    """Send unsubscribe requests to all entries."""
    ok, fail = 0, 0
    with Progress(
        SpinnerColumn(),
        TextColumn("[bold magenta]Abonelik iptali gönderiliyor[/bold magenta]"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("unsub", total=len(entries))
        for entry in entries:
            success, msg = perform_fn(entry.unsubscribe_url)
            log.info(f"Unsubscribe {'OK' if success else 'FAIL'} [{msg}]: {entry.unsubscribe_url}")
            if success:
                ok += 1
            else:
                fail += 1
            progress.advance(task)

    result_lines = f"[bold green]✔ {ok} başarılı[/bold green]"
    if fail:
        result_lines += f"  [bold red]✘ {fail} başarısız[/bold red]"
    console.print(Panel(result_lines, title="[bold magenta]Abonelik İptali Tamamlandı[/bold magenta]", border_style="magenta", box=box.ROUNDED))


def _do_unsubscribe_select(entries, perform_fn) -> None:
    """Show a numbered list; user enters comma-separated choices."""
    from rich.table import Table as RichTable

    tbl = RichTable(box=box.SIMPLE, show_header=True, padding=(0, 1))
    tbl.add_column("#", style="dim", width=4)
    tbl.add_column("Gönderici", style="cyan", max_width=40, no_wrap=True)
    tbl.add_column("Mail", style="yellow", justify="right", width=6)
    tbl.add_column("Unsubscribe URL", style="dim", max_width=50, no_wrap=True)
    for idx, e in enumerate(entries, start=1):
        tbl.add_row(str(idx), e.sender[:40], str(e.mail_count), e.unsubscribe_url[:50])
    console.print(tbl)

    raw = Prompt.ask(
        "[bold]İptal etmek istediğiniz numaraları girin[/bold] [dim](örn: 1,3 veya hepsi için all)[/dim]"
    ).strip()

    if raw.lower() == "all":
        selected = entries
    else:
        chosen: list = []
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit():
                idx = int(part) - 1
                if 0 <= idx < len(entries):
                    chosen.append(entries[idx])
        selected = chosen

    if not selected:
        console.print("[yellow]Hiçbir seçim yapılmadı.[/yellow]")
        return

    _do_unsubscribe_all(selected, perform_fn)


def _do_export_unsubscribe(entries, export_fn) -> None:
    """Prompt for output path and export unsubscribe links."""
    from .utils.paths import get_path

    default_path = str(get_path("logs") / "unsubscribe_links.json")
    output_path = Prompt.ask(
        "[bold]Kayıt yolu[/bold]",
        default=default_path,
    )
    try:
        export_fn(entries, output_path)
        console.print(Panel(
            f"[bold green]✔ {len(entries)} abonelik linki kaydedildi.[/bold green]\n"
            f"[dim]{output_path}[/dim]",
            title="[bold magenta]Dışa Aktarıldı[/bold magenta]",
            border_style="magenta",
            box=box.ROUNDED,
        ))
    except Exception as exc:
        console.print(f"[bold red]Dışa aktarma başarısız:[/bold red] {exc}")


def clean_text(text: Optional[str], max_len: int = 35) -> str:
    """Normalize progress labels to avoid wrapped/duplicated-looking bars on narrow terminals."""
    if not text:
        return "(bilinmiyor)"

    normalized = unicodedata.normalize("NFKC", text)
    normalized = re.sub(r"[\x00-\x1f\x7f]", " ", normalized)
    normalized = "".join(
        ch for ch in normalized
        if unicodedata.category(ch) not in {"Mn", "Me", "Cf", "Cs"}
    )

    cleaned = " ".join(normalized.split())
    return (cleaned[:max_len] + "…") if len(cleaned) > max_len else (cleaned or "(bilinmiyor)")


def format_duration(seconds: float) -> str:
    """Format a duration in seconds as a short human-readable label."""
    total = max(0, int(round(seconds)))
    minutes, sec = divmod(total, 60)
    return f"~{minutes} dk {sec:02d} sn" if minutes else f"~{sec} sn"


_IMAP_MONTH_TO_NUM = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}
_IMAP_NUM_TO_MONTH = {v: k for k, v in _IMAP_MONTH_TO_NUM.items()}


def parse_cli_date(raw_value: Optional[str], option_name: str) -> Optional[date]:
    """Parse CLI date text into a date object (supports YYYY-MM-DD and IMAP DD-Mon-YYYY)."""
    if raw_value is None:
        return None

    candidate = raw_value.strip()
    if not candidate:
        return None

    try:
        return date.fromisoformat(candidate)
    except ValueError:
        pass

    imap_match = re.fullmatch(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})", candidate)
    if imap_match:
        day_s, mon_s, year_s = imap_match.groups()
        month = _IMAP_MONTH_TO_NUM.get(mon_s.capitalize())
        if month is None:
            raise click.BadParameter(
                f"{option_name} ay formatı geçersiz: '{raw_value}'. Örnek: 01-Jan-2025"
            )
        try:
            return date(int(year_s), month, int(day_s))
        except ValueError as exc:
            raise click.BadParameter(f"{option_name} geçersiz: '{raw_value}'") from exc

    for sep in ("/", "."):
        parts = candidate.split(sep)
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            day_i, month_i, year_i = (int(parts[0]), int(parts[1]), int(parts[2]))
            try:
                return date(year_i, month_i, day_i)
            except ValueError as exc:
                raise click.BadParameter(f"{option_name} geçersiz: '{raw_value}'") from exc

    raise click.BadParameter(
        f"{option_name} formatı geçersiz: '{raw_value}'. Örnek: 2025-01-01 veya 01-Jan-2025"
    )


def format_imap_date(value: Optional[date]) -> Optional[str]:
    """Convert a date object to locale-independent IMAP date format (DD-Mon-YYYY)."""
    if value is None:
        return None
    month = _IMAP_NUM_TO_MONTH[value.month]
    return f"{value.day:02d}-{month}-{value.year:04d}"


def _can_open_tcp(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def ensure_proton_bridge_ready(cfg: AppConfig, max_checks: int = 5) -> bool:
    """Probe Proton Bridge TCP endpoint and guide the user before IMAP login."""
    if cfg.provider != Provider.PROTON:
        return True

    host, port = cfg.imap.host, cfg.imap.port
    if _can_open_tcp(host, port):
        return True

    console.print(
        Panel(
            "[bold yellow]Proton Bridge çalışmıyor görünüyor.[/bold yellow]\n"
            f"MailShift şu adrese bağlanamıyor: [cyan]{host}:{port}[/cyan]\n"
            "[dim]Bridge'i başlatın ve hazır olunca Enter'a basın.[/dim]",
            title="[bold yellow]Proton Bridge Bekleniyor[/bold yellow]",
            border_style="yellow",
            box=box.ROUNDED,
        )
    )

    for attempt in range(1, max_checks + 1):
        answer = Prompt.ask("[bold]Tekrar denemek için Enter, çıkmak için q[/bold]", default="").strip().lower()
        if answer in {"q", "quit", "exit"}:
            return False
        if _can_open_tcp(host, port):
            console.print("[green]✔ Proton Bridge bağlantısı algılandı.[/green]")
            return True
        if attempt < max_checks:
            console.print(f"[yellow]Bridge hâlâ erişilemiyor ({attempt}/{max_checks}).[/yellow]")

    return False


def verify_llm_health(cfg: AppConfig, selected_model: str) -> bool:
    """Checks LLM backend health and prompts fallback if offline. Returns True if backend is healthy/kept."""
    is_lm_studio = cfg.llm_backend == "lm_studio"
    backend_name = "LM Studio" if is_lm_studio else "Ollama"
    
    with console.status(f"[cyan]{backend_name} bağlantısı kontrol ediliyor…[/cyan]", spinner="dots"):
        if is_lm_studio:
            ok, msg = check_lm_studio_health(cfg.lm_studio.base_url, selected_model)
        else:
            ok, msg = check_ollama_health(cfg.ollama.base_url, selected_model)

    if ok:
        console.print(f"[green]✔ {msg}[/green]")
        return True

    console.print(f"[bold red]✘ {msg}[/bold red]")
    if not Confirm.ask(f"[yellow]{backend_name} olmadan devam etmek ister misiniz? (Fast mode'a geçilecek)[/yellow]", default=False):
        sys.exit(1)
        
    console.print("[yellow]Fast mode'a geçildi.[/yellow]")
    cfg.mode = Mode.FAST
    cfg.llm_backend = "ollama" if is_lm_studio else cfg.llm_backend # fallback
    return False


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--provider", type=click.Choice(["gmail", "proton", "custom"]), default=None, help="Mail provider.")
@click.option("--mode", type=click.Choice(["fast", "pro"]), default=None, help="Scan mode.")
@click.option("--username", default=None, help="IMAP username / email address.")
@click.option("--password", default=None, help="IMAP password.")
@click.option("--host", default=None, help="Custom IMAP server host.")
@click.option("--port", default=None, type=int, help="Custom IMAP server port.")
@click.option("--use-ssl/--no-ssl", default=True, help="Use SSL for IMAP connection.")
@click.option("--dry-run/--no-dry-run", default=True, show_default=True, help="Dry run (default: enabled).")
@click.option("--scan-limit", default=None, type=int, help="Max number of messages to scan.")
@click.option("--since", default=None, help="Scan only messages on/after date (YYYY-MM-DD or DD-Mon-YYYY).")
@click.option("--before", default=None, help="Scan only messages before date (YYYY-MM-DD or DD-Mon-YYYY).")
@click.option("--ollama-url", default="http://localhost:11434", show_default=True, help="Ollama API base URL.")
@click.option("--ollama-model", default="qwen3.5:2B", show_default=True, help="Ollama model name.")
@click.option("--ollama-prompt", default=None, help="Custom system prompt for Ollama.")
@click.option("--uninstall", is_flag=True, help="Completely remove MailShift from this system.")
@click.option("--history", is_flag=True, help="Show cleanup history from log files.")
@click.option("--add-whitelist", "add_whitelist", default=None, help="Add a keyword to the whitelist.")
@click.option("--remove-whitelist", "remove_whitelist", default=None, help="Remove a keyword from the whitelist.")
@click.option("--add-blacklist", "add_blacklist", default=None, help="Add a keyword to the blacklist.")
@click.option("--remove-blacklist", "remove_blacklist", default=None, help="Remove a keyword from the blacklist.")
@click.option("--list-keywords", "list_keywords_flag", is_flag=True, help="List all whitelist and blacklist keywords.")
@click.option("--export", "export_file", default=None, help="Export scan results to CSV or JSON file.")
@click.option("--workers", "-w", type=int, default=None, help="Number of workers for parallel processing.")
def main(
    provider: Optional[str], mode: Optional[str], username: Optional[str],
    password: Optional[str], host: Optional[str], port: Optional[int],
    use_ssl: bool, dry_run: bool, scan_limit: Optional[int],
    since: Optional[str], before: Optional[str],
    ollama_url: str, ollama_model: str, ollama_prompt: Optional[str],
    uninstall: bool, history: bool, add_whitelist: Optional[str],
    remove_whitelist: Optional[str], add_blacklist: Optional[str],
    remove_blacklist: Optional[str], list_keywords_flag: bool,
    export_file: Optional[str], workers: Optional[int],
) -> None:
    """MailShift | privacy-first newsletter purger for Gmail and Proton Mail."""
    log.info("Starting MailShift CLI")

    # Early exits
    if history:
        return print_history()
    if handle_keywords(list_keywords_flag, add_whitelist, remove_whitelist, add_blacklist, remove_blacklist):
        return
    if uninstall:
        return handle_uninstall()

    # Resolution & Setup
    resolved_provider = Provider(provider) if provider else prompt_provider()
    
    if mode:
        resolved_mode, selected_model, llm_backend, manual_workers = Mode(mode), ollama_model, "ollama", workers
    else:
        resolved_mode, selected_model, llm_backend, manual_workers = prompt_mode()
        manual_workers = workers if workers is not None else manual_workers

    if not username or not password:
        username, password = prompt_credentials(resolved_provider, preset_username=username, preset_password=password)

    custom_host, custom_port, custom_use_ssl = host, port, use_ssl if resolved_provider == Provider.CUSTOM else None
    if resolved_provider == Provider.CUSTOM and not custom_host:
        custom_host, custom_port, custom_use_ssl = prompt_custom_imap_settings()

    imap_cfg = build_imap_config(
        resolved_provider, username, password, host=custom_host, port=custom_port, use_ssl=custom_use_ssl,
    )

    since_date = parse_cli_date(since, "--since")
    before_date = parse_cli_date(before, "--before")
    if since_date and before_date and before_date <= since_date:
        raise click.BadParameter("--before tarihi, --since tarihinden sonra olmalıdır.")

    since_imap = format_imap_date(since_date)
    before_imap = format_imap_date(before_date)
    
    sys_prompt = ollama_prompt or DEFAULT_SYSTEM_PROMPT
    ollama_cfg = OllamaConfig(base_url=ollama_url, model=selected_model, system_prompt=sys_prompt)
    lm_studio_cfg = LMStudioConfig(model=selected_model if llm_backend == "lm_studio" else "", system_prompt=sys_prompt)

    resolved_workers = calculate_optimal_workers(selected_model, resolved_mode.value, manual_workers=manual_workers, backend=llm_backend)

    cfg = AppConfig(
        provider=resolved_provider, mode=resolved_mode, imap=imap_cfg,
        ollama=ollama_cfg, lm_studio=lm_studio_cfg, llm_backend=llm_backend,
        dry_run=dry_run, scan_limit=scan_limit, since=since_imap,
        before=before_imap, max_workers=manual_workers,
    )

    clear_console()

    # Pre-flight Configuration Summary
    sys_info = get_system_info()
    model_display = f"Pro ({( 'LM Studio' if llm_backend == 'lm_studio' else 'Ollama' )}: {selected_model})" if resolved_mode == Mode.PRO else "Fast (heuristic)"
    hardware_info = format_system_info(sys_info, resolved_mode.value, selected_model)
    date_filter_lines: List[str] = []
    if since_imap:
        date_filter_lines.append(f"[bold]Since:[/bold]    {since_imap}")
    if before_imap:
        date_filter_lines.append(f"[bold]Before:[/bold]   {before_imap}")
    date_filter_block = ("\n" + "\n".join(date_filter_lines)) if date_filter_lines else ""
    
    console.print(
        Panel(
            f"[bold]Provider:[/bold] {resolved_provider.value.capitalize()}\n"
            f"[bold]Mode:[/bold]     {model_display}\n"
            f"[bold]User:[/bold]     {username}\n"
            f"[bold]Workers:[/bold]  {resolved_workers} {'[yellow](manual)[/yellow]' if manual_workers else '[dim](auto)[/dim]'}\n"
            f"{hardware_info}\n"
            f"[bold]Dry run:[/bold]  {'[yellow]Yes | no emails will be deleted[/yellow]' if dry_run else '[red]No | emails WILL be deleted[/red]'}"
            f"{date_filter_block}",
            title="[bold blue]Configuration[/bold blue]", border_style="blue", box=box.ROUNDED,
        )
    )

    if cfg.mode == Mode.PRO:
        verify_llm_health(cfg, selected_model)

    # Core Execution Initialization
    engine: Optional[MailEngine] = None
    cancel_event = threading.Event()
    stats = ScanStats()
    scan_results: List[ScanResult] = []

    try:
        if not ensure_proton_bridge_ready(cfg):
            console.print("[bold red]Proton Bridge bağlantısı kurulamadı. İşlem iptal edildi.[/bold red]")
            sys.exit(1)

        # ---- Connect & Fetch UIDs ----
        with console.status("[cyan]Connecting to IMAP server…[/cyan]", spinner="dots"):
            try:
                engine = MailEngine(cfg)
                engine.connect()
            except Exception as exc:
                console.print(f"[bold red]Connection failed:[/bold red] {exc}")
                sys.exit(1)

        with console.status("[cyan]Listing messages…[/cyan]", spinner="dots"):
            current_uids = engine.list_uids()

        if not current_uids:
            return console.print("[yellow]No messages found in INBOX.[/yellow]")

        console.print(f"[green]Found [bold]{len(current_uids)}[/bold] message(s) in INBOX.[/green]")
        
        # ---- Cache Management ----
        with console.status("[cyan]Loading matching cache rows…[/cyan]", spinner="dots"):
            cached_mails = load_mails_cache_by_uids(current_uids)
            
        cached_dict = {m.uid: m for m in cached_mails}
        missing_uids = [uid for uid in current_uids if uid not in cached_dict]
        mails = [cached_dict[uid] for uid in current_uids if uid in cached_dict]
        
        if mails:
            console.print(f"[cyan]Cache'den [bold]{len(mails)}[/bold] mail başlığı yüklendi.[/cyan]")

        # ---- Fetch Missing Headers ----
        if missing_uids:
            console.print(f"[cyan]Sunucudan [bold]{len(missing_uids)}[/bold] yeni ileti başlığı çekiliyor…[/cyan]")
            
            with Progress(
                SpinnerColumn(), TextColumn("[bold cyan]Fetching Headers[/bold cyan]"),
                BarColumn(), TaskProgressColumn(), TextColumn("[dim]{task.fields[current]}[/dim]"),
                TimeElapsedColumn(), console=console, transient=True
            ) as progress:
                task = progress.add_task("fetch", total=len(missing_uids), current="Starting…")
                fetch_handler = FetchProgressHandler(
                    mails=mails,
                    progress=progress,
                    task_id=task,
                    total_count=len(missing_uids),
                    clean_text_fn=clean_text,
                    format_duration_fn=format_duration,
                )
                engine.fetch_headers_concurrent(missing_uids, progress_cb=fetch_handler)
            console.print("[green]Yeni başlıklar eklendi.[/green]")

        save_mails_cache(mails)

        # ---- Analysis Phase ----
        if cfg.mode == Mode.PRO:
            # Phase 1: Fast heuristic scan
            fast_results: List[ScanResult] = []
            with Progress(
                SpinnerColumn(), TextColumn("[bold cyan]Phase 1 | Heuristic Scan[/bold cyan]"),
                BarColumn(), TaskProgressColumn(), TextColumn("[dim]{task.fields[current]}[/dim]"),
                TimeElapsedColumn(), TimeRemainingColumn(), console=console, transient=True
            ) as progress:
                task = progress.add_task("fast", total=len(mails), current="Starting…")
                
                for idx, mail in enumerate(mails, start=1):
                    res = fast_analyze(mail)
                    fast_results.append(res)
                    icon = "SIL" if res.decision == "SIL" else "TUT"
                    subj = clean_text(mail.subject, max_len=24)
                    
                    if idx % 20 == 0 or idx == len(mails):
                        progress.update(task, advance=(idx % 20) or 20, current=f"{icon} {subj}")

            sil_candidates = [r for r in fast_results if r.decision == "SIL"]
            tut_results = [r for r in fast_results if r.decision == "TUT"]
            console.print(f"[cyan]Phase 1 tamamlandı: [bold]{len(sil_candidates)}[/bold] şüpheli, [bold]{len(tut_results)}[/bold] güvenli.[/cyan]")

            # Phase 2: LLM Verification
            if sil_candidates:
                need_body_mails = [r.mail for r in sil_candidates if not r.mail.body_preview]
                if need_body_mails:
                    with console.status(f"[cyan]Pro mode: [bold]{len(need_body_mails)}[/bold] SIL adayı için body çekiliyor…[/cyan]", spinner="dots"):
                        engine.fetch_body_for_cached_mails(need_body_mails, progress_cb=lambda m: None)

                llm_verified: List[ScanResult] = []
                max_workers = calculate_optimal_workers(selected_model, cfg.mode.value, manual_workers=cfg.max_workers)
                
                with Progress(
                    SpinnerColumn(), TextColumn("[bold magenta]Phase 2 | LLM Verification[/bold magenta]"),
                    BarColumn(), TaskProgressColumn(), TextColumn("[dim]{task.fields[current]}[/dim]"),
                    console=console, transient=True
                ) as progress:
                    task = progress.add_task("llm", total=len(sil_candidates), current="Starting…")
                    phase2_start = time.perf_counter()
                    llm_worker = LLMWorker(cfg=cfg, cancel_event=cancel_event)

                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        futures = {executor.submit(llm_worker, (i, c)): i for i, c in enumerate(sil_candidates)}
                        temp_results = [None] * len(sil_candidates)
                        done_count = 0
                        
                        for future in as_completed(futures):
                            try:
                                idx, res = future.result()
                                temp_results[idx] = res
                                subj = clean_text(res.mail.subject, max_len=24)
                                icon = "SIL" if res.decision == "SIL" else "TUT"
                                
                                done_count += 1
                                elapsed = max(0.001, time.perf_counter() - phase2_start)
                                remaining = max(0.0, (len(sil_candidates) - done_count) * (elapsed / done_count))
                                progress.update(task, advance=1, current=f"{icon} {subj} | kalan {format_duration(remaining)}")
                            except Exception as exc:
                                idx = futures[future]
                                temp_results[idx] = ScanResult(mail=sil_candidates[idx].mail, decision="TUT", reason=f"llm-error:{exc}")
                                progress.update(task, advance=1, current="⚠️ hata")

                    llm_verified = [r for r in temp_results if r is not None]

                llm_confirmed = sum(1 for r in llm_verified if r.decision == "SIL")
                console.print(f"[magenta]Phase 2 tamamlandı: [bold]{llm_confirmed}[/bold] silme onaylandı, [bold]{len(llm_verified) - llm_confirmed}[/bold] kurtarıldı.[/magenta]")
                scan_results = tut_results + llm_verified

                if cfg.llm_backend == "lm_studio":
                    with console.status("[cyan]LM Studio modeli VRAM'den tahliye ediliyor…[/cyan]", spinner="dots"):
                        unload_lm_studio_models(cfg.lm_studio.base_url, selected_model)
            else:
                scan_results = fast_results

        else:
            # Fast Mode Analysis
            with Progress(
                SpinnerColumn(), TextColumn("[bold cyan]Analyzing[/bold cyan]"),
                BarColumn(), TaskProgressColumn(), TextColumn("[dim]{task.fields[current]}[/dim]"),
                TimeElapsedColumn(), console=console, transient=True
            ) as progress:
                task = progress.add_task("analyze", total=len(mails), current="Starting…")
                analyze_handler = AnalyzeProgressHandler(
                    scan_results=scan_results,
                    progress=progress,
                    task_id=task,
                    total_count=len(mails),
                    clean_text_fn=clean_text,
                )
                _, raw_stats = engine.analyze(mails, progress_cb=analyze_handler)

        # Build final stats object
        for r in scan_results:
            stats.total_scanned += 1
            stats.total_size_bytes += r.mail.size_bytes
            if r.decision == "SIL":
                stats.marked_for_deletion += 1
                stats.marked_size_bytes += r.mail.size_bytes

        # ---- Display and Action ----
        to_delete = [r for r in scan_results if r.decision == "SIL"]

        if not to_delete:
            return console.print(Panel("[green]No junk messages detected. Your inbox looks clean! 🎉[/green]", border_style="green", box=box.ROUNDED))
            
        console.print(build_results_table(to_delete))
        console.print(build_stats_panel(stats, cfg.dry_run))

        if export_file:
            export_scan_results(to_delete, export_file)

        # Dry-run modundaysa önizleme logu kaydet ve bilgilendirme paneli göster,
        # ardından silme menüsüne düş (return yok — kullanıcı devam edebilir).
        if cfg.dry_run:
            log_file = save_cleanup_log(
                to_delete,
                stats,
                cfg.provider.value,
                cfg.mode.value,
                dry_run=True,
                action="preview",
            )
            console.print(Panel(
                f"[bold]{len(to_delete)}[/bold] mesaj silinebilir olarak işaretlendi.\n"
                "[dim]Dry run modu — silmek için aşağıdan seçin, atlamak için İptal.[/dim]\n"
                f"[dim]Log kaydedildi: {log_file}[/dim]",
                title="[bold yellow]Dry Run Önizlemesi[/bold yellow]", border_style="yellow", box=box.ROUNDED
            ))

        console.print("\n  [bold cyan][1][/bold cyan] Kalıcı Sil  [dim](geri alınamaz)[/dim]\n  [bold cyan][2][/bold cyan] Çöp Kutusuna Gönder\n  [bold cyan][3][/bold cyan] İptal\n")
        choice = Prompt.ask(f"[bold]{len(to_delete)} mesaj için ne yapmak istersiniz?[/bold]", choices=["1", "2", "3"], default="3")
        clear_console()

        if choice in ("1", "2"):
            trash_folder = {Provider.GMAIL: "[Gmail]/Trash", Provider.PROTON: "Trash"}.get(cfg.provider, "Trash")
            delete_uids = [r.mail.uid for r in to_delete]
            action_label = "Kalıcı Siliniyor" if choice == "1" else "Çöp Kutusuna Taşınıyor"

            with Progress(
                SpinnerColumn(), TextColumn(f"[bold red]{action_label}[/bold red]"),
                BarColumn(), TaskProgressColumn(), TimeElapsedColumn(), console=console, transient=True
            ) as progress:
                del_task = progress.add_task("action", total=len(delete_uids))
                cb = lambda _uid: progress.advance(del_task)

                deleted = engine.delete_mails(delete_uids, progress_cb=cb) if choice == "1" else \
                          engine.move_to_trash(delete_uids, trash_folder, progress_cb=cb)

            deleted_results = [r for r in to_delete if r.mail.uid in deleted]
            log_file = save_cleanup_log(
                deleted_results,
                stats,
                cfg.provider.value,
                cfg.mode.value,
                dry_run=False,
                action="delete" if choice == "1" else "trash",
            )

            if not deleted:
                console.print(Panel(
                    "[bold red]İşlem başarısız.[/bold red] Hiçbir mesaj işlenemedi.\n[dim]Sunucu izni veya klasör adı sorunlu olabilir. Log dosyasını inceleyin.[/dim]\n"
                    f"[dim]Log kaydedildi: {log_file}[/dim]", title="[bold red]Hata[/bold red]", border_style="red", box=box.ROUNDED
                ))
            else:
                console.print(Panel(
                    f"[bold green]✔ {len(deleted)} mesaj {'kalıcı olarak silindi' if choice == '1' else 'çöp kutusuna taşındı'}.[/bold green]\n"
                    f"[dim]Log kaydedildi: {log_file}[/dim]", title="[bold green]İşlem Tamamlandı[/bold green]", border_style="green", box=box.ROUNDED
                ))
            _prompt_unsubscribe(to_delete)
        else:
            _prompt_unsubscribe(to_delete)
            console.print("[yellow]İşlem iptal edildi.[/yellow]")

    except KeyboardInterrupt:
        log.warning("Process interrupted by user (KeyboardInterrupt)")
        cancel_event.set()
        console.print("\n[bold yellow]İşlem kullanıcı tarafından durduruldu.[/bold yellow]")
    except Exception as e:
        log.exception("An unexpected error occurred during execution.")
        console.print(f"\n[bold red]Beklenmeyen bir hata oluştu:[/bold red] {e}")
    finally:
        log.info("Cleaning up and exiting MailShift")
        if engine is not None:
            engine.disconnect()

        # Güvenli VRAM ve İşlem Temizliği (Scope hataları giderildi)
        if cfg and cfg.mode == Mode.PRO:
            if cfg.llm_backend == "ollama":
                cleanup_ollama_if_it_was_started_by_us(model_name=cfg.ollama.model)
            elif cfg.llm_backend == "lm_studio":
                unload_lm_studio_models(cfg.lm_studio.base_url, cfg.lm_studio.model)

        close_ollama_session()
        cleanup_lm_studio_if_it_was_started_by_us()

if __name__ == "__main__":
    clear_console()
    main()
