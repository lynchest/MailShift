import getpass
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from rich import box
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

from config import (
    Mode,
    Provider,
    add_to_blacklist,
    add_to_whitelist,
    list_keywords,
    remove_from_blacklist,
    remove_from_whitelist,
)
from ui import console, clear_console


_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")
_CREDENTIALS_FILE = Path(__file__).resolve().parent / "credentials.json"


def _load_saved_credentials() -> dict[str, dict[str, str]]:
    """Load provider-based saved credentials from disk."""
    if not _CREDENTIALS_FILE.exists():
        return {}

    try:
        with open(_CREDENTIALS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        # Corrupt/unreadable file should not block login flow.
        pass
    return {}


def _get_saved_credentials(provider: Provider) -> tuple[str, str] | None:
    saved = _load_saved_credentials()
    provider_data = saved.get(provider.value)
    if not isinstance(provider_data, dict):
        return None

    username = provider_data.get("username")
    password = provider_data.get("password")
    if isinstance(username, str) and isinstance(password, str) and username and password:
        return username, password
    return None


def _save_credentials(provider: Provider, username: str, password: str) -> bool:
    """Persist credentials so user can reuse them in future runs."""
    try:
        data = _load_saved_credentials()
        data[provider.value] = {
            "username": username,
            "password": password,
        }
        with open(_CREDENTIALS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def _is_valid_email(value: str) -> bool:
    return bool(_EMAIL_RE.match(value.strip()))


def _prompt_email(label: str) -> str:
    """Prompt for an e-mail address, looping until a valid one is entered."""
    while True:
        value = Prompt.ask(f"\n[bold cyan]{label}[/bold cyan]").strip()
        if _is_valid_email(value):
            clear_console()
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
    clear_console()
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
    clear_console()
    return host, port, use_ssl.lower() == "y"


def check_ollama_installed() -> bool:
    """Check if Ollama is installed on the system."""
    return shutil.which("ollama") is not None


def install_ollama() -> bool:
    """Download and install Ollama on Windows using PowerShell command."""
    if sys.platform != "win32":
        console.print("[red]Otomatik kurulum şu an sadece Windows sistemlerini destekliyor.[/red]")
        console.print("Lütfen [link]https://ollama.com[/link] adresinden manuel kurun.")
        return False

    console.print("\n[bold yellow]Ollama indiriliyor ve kuruluyor...[/bold yellow]")
    console.print("[dim]Komut: irm https://ollama.com/install.ps1 | iex[/dim]\n")
    
    try:
        # PowerShell command to install Ollama
        process = subprocess.Popen(
            ["powershell", "-Command", "irm https://ollama.com/install.ps1 | iex"],
            stdout=sys.stdout,
            stderr=sys.stderr,
            shell=True
        )
        process.wait()
        
        if process.returncode == 0:
            console.print("\n[bold green]Ollama kurulum komutu başarıyla çalıştırıldı![/bold green]")
            console.print("[yellow]Not: PATH değişikliklerinin geçerli olması için terminali veya uygulamayı yeniden başlatmanız gerekebilir.[/yellow]\n")
            return True
        else:
            console.print(f"\n[bold red]Kurulum sırasında bir hata oluştu (Exit Code: {process.returncode})[/bold red]")
            return False
            
    except Exception as e:
        console.print(f"\n[bold red]Kurulum başlatılamadı:[/bold red] {e}")
        return False


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
            # CREATE_NO_WINDOW (0x08000000) prevents CMD window from popping up
            creation_flags = 0x08000000
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=creation_flags,
                close_fds=True
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
            console.print("[green]Ollama başarıyla başlatıldı! (tamamen kapatmanız için görev yöneticisine bakmalısınız)[/green]")
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


def download_ollama_model(model_name: str, base_url: str = "http://localhost:11434") -> bool:
    """Download an Ollama model with a progress bar."""
    import requests
    
    console.print(f"\n[bold yellow]Model indiriliyor:[/bold yellow] [bold cyan]{model_name}[/bold cyan]")
    
    try:
        with requests.post(f"{base_url}/api/pull", json={"name": model_name}, stream=True) as resp:
            resp.raise_for_status()
            
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TimeElapsedColumn(),
                console=console,
                transient=True,
            ) as progress:
                task = progress.add_task(f"İndiriliyor {model_name}...", total=100)
                
                for line in resp.iter_lines():
                    if line:
                        data = json.loads(line.decode("utf-8"))
                        status = data.get("status", "")
                        
                        if "total" in data and "completed" in data:
                            total = data["total"]
                            completed = data["completed"]
                            # total 0 ise bölme hatasından kaçın
                            if total > 0:
                                percent = (completed / total) * 100
                                progress.update(task, completed=percent, description=f"{status} ({completed/1024/1024:.1f}MB / {total/1024/1024:.1f}MB)")
                        else:
                            progress.update(task, description=status)
                            
                        if status == "success":
                            progress.update(task, completed=100)
                            break
                            
        console.print(f"[bold green]✓ {model_name} başarıyla indirildi![/bold green]")
        return True
    except Exception as e:
        console.print(f"[bold red]✗ Model indirilirken hata oluştu:[/bold red] {e}")
        return False


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
                    "İndirmek için: [link]https://ollama.com[/link]",
                    title="[bold red]Ollama Gerekli[/bold red]",
                    border_style="red",
                    box=box.ROUNDED,
                ))

                if sys.platform == "win32":
                    should_install = Confirm.ask(
                        "\n[bold yellow]Ollama şimdi otomatik kurulsun mu?[/bold yellow]\n"
                        "[dim](Komut: irm https://ollama.com/install.ps1 | iex)[/dim]",
                        default=True
                    )
                    
                    if should_install:
                        if install_ollama():
                            console.print("\n[bold green]Ollama kuruldu. Uygulamayı yeniden başlatmanız önerilir.[/bold green]")
                            # Try to wait a bit then start
                            time.sleep(2)
                            if start_ollama():
                                available_models = get_ollama_models()
                                if available_models:
                                    # Fallthrough to model selection
                                    pass
                                else:
                                    console.print("[cyan]Model bulunamadı. Lütfen daha sonra manuel kurun.[/cyan]")
                                    return Mode.FAST, "qwen3.5:2B"
                            else:
                                console.print("[cyan]Ollama kuruldu ama başlatılamadı. Lütfen uygulamayı yeniden açın.[/cyan]")
                                return Mode.FAST, "qwen3.5:2B"
                        else:
                            console.print("[cyan]Kurulum başarısız oldu. Manuel kurmanız gerekebilir.[/cyan]")
                            return Mode.FAST, "qwen3.5:2B"
                    else:
                        console.print("[cyan]Fast moduna geçiliyor...[/cyan]")
                        return Mode.FAST, "qwen3.5:2B"
                else:
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
                console.print("  terminal/cmd => ollama serve (tamamen kapatmanız için görev yöneticisine bakmalısınız)")
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
        
        recommended_models = ["qwen3.5:2B", "qwen3.5:4B"]
        
        for rec_model in recommended_models:
            is_available = any(rec_model.lower() == m.lower() for m in available_models)
            status = "[bold green]Mevcut[/bold green]" if is_available else "[bold red]Mevcut Değil[/bold red]"
            mt.add_row("[1]" if rec_model == "qwen3.5:2B" else "[2]", rec_model, status + " (Recommended)")
        
        non_rec_models = [m for m in available_models if not any(m.lower() == r.lower() for r in recommended_models)]
        
        for idx, model in enumerate(non_rec_models, start=len(recommended_models) + 1):
            mt.add_row(f"[{idx}]", model, "")
        
        console.print(Panel(mt, title="[bold cyan]Ollama Model[/bold cyan]", border_style="cyan", box=box.ROUNDED))

        choices = ["1", "2"] + [str(i) for i in range(len(recommended_models) + 1, len(non_rec_models) + len(recommended_models) + 1)]
        default_choice = "1"
        model_choice = Prompt.ask("[bold]Model[/bold]", choices=choices, default=default_choice)
        
        if model_choice == "1":
            model = "qwen3.5:2B"
        elif model_choice == "2":
            model = "qwen3.5:4B"
        else:
            idx = int(model_choice) - len(recommended_models) - 1
            model = non_rec_models[idx]
        
        # Eğer seçilen model önerilen modellerden biriyse ve sistemde yoksa indir
        if model in recommended_models and not any(model.lower() == m.lower() for m in available_models):
            download_ollama_model(model)
            
        return mode, model
    
    return mode, "qwen3.5:2B"


