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
import time
import unicodedata
from typing import Optional

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    import os
    os.environ["TERM"] = "xterm-256color"

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
    TimeRemainingColumn,
)
from rich.prompt import Confirm, Prompt

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
)
from database import save_mails_cache, load_mails_cache
from pro_analyzer import check_ollama_health

from ui import (
    console,
    print_banner,
    build_results_table,
    build_stats_panel,
    clear_console,
)
from history import save_cleanup_log, export_scan_results, print_history
from logger import log
from cli_utils import (
    handle_keywords,
    handle_uninstall,
    prompt_credentials,
    prompt_custom_imap_settings,
    prompt_mode,
    prompt_provider,
)

def clean_text(text: Optional[str], max_len: int = 35) -> str:
    """Normalize progress labels to avoid wrapped/duplicated-looking bars on narrow terminals."""
    if not text:
        return "(bilinmiyor)"

    # Normalize accented forms and strip control/format characters that can break terminal rendering.
    normalized = unicodedata.normalize("NFKC", text)
    normalized = re.sub(r"[\x00-\x1f\x7f]", " ", normalized)
    normalized = "".join(
        ch for ch in normalized
        if unicodedata.category(ch) not in {"Mn", "Me", "Cf", "Cs"}
    )

    cleaned = " ".join(normalized.split())
    if not cleaned:
        cleaned = "(bilinmiyor)"
    return cleaned[:max_len] + ("…" if len(cleaned) > max_len else "")


