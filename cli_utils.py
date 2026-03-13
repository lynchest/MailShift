import getpass
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from rich import box
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from config import (
    Mode,
    Provider,
    add_to_blacklist,
    add_to_whitelist,
    list_keywords,
    remove_from_blacklist,
    remove_from_whitelist,
)
from ui import console


_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


def _is_valid_email(value: str) -> bool:
    return bool(_EMAIL_RE.match(value.strip()))


def _prompt_email(label: str) -> str:
    """Prompt for an e-mail address, looping until a valid one is entered."""
    while True:
        value = Prompt.ask(f"\n[bold cyan]{label}[/bold cyan]").strip()
        if _is_valid_email(value):
            return value
        console.print(
            f"[bold red]✗ Geçersiz e-posta adresi:[/bold red] [yellow]{value}[/yellow]\n"
            "  Lütfen geçerli bir e-posta adresi girin (örn. [dim]kullanici@gmail.com[/dim])."
        )


def prompt_provider() -> Provider:
    t = Table(box=None, show_header=False, padding=(0, 2))
    t.add_column(style="bold yellow", width=4)
    t.add_column(style="bold", width=8)
    t.add_column(style="dim")
    t.add_row("[1]", "Gmail", "IMAP over SSL – App Password required")
    t.add_row("[2]", "Proton", "via Proton Bridge at 127.0.0.1")
    t.add_row("[3]", "Custom", "your own IMAP server")
    console.print(Panel(t, title="[bold cyan]Mail Provider[/bold cyan]", border_style="cyan", box=box.ROUNDED))
    choice = Prompt.ask("[bold]Provider[/bold]", choices=["1", "2", "3"], default="1")
    if choice == "1":
        return Provider.GMAIL
    elif choice == "2":
        return Provider.PROTON
    return Provider.CUSTOM


def prompt_custom_imap_settings() -> tuple[str, int, bool]:
    """Prompt for custom IMAP server settings."""
    while True:
        host = Prompt.ask("\n[bold cyan]IMAP Host[/bold cyan] (örn. imap.example.com)").strip()
        if host:
            break
        console.print("[bold red]✗ Host boş bırakılamaz.[/bold red]")

    while True:
        raw_port = Prompt.ask("[bold cyan]IMAP Port[/bold cyan]", default="993").strip()
        if raw_port.isdigit() and 1 <= int(raw_port) <= 65535:
            port = int(raw_port)
            break
        console.print(
            f"[bold red]✗ Geçersiz port:[/bold red] [yellow]{raw_port}[/yellow]\n"
            "  Port 1–65535 arasında bir sayı olmalıdır."
        )

    use_ssl = Prompt.ask("[bold cyan]SSL kullanılsın mı?[/bold cyan]", choices=["y", "n"], default="y")
    return host, port, use_ssl.lower() == "y"


def check_ollama_installed() -> bool:
    """Check if Ollama is installed on the system."""
    return shutil.which("ollama") is not None