def prompt_credentials(
    provider: Provider,
    preset_username: Optional[str] = None,
    preset_password: Optional[str] = None,
) -> tuple[str, str]:
    if not preset_username or not preset_password:
        saved_creds = _get_saved_credentials(provider)
        if saved_creds:
            saved_username, saved_password = saved_creds
            if Confirm.ask(
                f"[bold]Kayıtlı bilgiler bulundu ({saved_username}). Önceki bilgileri kullanılsın mı?[/bold]",
                default=True,
            ):
                clear_console()
                return saved_username, saved_password

    if provider == Provider.GMAIL:
        username = preset_username.strip() if preset_username else _prompt_email("Gmail adresi")

        pt = Table(box=None, show_header=False, padding=(0, 2))
        pt.add_column(style="bold yellow", width=4)
        pt.add_column(style="dim")
        pt.add_row("[1]", "App Password'üm var, girebilirim")
        pt.add_row("[2]", "App Password oluşturmam gerekiyor (yönlendir)")
        console.print(Panel(pt, title="[bold cyan]Gmail Şifre Durumu[/bold cyan]", border_style="cyan", box=box.ROUNDED))
        choice = Prompt.ask("[bold]Seçim[/bold]", choices=["1", "2"], default="1")
        clear_console()

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
            clear_console()

    elif provider == Provider.PROTON:
        username = preset_username.strip() if preset_username else _prompt_email("Proton Bridge e-posta adresi")
    else:
        username = preset_username.strip() if preset_username else Prompt.ask(f"\n[bold cyan]IMAP Kullanıcı Adı[/bold cyan]").strip()

    password = preset_password if preset_password else getpass.getpass("Şifre (App Password / Bridge şifresi): ")

    if Confirm.ask("[bold]Bu bilgileri sonraki çalıştırmalar için kaydedeyim mi?[/bold]", default=True):
        if _save_credentials(provider, username, password):
            console.print("[green]Kimlik bilgileri kaydedildi.[/green]")
        else:
            console.print("[yellow]Kimlik bilgileri kaydedilemedi.[/yellow]")

    clear_console()
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