def format_duration(seconds: float) -> str:
    """Format a duration in seconds as a short human-readable label."""
    total = max(0, int(round(seconds)))
    minutes, sec = divmod(total, 60)
    if minutes:
        return f"~{minutes} dk {sec:02d} sn"
    return f"~{sec} sn"

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
@click.option("--ollama-url", default="http://localhost:11434", show_default=True, help="Ollama API base URL.")
@click.option("--ollama-model", default="qwen3.5:2B", show_default=True, help="Ollama model name.")
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
    provider: Optional[str], mode: Optional[str], username: Optional[str],
    password: Optional[str], host: Optional[str], port: Optional[int],
    use_ssl: bool, dry_run: bool, scan_limit: Optional[int],
    ollama_url: str, ollama_model: str, ollama_prompt: Optional[str],
    uninstall: bool, history: bool, add_whitelist: Optional[str],
    remove_whitelist: Optional[str], add_blacklist: Optional[str],
    remove_blacklist: Optional[str], list_keywords_flag: bool,
    export_file: Optional[str],
) -> None:
    """MailShift – privacy-first newsletter purger for Gmail and Proton Mail."""
    log.info("Starting MailShift CLI")

    if history:
        print_history()
        return

    # Anahtar kelime yöneticisi
    if handle_keywords(list_keywords_flag, add_whitelist, remove_whitelist, add_blacklist, remove_blacklist):
        return

    if uninstall:
        handle_uninstall()
        return

    # ---- resolve provider / mode (interactive if not provided) ----
    resolved_provider = Provider(provider) if provider else prompt_provider()
    resolved_mode, selected_model = (Mode(mode), ollama_model) if mode else prompt_mode()

    # ---- credentials ----
    if not username or not password:
        username, password = prompt_credentials(
            resolved_provider,
            preset_username=username,
            preset_password=password,
        )

    # ---- custom IMAP settings ----
    custom_host = host
    custom_port = port
    custom_use_ssl = use_ssl if resolved_provider == Provider.CUSTOM else None
    
    if resolved_provider == Provider.CUSTOM and not custom_host:
        custom_host, custom_port, custom_use_ssl = prompt_custom_imap_settings()

    # ---- build config ----
    imap_cfg = build_imap_config(
        resolved_provider, username, password,
        host=custom_host, port=custom_port, use_ssl=custom_use_ssl,
    )
    
    selected_ollama_model = selected_model if not mode else ollama_model
    ollama_cfg = OllamaConfig(
        base_url=ollama_url,
        model=selected_ollama_model,
        system_prompt=ollama_prompt if ollama_prompt else DEFAULT_SYSTEM_PROMPT,
    )
    
    resolved_workers = calculate_optimal_workers(selected_ollama_model, resolved_mode.value)

    cfg = AppConfig(
        provider=resolved_provider, mode=resolved_mode, imap=imap_cfg,
        ollama=ollama_cfg, dry_run=dry_run,
        scan_limit=scan_limit,
    )

    clear_console()

    # ---- summary of what we're about to do ----
    sys_info = get_system_info()
    model_display = (
        f"{resolved_mode.value.capitalize()} ({selected_ollama_model})"
        if resolved_mode == Mode.PRO else "Fast (heuristic)"
    )
    hardware_info = format_system_info(sys_info, resolved_mode.value, selected_ollama_model)
    
    console.print(
        Panel(
            f"[bold]Provider:[/bold] {resolved_provider.value.capitalize()}\n"
            f"[bold]Mode:[/bold]     {model_display}\n"
            f"[bold]User:[/bold]     {username}\n"
            f"[bold]Workers:[/bold]  {resolved_workers} [dim](auto)[/dim]\n"
            f"{hardware_info}\n"
            f"[bold]Dry run:[/bold]  {'[yellow]Yes – no emails will be deleted[/yellow]' if dry_run else '[red]No – emails WILL be deleted[/red]'}",
            title="[bold blue]Configuration[/bold blue]",
            border_style="blue",
            box=box.ROUNDED,
        )
    )

    # ---- Ollama health check (Pro mode only) ----
    if resolved_mode == Mode.PRO:
        with console.status("[cyan]Ollama bağlantısı kontrol ediliyor…[/cyan]", spinner="dots"):
            ok, msg = check_ollama_health(ollama_cfg.base_url, selected_ollama_model)
        if ok:
            console.print(f"[green]✓ {msg}[/green]")
        else:
            console.print(f"[bold red]✗ {msg}[/bold red]")
            if not Confirm.ask("[yellow]Ollama olmadan devam etmek ister misiniz? (Fast mode'a geçilecek)[/yellow]", default=False):
                sys.exit(1)
            resolved_mode = Mode.FAST
            cfg = AppConfig(
                provider=resolved_provider, mode=resolved_mode, imap=imap_cfg,
                ollama=ollama_cfg, dry_run=dry_run, scan_limit=scan_limit,
            )
            console.print("[yellow]Fast mode'a geçildi.[/yellow]")

    # Define engine locally to handle UnboundLocalError in finally block
    engine = None
    try:
        # ---- connect ----
        with console.status("[cyan]Connecting to IMAP server…[/cyan]", spinner="dots"):
            try:
                engine = MailEngine(cfg)
                engine.connect()
            except Exception as exc:
                console.print(f"[bold red]Connection failed:[/bold red] {exc}")
                sys.exit(1)

        # ---- check cache & list UIDs ----
        cached_mails = load_mails_cache() or []
        cached_dict = {m.uid: m for m in cached_mails}
        
        with console.status("[cyan]Listing messages…[/cyan]", spinner="dots"):
            current_uids = engine.list_uids()

        if not current_uids:
            console.print("[yellow]No messages found in INBOX.[/yellow]")
            return

        console.print(f"[green]Found [bold]{len(current_uids)}[/bold] message(s) in INBOX.[/green]")
        
        # Partition UIDs: those in cache vs newly arrived
        missing_uids = [uid for uid in current_uids if uid not in cached_dict]
        mails: list[MailMeta] = [cached_dict[uid] for uid in current_uids if uid in cached_dict]
        
        if mails:
            console.print(f"[cyan]Cache'den [bold]{len(mails)}[/bold] mail başlığı yüklendi.[/cyan]")
            
        # ---- fetch new headers ----
        if missing_uids:
            console.print(f"[cyan]Suncudan [bold]{len(missing_uids)}[/bold] yeni ileti başlığı çekiliyor…[/cyan]")
            with Progress(
                SpinnerColumn(), TextColumn("[bold cyan]Fetching Headers[/bold cyan]"),
                BarColumn(), TaskProgressColumn(),
                TextColumn("[dim]{task.fields[current]}[/dim]"),
                TimeElapsedColumn(), console=console, transient=True,
                redirect_stdout=True, redirect_stderr=True,
            ) as progress:
                task = progress.add_task("fetch", total=len(missing_uids), current="Starting…")

                def _on_fetch(meta: MailMeta) -> None:
                    mails.append(meta)  # append new mail to our total list
                    # DÜZELTME: Satır sonu karakterlerinden arındırma
                    progress.update(
                        task, advance=1,
                        current=clean_text(meta.sender, max_len=24)
                    )

                engine.fetch_headers_concurrent(missing_uids, progress_cb=_on_fetch)
            console.print(f"[green]Yeni başlıklar eklendi.[/green]")

        # ---- fetch bodies for Pro Mode if missing ----
        if resolved_mode == Mode.PRO:
            need_body = [m for m in mails if not m.body_preview]
            if need_body:
                console.print(f"[cyan]Pro mode: [bold]{len(need_body)}[/bold] iletinin içeriği (body) çekiliyor…[/cyan]")
                with Progress(
                    SpinnerColumn(), TextColumn("[bold cyan]Body Fetching[/bold cyan]"),
                    BarColumn(), TaskProgressColumn(),
                    TextColumn("[dim]{task.fields[current]}[/dim]"),
                    TimeElapsedColumn(), console=console, transient=True,
                    redirect_stdout=True, redirect_stderr=True,
                ) as progress:
                    task = progress.add_task("body_fetch", total=len(need_body), current="Starting…")

                    def _on_body(meta: MailMeta) -> None:
                        # DÜZELTME: Satır sonu karakterlerinden arındırma
                        progress.update(
                            task, advance=1,
                            current=clean_text(meta.sender, max_len=24)
                        )

                    engine.fetch_body_for_cached_mails(need_body, progress_cb=_on_body)
                console.print(f"[green]İleti içerikleri güncellendi.[/green]")

        # Save merged results back to cache
        save_mails_cache(mails)

        # ---- analyze (2-phase for Pro mode) ----
        scan_results: list[ScanResult] = []
        stats: Optional[ScanStats] = None

        if resolved_mode == Mode.PRO:
            # ── Phase 1: Fast heuristic scan ──
            from fast_analyzer import fast_analyze

            fast_results: list[ScanResult] = []
            with Progress(
                SpinnerColumn(), TextColumn("[bold cyan]Phase 1 – Heuristic Scan[/bold cyan]"),
                BarColumn(), TaskProgressColumn(),
                TextColumn("[dim]{task.fields[current]}[/dim]"),
                TimeElapsedColumn(), TimeRemainingColumn(), console=console, transient=True,
                redirect_stdout=True, redirect_stderr=True,
            ) as progress:
                task = progress.add_task("fast", total=len(mails), current="Starting…")
                for mail in mails:
                    res = fast_analyze(mail)
                    fast_results.append(res)
                    icon = "SIL" if res.decision == "SIL" else "TUT"
                    
                    # DÜZELTME: Satır sonu karakterlerinden arındırma
                    subj = clean_text(mail.subject, max_len=24)
                    progress.update(task, advance=1, current=f"{icon} {subj}")

            sil_candidates = [r for r in fast_results if r.decision == "SIL"]
            tut_results = [r for r in fast_results if r.decision == "TUT"]

            console.print(
                f"[cyan]Phase 1 tamamlandı: "
                f"[bold]{len(sil_candidates)}[/bold] şüpheli, "
                f"[bold]{len(tut_results)}[/bold] güvenli.[/cyan]"
            )

            # ── Phase 2: LLM verification on SIL candidates ──
            if sil_candidates:
                from pro_analyzer import pro_analyze

                llm_verified: list[ScanResult] = []
                max_workers = calculate_optimal_workers(selected_ollama_model, resolved_mode.value)
                console.print(
                    "[dim]Pro mode dinamik tahmin: ilk sonuçlardan sonra kalan süre "
                    "otomatik hesaplanır.[/dim]"
                )

                with Progress(
                    SpinnerColumn(), TextColumn("[bold magenta]Phase 2 – LLM Verification[/bold magenta]"),
                    BarColumn(), TaskProgressColumn(),
                    TextColumn("[dim]{task.fields[current]}[/dim]"),
                    TimeElapsedColumn(), TimeRemainingColumn(), console=console, transient=True,
                    redirect_stdout=True, redirect_stderr=True,
                ) as progress:
                    task = progress.add_task("llm", total=len(sil_candidates), current="Starting…")
                    phase2_start = time.perf_counter()
                    phase2_done = 0
                    phase2_total = len(sil_candidates)

                    from concurrent.futures import ThreadPoolExecutor, as_completed

                    def _llm_worker(idx_result):
                        idx, candidate = idx_result
                        return idx, pro_analyze(candidate.mail, cfg.ollama)

                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        futures = {
                            executor.submit(_llm_worker, (i, c)): i
                            for i, c in enumerate(sil_candidates)
                        }
                        temp = [None] * len(sil_candidates)
                        for future in as_completed(futures):
                            try:
                                idx, res = future.result()
                                temp[idx] = res
                                icon = "SIL" if res.decision == "SIL" else "TUT"
                                
                                # DÜZELTME: Satır sonu karakterlerinden arındırma
                                subj = clean_text(res.mail.subject, max_len=24)
                                phase2_done += 1
                                elapsed = max(0.001, time.perf_counter() - phase2_start)
                                avg_per_item = elapsed / phase2_done
                                remaining = max(0.0, (phase2_total - phase2_done) * avg_per_item)
                                progress.update(
                                    task,
                                    advance=1,
                                    current=f"{icon} {subj} | kalan {format_duration(remaining)}",
                                )
                            except Exception as exc:
                                i = futures[future]
                                temp[i] = ScanResult(
                                    mail=sil_candidates[i].mail,
                                    decision="TUT",
                                    reason=f"llm-error:{exc}",
                                )
                                phase2_done += 1
                                elapsed = max(0.001, time.perf_counter() - phase2_start)
                                avg_per_item = elapsed / phase2_done
                                remaining = max(0.0, (phase2_total - phase2_done) * avg_per_item)
                                progress.update(
                                    task,
                                    advance=1,
                                    current=f"⚠️ hata | kalan {format_duration(remaining)}",
                                )

                    llm_verified = [r for r in temp if r is not None]

                llm_confirmed = sum(1 for r in llm_verified if r.decision == "SIL")
                llm_saved = len(llm_verified) - llm_confirmed
                console.print(
                    f"[magenta]Phase 2 tamamlandı: "
                    f"[bold]{llm_confirmed}[/bold] silme onaylandı, "
                    f"[bold]{llm_saved}[/bold] kurtarıldı.[/magenta]"
                )

                # Combine TUT from fast + LLM results
                scan_results = tut_results + llm_verified
            else:
                scan_results = fast_results

            # Build stats
            from threading import Lock
            stats = ScanStats()
            for r in scan_results:
                stats.total_scanned += 1
                stats.total_size_bytes += r.mail.size_bytes
                if r.decision == "SIL":
                    stats.marked_for_deletion += 1
                    stats.marked_size_bytes += r.mail.size_bytes

        else:
            # ── Fast mode: single-phase analysis ──
            with Progress(
                SpinnerColumn(), TextColumn("[bold cyan]Analyzing[/bold cyan]"),
                BarColumn(), TaskProgressColumn(), TextColumn("[dim]{task.fields[current]}[/dim]"),
                TimeElapsedColumn(), console=console, transient=True,
                redirect_stdout=True, redirect_stderr=True,
            ) as progress:
                task = progress.add_task("analyze", total=len(mails), current="Starting…")

                def _on_analyze(result: ScanResult) -> None:
                    scan_results.append(result)
                    icon = "SIL" if result.decision == "SIL" else "TUT"
                    
                    # DÜZELTME: Satır sonu karakterlerinden arındırma
                    subj = clean_text(result.mail.subject, max_len=24)
                    progress.update(task, advance=1, current=f"{icon} {subj}")

                _, stats = engine.analyze(mails, progress_cb=_on_analyze)

        # ---- display results ----
        to_delete = [r for r in scan_results if r.decision == "SIL"]

        if not to_delete:
            console.print(
                Panel(
                    "[green]No junk messages detected. Your inbox looks clean! 🎉[/green]",
                    border_style="green", box=box.ROUNDED,
                )
            )
        else:
            console.print(build_results_table(to_delete))

        console.print(build_stats_panel(stats, dry_run))

        if export_file:
            export_scan_results(to_delete, export_file)

        # ---- confirmation + action ----
        _TRASH_FOLDER = {
            Provider.GMAIL: "[Gmail]/Trash",
            Provider.PROTON: "Trash",
            Provider.CUSTOM: "Trash",
        }.get(resolved_provider, "Trash")

        if to_delete:
            if dry_run:
                console.print(
                    Panel(
                        f"[bold]{len(to_delete)}[/bold] mesaj silinebilir olarak işaretlendi.\n"
                        "[dim]Dry run modu – henüz hiçbir şey silinmedi.[/dim]",
                        title="[bold yellow]Dry Run Tamamlandı[/bold yellow]",
                        border_style="yellow",
                        box=box.ROUNDED,
                    )
                )

            console.print(
                "\n  [bold cyan][1][/bold cyan] Kalıcı Sil  [dim](geri alınamaz)[/dim]\n"
                "  [bold cyan][2][/bold cyan] Çöp Kutusuna Gönder\n"
                "  [bold cyan][3][/bold cyan] İptal\n"
            )
            choice = Prompt.ask(
                f"[bold]{len(to_delete)} mesaj için ne yapmak istersiniz?[/bold]",
                choices=["1", "2", "3"],
                default="3",
            )
            clear_console()

            if choice in ("1", "2"):
                delete_uids = [r.mail.uid for r in to_delete]
                action_label = "Kalıcı Siliniyor" if choice == "1" else "Çöp Kutusuna Taşınıyor"
                with Progress(
                    SpinnerColumn(), TextColumn(f"[bold red]{action_label}[/bold red]"),
                    BarColumn(), TaskProgressColumn(), TimeElapsedColumn(),
                    console=console, transient=True,
                    redirect_stdout=True, redirect_stderr=True,
                ) as progress:
                    del_task = progress.add_task("action", total=len(delete_uids))
                    if choice == "1":
                        deleted = engine.delete_mails(
                            delete_uids, progress_cb=lambda _uid: progress.advance(del_task),
                        )
                    else:
                        deleted = engine.move_to_trash(
                            delete_uids, _TRASH_FOLDER,
                            progress_cb=lambda _uid: progress.advance(del_task),
                        )

                deleted_results = [r for r in to_delete if r.mail.uid in deleted]
                log_file = save_cleanup_log(
                    deleted_results, stats,
                    resolved_provider.value, resolved_mode.value,
                )

                if not deleted:
                    console.print(
                        Panel(
                            "[bold red]İşlem başarısız.[/bold red] Hiçbir mesaj işlenemedi.\n"
                            "[dim]Sunucu izni veya klasör adı sorunlu olabilir. Log dosyasını inceleyin.[/dim]\n"
                            f"[dim]Log kaydedildi: {log_file}[/dim]",
                            title="[bold red]Hata[/bold red]",
                            border_style="red",
                            box=box.ROUNDED,
                        )
                    )
                else:
                    action_done = "kalıcı olarak silindi" if choice == "1" else "çöp kutusuna taşındı"
                    console.print(
                        Panel(
                            f"[bold green]✓ {len(deleted)} mesaj {action_done}.[/bold green]\n"
                            f"[dim]Log kaydedildi: {log_file}[/dim]",
                            title="[bold green]İşlem Tamamlandı[/bold green]",
                            border_style="green",
                            box=box.ROUNDED,
                        )
                    )
            else:
                console.print("[yellow]İşlem iptal edildi.[/yellow]")

    except KeyboardInterrupt:
        log.warning("Process interrupted by user (KeyboardInterrupt)")
        console.print("\n[bold yellow]İşlem kullanıcı tarafından durduruldu.[/bold yellow]")
    except Exception as e:
        log.exception("An unexpected error occurred during execution.")
        console.print(f"\n[bold red]Beklenmeyen bir hata oluştu:[/bold red] {e}")
    finally:
        log.info("Cleaning up and exiting MailShift")
        # Engine referansı varsa her durumda güvenli bir şekilde kapat
        if engine is not None:
            engine.disconnect()


if __name__ == "__main__":
    clear_console()
    main()