def start_ollama(max_retries: int = 3, retry_delay: int = 3) -> bool:
    """
    Try to start Ollama in the background.
    Returns True if Ollama is now running, False otherwise.
    """
    console.print("\n[yellow]Ollama çalışmıyor, başlatılmaya çalışılıyor...[/yellow]")

    if not check_ollama_installed():
        return False

    if sys.platform == "win32":
        try:
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                startinfo=subprocess.STARTUPINFO(dwFlags=subprocess.STARTF_USESHOWWINDOW, wShowWindow=subprocess.SW_HIDE),
            )
        except Exception:
            try:
                subprocess.Popen(
                    ["cmd", "/c", "start", "", "ollama", "serve"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
                )
            except Exception:
                return False
    else:
        try:
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception:
            return False

    for attempt in range(max_retries):
        time.sleep(retry_delay)
        if get_ollama_models():
            console.print("[green]Ollama başarıyla başlatıldı![/green]")
            return True

    return False


def get_ollama_models(base_url: str = "http://localhost:11434", timeout: int = 5) -> list[str]:
    """Get available models from Ollama API."""
    try:
        import requests
        resp = requests.get(f"{base_url}/api/tags", timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def prompt_mode() -> tuple[Mode, str]:
    t = Table(box=None, show_header=False, padding=(0, 2))
    t.add_column(style="bold yellow", width=4)
    t.add_column(style="bold", width=6)
    t.add_column(style="dim")
    t.add_row("[1]", "Fast", "Heuristic keyword matching – instant, no AI [yellow](Less accurate)[/yellow]")
    t.add_row("[2]", "Pro", "Heuristic + local Ollama LLM – more accurate [bold green](Recommended)[/bold green]")
    console.print(Panel(t, title="[bold cyan]Scan Mode[/bold cyan]", border_style="cyan", box=box.ROUNDED))
    choice = Prompt.ask("[bold]Mode[/bold]", choices=["1", "2"], default="1")
    mode = Mode.FAST if choice == "1" else Mode.PRO
    
    if mode == Mode.PRO:
        console.print("\n[bold cyan]Select Ollama model:[/bold cyan]")

        available_models = get_ollama_models()

        if not available_models:
            if not check_ollama_installed():
                console.print(Panel(
                    "[bold yellow]Ollama sistemde bulunamadı![/bold yellow]\n\n"
                    "Pro modu kullanmak için Ollama'yı yüklemeniz gerekiyor.\n\n"
                    "İndirmek için: [link]https://ollama.com[/link]\n\n"
                    "Kurulumdan sonra uygulamayı yeniden başlatın.",
                    title="[bold red]Ollama Gerekli[/bold red]",
                    border_style="red",
                    box=box.ROUNDED,
                ))
                console.print("[cyan]Fast moduna geçiliyor...[/cyan]")
                return Mode.FAST, "qwen3.5:2B"

            console.print("[yellow]Ollama çalışmıyor veya model bulunamadı.[/yellow]")

            auto_start = Confirm.ask(
                "\n[bold]Ollama'yı otomatik başlatmak ister misiniz?[/bold]",
                default=True
            )

            if auto_start:
                if start_ollama():
                    available_models = get_ollama_models()
                else:
                    console.print("[red]Ollama başlatılamadı.[/red]")
            else:
                console.print("\n[bold cyan]Ollama'yı manuel olarak başlatın:[/bold cyan]")
                console.print("  terminal/cmd => ollama serve")
                console.print("\n[cyan]Model listesini yeniden almak için Enter'a basın...[/cyan]")
                Prompt.ask("")
                available_models = get_ollama_models()

                if not available_models:
                    console.print("[red]Hâlâ model alınamıyor. Fast moduna geçiliyor.[/red]")
                    return Mode.FAST, "qwen3.5:2B"

        mt = Table(box=None, show_header=False, padding=(0, 2))
        mt.add_column(style="bold yellow", width=4)
        mt.add_column(style="bold")
        mt.add_column(style="dim")
        mt.add_row("[1]", "qwen3.5:2B", "[bold green](Recommended)[/bold green]")
        for idx, model in enumerate(available_models, start=2):
            mt.add_row(f"[{idx}]", model, "")
        console.print(Panel(mt, title="[bold cyan]Ollama Model[/bold cyan]", border_style="cyan", box=box.ROUNDED))

        choices = ["1"] + [str(i) for i in range(2, len(available_models) + 2)] if available_models else ["1"]
        model_choice = Prompt.ask("[bold]Model[/bold]", choices=choices, default="1")
        
        if model_choice == "1":
            model = "qwen3.5:2B"
        else:
            idx = int(model_choice) - 2
            model = available_models[idx]
        return mode, model
    
    return mode, "qwen3.5:2B"


def prompt_credentials(provider: Provider) -> tuple[str, str]:
    if provider == Provider.GMAIL:
        username = _prompt_email("Gmail adresi")

        pt = Table(box=None, show_header=False, padding=(0, 2))
        pt.add_column(style="bold yellow", width=4)
        pt.add_column(style="dim")
        pt.add_row("[1]", "App Password'üm var, girebilirim")
        pt.add_row("[2]", "App Password oluşturmam gerekiyor (yönlendir)")
        console.print(Panel(pt, title="[bold cyan]Gmail Şifre Durumu[/bold cyan]", border_style="cyan", box=box.ROUNDED))
        choice = Prompt.ask("[bold]Seçim[/bold]", choices=["1", "2"], default="1")

        if choice == "2":
            console.print(Panel(
                "[bold]Adım 1:[/bold] https://myaccount.google.com/apppasswords adresini açın\n"
                "[bold]Adım 2:[/bold] 'Uygulama şifresi oluştur' seçeneğine tıklayın\n"
                "[bold]Adım 3:[/bold] 'Mail' uygulamasını seçin ve oluşturulan 16 haneli şifreyi kopyalayın",
                title="[bold yellow]Gmail App Password[/bold yellow]",
                border_style="yellow",
                box=box.ROUNDED,
            ))
            Prompt.ask("[dim]Devam etmek için Enter'a basın[/dim]")

    elif provider == Provider.PROTON:
        username = _prompt_email("Proton Bridge e-posta adresi")
    else:
        username = Prompt.ask(f"\n[bold cyan]IMAP Kullanıcı Adı[/bold cyan]").strip()

    password = getpass.getpass("Şifre (App Password / Bridge şifresi): ")
    return username, password


def handle_keywords(
    list_flag: bool, add_w: Optional[str], rm_w: Optional[str], 
    add_b: Optional[str], rm_b: Optional[str]
) -> bool:
    """Handles keyword configuration. Returns True if any action was performed."""
    handled = False
    if list_flag:
        whitelist, blacklist = list_keywords()
        console.print(Panel(
            f"[bold cyan]Whitelist ({len(whitelist)} keywords):[/bold cyan]\n" +
            (", ".join(whitelist) if whitelist else "[dim]Empty[/dim]"),
            title="[bold green]Keywords[/bold green]",
            border_style="green",
            box=box.ROUNDED,
        ))
        console.print(Panel(
            f"[bold red]Blacklist ({len(blacklist)} keywords):[/bold red]\n" +
            (", ".join(blacklist) if blacklist else "[dim]Empty[/dim]"),
            border_style="red",
            box=box.ROUNDED,
        ))
        handled = True

    if add_w:
        if add_to_whitelist(add_w):
            console.print(f"[green]Added '{add_w}' to whitelist.[/green]")
        else:
            console.print(f"[yellow]'{add_w}' already exists in whitelist.[/yellow]")
        handled = True

    if rm_w:
        if remove_from_whitelist(rm_w):
            console.print(f"[green]Removed '{rm_w}' from whitelist.[/green]")
        else:
            console.print(f"[yellow]'{rm_w}' not found in whitelist.[/yellow]")
        handled = True

    if add_b:
        if add_to_blacklist(add_b):
            console.print(f"[green]Added '{add_b}' to blacklist.[/green]")
        else:
            console.print(f"[yellow]'{add_b}' already exists in blacklist.[/yellow]")
        handled = True

    if rm_b:
        if remove_from_blacklist(rm_b):
            console.print(f"[green]Removed '{rm_b}' from blacklist.[/green]")
        else:
            console.print(f"[yellow]'{rm_b}' not found in blacklist.[/yellow]")
        handled = True

    return handled


def handle_uninstall() -> None:
    """Handles the complete uninstallation of MailShift."""
    console.print(Panel(
        "[bold red]MailShift Tam Kaldırma[/bold red]",
        border_style="red",
        box=box.ROUNDED,
    ))
    
    confirm = Confirm.ask(
        "\n[bold yellow]Bu işlem MailShift'i ve tüm verilerini siler:[/bold yellow]\n"
        "  • Python paketleri (sadece proje dizinindekiler)\n"
        "  • Önbellek dosyaları (.db cache)\n"
        "  • Python __pycache__ klasörleri\n"
        "  • Proje dosyaları\n\n"
        "[bold]Devam?[/bold]",
        default=False,
    )
    
    if not confirm:
        console.print("[yellow]İşlem iptal edildi.[/yellow]")
        return
    
    # Needs absolute pathing
    import main
    base_path = Path(main.__file__).resolve().parent
    console.print("\n[cyan]Kaldırma işlemi başlıyor...[/cyan]\n")
    
    items_removed = []
    
    cache_files = ["whitelist.json", "blacklist.json", "mailshift.db"]
    for f in cache_files:
        fp = base_path / f
        if fp.exists():
            fp.unlink()
            items_removed.append(f"  • {f}")
    
    pycache = base_path / "__pycache__"
    if pycache.exists() and pycache.is_dir():
        shutil.rmtree(pycache)
        items_removed.append("  • __pycache__/")
    
    git_dir = base_path / ".git"
    if git_dir.exists() and git_dir.is_dir():
        shutil.rmtree(git_dir)
        items_removed.append("  • .git/")
    
    for pf in base_path.glob("*.py"):
        try:
            pf.unlink()
            items_removed.append(f"  • {pf.name}")
        except Exception:
            pass
    
    other_files = ["requirements.txt", "README.md", "LICENSE", ".gitignore"]
    for of in other_files:
        fp = base_path / of
        if fp.exists():
            try:
                fp.unlink()
                items_removed.append(f"  • {of}")
            except Exception:
                pass
    
    tests_dir = base_path / "tests"
    if tests_dir.exists() and tests_dir.is_dir():
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
