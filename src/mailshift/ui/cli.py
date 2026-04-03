import getpass
import json
import re
import shutil
import subprocess
import sys
import time
import webbrowser
import threading
from pathlib import Path
from typing import Optional

# Üst düzey importlar
import keyring
import requests
import psutil

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

# --- Proje İçi İçe Aktarmalar ---
from ..core.analyzers.pro import unload_ollama_model
from ..config.config import (
    Mode,
    Provider,
    add_to_blacklist,
    add_to_whitelist,
    list_keywords,
    remove_from_blacklist,
    remove_from_whitelist,
)
from .styles import console, clear_console
from ..utils.paths import get_path, ROOT_DIR


# --- Sabitler ---
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")
_KEYRING_SERVICE = "MailShift"

OLLAMA_BASE_URL = "http://localhost:11434"
LM_STUDIO_BASE_URL = "http://localhost:1234"
REQ_TIMEOUT_QUICK = 3
REQ_TIMEOUT_DEFAULT = 5
REQ_TIMEOUT_LONG = 30
POLL_INTERVAL = 2.0


# --- Durum Yönetimi ---
class LLMState:
    """LLM servislerinin bu oturumda başlatılıp başlatılmadığını takip eder."""
    def __init__(self):
        self.ollama_started_by_us = False
        self.lm_studio_started_by_us = False

session_state = LLMState()


# --- Yardımcı Fonksiyonlar ---

def _kill_process_by_name(process_name: str) -> bool:
    """İşletim sisteminden bağımsız (psutil) olarak süreçleri isme göre sonlandırır."""
    killed_any = False
    for proc in psutil.process_iter(['name']):
        try:
            if proc.info['name'] and process_name.lower() in proc.info['name'].lower():
                proc.kill()
                killed_any = True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return killed_any

def preload_model_in_background(model_name: str, backend: str) -> None:
    """Seçilen modeli arkaplanda asenkron olarak VRAM'e yükler (Warm-up)."""
    def _warmup():
        if backend == "ollama":
            try:
                requests.post(
                    f"{OLLAMA_BASE_URL}/api/generate",
                    json={"model": model_name, "keep_alive": "30m"},
                    timeout=120
                )
            except Exception:
                pass
        elif backend == "lm_studio":
            try:
                requests.post(
                    f"{LM_STUDIO_BASE_URL}/v1/chat/completions",
                    json={
                        "model": model_name,
                        "messages": [{"role": "user", "content": "warmup"}],
                        "max_tokens": 1
                    },
                    timeout=120
                )
            except Exception:
                pass

    console.print(f"\n[dim]⚡ {model_name} arkaplanda belleğe yükleniyor...[/dim]")
    thread = threading.Thread(target=_warmup, daemon=True)
    thread.start()


# --- Kimlik Yönetimi ---

def _load_saved_credentials() -> dict[str, dict[str, str]]:
    return {}

def _get_saved_credentials(provider: Provider) -> tuple[str, str] | None:
    try:
        username = keyring.get_password(_KEYRING_SERVICE, f"{provider.value}:username")
        password = keyring.get_password(_KEYRING_SERVICE, f"{provider.value}:password")
        if username and password:
            return username, password
    except keyring.errors.KeyringError as e:
        console.print(f"[dim]Keyring okuma hatası: {e}[/dim]")
    return None

def _save_credentials(provider: Provider, username: str, password: str) -> bool:
    try:
        keyring.set_password(_KEYRING_SERVICE, f"{provider.value}:username", username)
        keyring.set_password(_KEYRING_SERVICE, f"{provider.value}:password", password)
        return True
    except keyring.errors.KeyringError as e:
        console.print(f"[bold red]Keyring yazma hatası:[/bold red] {e}")
        return False


# --- Girdi İstekleri ---

def _is_valid_email(value: str) -> bool:
    return bool(_EMAIL_RE.match(value.strip()))

def _prompt_email(label: str) -> str:
    while True:
        value = Prompt.ask(f"\n[bold cyan]{label}[/bold cyan]").strip()
        if _is_valid_email(value):
            clear_console()
            return value
        console.print(
            f"[bold red]✘ Geçersiz e-posta adresi:[/bold red] [yellow]{value}[/yellow]\n"
            "  Lütfen geçerli bir e-posta adresi girin (örn. [dim]kullanici@gmail.com[/dim])."
        )

def prompt_provider() -> Provider:
    t = Table(box=None, show_header=False, padding=(0, 2))
    t.add_column(style="bold yellow", width=4)
    t.add_column(style="bold", width=8)
    t.add_column(style="dim")
    t.add_row("[1]", "Gmail", "IMAP over SSL | App Password required")
    t.add_row("[2]", "Proton", "via Proton Bridge at 127.0.0.1 (Required paid account)")
    t.add_row("[3]", "Custom", "your own IMAP server")
    console.print(Panel(t, title="[bold cyan]Mail Provider[/bold cyan]", border_style="cyan", box=box.ROUNDED))
    choice = Prompt.ask("[bold]Provider[/bold]", choices=["1", "2", "3"], default="1")
    clear_console()
    
    provider_map = {"1": Provider.GMAIL, "2": Provider.PROTON, "3": Provider.CUSTOM}
    return provider_map.get(choice, Provider.GMAIL)

def prompt_custom_imap_settings() -> tuple[str, int, bool]:
    while True:
        host = Prompt.ask("\n[bold cyan]IMAP Host[/bold cyan] (örn. imap.example.com)").strip()
        if host:
            break
        console.print("[bold red]✘ Host boş bırakılamaz.[/bold red]")

    while True:
        raw_port = Prompt.ask("[bold cyan]IMAP Port[/bold cyan]", default="993").strip()
        if raw_port.isdigit() and 1 <= int(raw_port) <= 65535:
            port = int(raw_port)
            break
        console.print(f"[bold red]✘ Geçersiz port:[/bold red] [yellow]{raw_port}[/yellow]")

    use_ssl = Prompt.ask("[bold cyan]SSL kullanılsın mı?[/bold cyan]", choices=["y", "n"], default="y")
    clear_console()
    return host, port, use_ssl.lower() == "y"


# --- LM Studio İşlemleri ---

def check_lm_studio_installed() -> bool:
    if shutil.which("lms") is not None:
        return True
    if sys.platform != "win32" or shutil.which("winget") is None:
        return False
    try:
        result = subprocess.run(
            ["winget", "list", "--id", "ElementLabs.LMStudio", "-e"],
            capture_output=True, text=True, timeout=REQ_TIMEOUT_LONG,
        )
        return "ElementLabs.LMStudio" in (result.stdout + result.stderr)
    except (subprocess.SubprocessError, OSError):
        return False

def open_lm_studio_download_page() -> bool:
    try:
        return webbrowser.open("https://lmstudio.ai", new=2)
    except Exception:
        return False

def install_lm_studio() -> bool:
    if sys.platform == "win32":
        if shutil.which("winget") is None:
            console.print("[red]winget bulunamadı. Lütfen LM Studio'yu siteden indirip kurun: https://lmstudio.ai[/red]")
            return False
        command = [
            "winget", "install", "--id", "ElementLabs.LMStudio", "-e",
            "--accept-package-agreements", "--accept-source-agreements",
        ]
        title = "LM Studio winget ile kuruluyor..."
    elif sys.platform == "darwin":
        if shutil.which("brew") is None:
            console.print("[red]Homebrew bulunamadı. Lütfen LM Studio'yu siteden indirip kurun: https://lmstudio.ai[/red]")
            return False
        command = ["brew", "install", "--cask", "lm-studio"]
        title = "LM Studio Homebrew ile kuruluyor..."
    else:
        console.print("[red]Otomatik LM Studio kurulumu bu sistemde desteklenmiyor.[/red]")
        console.print("[yellow]Lütfen LM Studio'yu manuel kurun: https://lmstudio.ai[/yellow]")
        return False

    console.print(f"\n[bold yellow]{title}[/bold yellow]")
    try:
        process = subprocess.Popen(command, stdout=sys.stdout, stderr=sys.stderr)
        process.wait()

        if process.returncode == 0:
            console.print("\n[bold green]✔ LM Studio başarıyla kuruldu.[/bold green]")
            return True
        console.print(f"\n[bold red]✘ Kurulum başarısız oldu (Code: {process.returncode}).[/bold red]")
        return False
    except OSError as exc:
        console.print(f"\n[bold red]✘ Başlatılamadı:[/bold red] {exc}")
        return False

def _is_lm_studio_server_running(base_url: str = LM_STUDIO_BASE_URL) -> bool:
    try:
        resp = requests.get(f"{base_url}/v1/models", timeout=REQ_TIMEOUT_QUICK)
        return resp.status_code == 200
    except requests.RequestException:
        return False

def start_lm_studio_server(base_url: str = LM_STUDIO_BASE_URL, max_retries: int = 5, retry_delay: int = 2) -> bool:
    if _is_lm_studio_server_running(base_url=base_url):
        return True
    if shutil.which("lms") is None:
        return False

    console.print("[yellow]LM Studio server kapalı görünüyor, lms server start deneniyor...[/yellow]")
    try:
        kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if sys.platform == "win32":
            kwargs.update({"creationflags": 0x08000000, "close_fds": True, "stdin": subprocess.DEVNULL})
        else:
            kwargs.update({"start_new_session": True})
            
        subprocess.Popen(["lms", "server", "start"], **kwargs)
    except OSError:
        return False

    for _ in range(max_retries):
        time.sleep(retry_delay)
        if _is_lm_studio_server_running(base_url=base_url):
            session_state.lm_studio_started_by_us = True
            console.print("[green]LM Studio server otomatik başlatıldı.[/green]")
            return True
    return False

def cleanup_lm_studio_if_it_was_started_by_us() -> None:
    if not session_state.lm_studio_started_by_us or shutil.which("lms") is None:
        return
    console.print("[cyan]LM Studio server durduruluyor...[/cyan]")
    try:
        subprocess.run(["lms", "server", "stop"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    except (subprocess.SubprocessError, OSError):
        pass

def get_lm_studio_models(base_url: str = LM_STUDIO_BASE_URL, timeout: int = REQ_TIMEOUT_DEFAULT) -> list[str]:
    try:
        resp = requests.get(f"{base_url}/v1/models", timeout=timeout)
        resp.raise_for_status()
        return [m["id"] for m in resp.json().get("data", [])]
    except requests.RequestException:
        return []

def _extract_lm_studio_status_payload(payload: object, model_name: str) -> dict:
    if isinstance(payload, dict):
        downloads = payload.get("downloads")
        if isinstance(downloads, list) and downloads:
            target_model = model_name.lower()
            for item in downloads:
                if not isinstance(item, dict):
                    continue
                item_model = str(item.get("model") or item.get("model_id") or item.get("id") or "")
                if item_model.lower() == target_model:
                    return item
            first = downloads[0]
            if isinstance(first, dict):
                return first
        status_obj = payload.get("download")
        if isinstance(status_obj, dict):
            return status_obj
        return payload
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        return payload[0]
    return {}

def _parse_lm_studio_download_status(payload: object, model_name: str) -> tuple[str, float, bool, bool, str]:
    item = _extract_lm_studio_status_payload(payload, model_name)
    status_raw = str(item.get("status") or item.get("state") or item.get("phase") or "").strip()
    message = str(item.get("message") or item.get("detail") or "").strip()
    error = str(item.get("error") or "").strip()
    progress_raw = item.get("progress")
    percent = 0.0
    if isinstance(progress_raw, (int, float)):
        percent = float(progress_raw)
        if percent <= 1.0:
            percent *= 100.0
    elif isinstance(progress_raw, str):
        clean = progress_raw.replace("%", "").strip()
        try:
            percent = float(clean)
            if percent <= 1.0:
                percent *= 100.0
        except ValueError:
            percent = 0.0
    total = item.get("total")
    completed = item.get("completed")
    if percent <= 0 and isinstance(total, (int, float)) and isinstance(completed, (int, float)) and total:
        percent = (float(completed) / float(total)) * 100.0
    status_lower = status_raw.lower()
    is_done = status_lower in {"completed", "complete", "success", "succeeded", "finished", "ready", "downloaded"}
    is_failed = status_lower in {"failed", "error", "cancelled", "canceled"}
    if is_done:
        percent = max(percent, 100.0)
    percent = min(max(percent, 0.0), 100.0)
    status_text = status_raw or message or "İndiriliyor"
    error_message = error or (message if is_failed else "")
    return status_text, percent, is_done, is_failed, error_message

def download_lm_studio_model(model_name: str, base_url: str = LM_STUDIO_BASE_URL, timeout_seconds: int = 1800) -> bool:
    model_name = model_name.strip()
    if not model_name:
        return False
    console.print(f"\n[bold yellow]LM Studio model indiriliyor:[/bold yellow] [bold cyan]{model_name}[/bold cyan]")
    try:
        start_resp = requests.post(
            f"{base_url}/api/v1/models/download",
            json={"source": "huggingface", "model": model_name},
            timeout=REQ_TIMEOUT_LONG,
        )
        start_resp.raise_for_status()
    except requests.RequestException as exc:
        console.print(f"[bold red]✘ LM Studio indirme başlatılamadı:[/bold red] {exc}")
        return False
    
    started_at = time.time()
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), BarColumn(), TaskProgressColumn(), TimeElapsedColumn(), console=console, transient=True) as progress:
        task = progress.add_task(f"İndiriliyor {model_name}...", total=100)
        while True:
            if time.time() - started_at > timeout_seconds:
                console.print(f"[bold red]✘ {model_name} zaman aşımı.[/bold red]")
                return False
            try:
                status_resp = requests.get(f"{base_url}/api/v1/models/download/status", timeout=10)
                status_resp.raise_for_status()
                status_payload = status_resp.json()
            except requests.RequestException:
                progress.update(task, description="Durum alınıyor...")
                time.sleep(POLL_INTERVAL)
                continue
                
            status_text, percent, is_done, is_failed, error_message = _parse_lm_studio_download_status(status_payload, model_name)
            progress.update(task, completed=percent, description=status_text)
            
            if is_done:
                progress.update(task, completed=100)
                console.print(f"[bold green]✔ {model_name} indirildi![/bold green]")
                return True
            if is_failed:
                console.print(f"[bold red]✘ Hata:[/bold red] {error_message or 'Bilinmeyen hata'}")
                return False
            time.sleep(POLL_INTERVAL)

def ensure_lm_studio_model(model_name: str, available_models: list[str] | None = None, base_url: str = LM_STUDIO_BASE_URL, max_attempts: int = 2) -> bool:
    current = available_models if available_models is not None else get_lm_studio_models(base_url=base_url)
    target_model = model_name.lower()
    if any(target_model == m.lower() for m in current):
        return True
    for attempt in range(1, max_attempts + 1):
        console.print(f"[cyan]{model_name} LM Studio listesinde yok. İndiriliyor ({attempt}/{max_attempts}).[/cyan]")
        if download_lm_studio_model(model_name, base_url=base_url):
            refreshed = get_lm_studio_models(base_url=base_url, timeout=8)
            if any(target_model == m.lower() for m in refreshed):
                return True
    return False


# --- Ollama İşlemleri ---

def check_ollama_installed() -> bool:
    return shutil.which("ollama") is not None

def show_ollama_next_steps(reason: str = "") -> None:
    steps = (
        "[bold]1)[/bold] Terminali kapatıp açın.\n"
        "[bold]2)[/bold] Ollama servis: [cyan]ollama serve[/cyan]\n"
        "[bold]3)[/bold] MailShift'i tekrar çalıştırın."
    )
    extra = f"[yellow]{reason}[/yellow]\n\n" if reason else ""
    console.print(Panel(f"{extra}[bold cyan]Adımlar:[/bold cyan]\n{steps}", title="Bilgi", border_style="yellow", box=box.ROUNDED))

def install_ollama() -> bool:
    if sys.platform == "win32":
        if shutil.which("winget") is None:
            console.print("[red]winget bulunamadı. Lütfen Ollama'yı siteden indirip kurun: https://ollama.com[/red]")
            return False
        command = [
            "winget", "install", "--id", "Ollama.Ollama", "-e",
            "--accept-package-agreements", "--accept-source-agreements",
        ]
        title = "Ollama winget ile kuruluyor..."
    elif sys.platform == "darwin":
        if shutil.which("brew") is None:
            console.print("[red]Homebrew bulunamadı. Lütfen Ollama'yı siteden indirip kurun: https://ollama.com[/red]")
            return False
        command = ["brew", "install", "ollama"]
        title = "Ollama Homebrew ile kuruluyor..."
    else:
        console.print("[red]Otomatik Ollama kurulumu bu sistemde desteklenmiyor.[/red]")
        console.print("[yellow]Lütfen Ollama'yı manuel kurun: https://ollama.com[/yellow]")
        return False

    console.print(f"\n[bold yellow]{title}[/bold yellow]")
    try:
        process = subprocess.Popen(command, stdout=sys.stdout, stderr=sys.stderr)
        process.wait()
        if process.returncode == 0:
            console.print("\n[bold green]✔ Kurulum komutu çalıştırıldı![/bold green]")
            return True
        return False
    except OSError as e:
        console.print(f"\n[bold red]Başlatılamadı:[/bold red] {e}")
        return False

def _launch_ollama_process() -> bool:
    try:
        kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if sys.platform == "win32":
            kwargs.update({"creationflags": 0x08000000, "close_fds": True, "stdin": subprocess.DEVNULL})
        else:
            kwargs.update({"start_new_session": True})
            
        subprocess.Popen(["ollama", "serve"], **kwargs)
        return True
    except OSError:
        return False

def stop_ollama() -> bool:
    killed = _kill_process_by_name("ollama")
    if sys.platform == "win32":
        _kill_process_by_name("ollama_llama_server")
    time.sleep(2)
    return killed

def cleanup_ollama_if_it_was_started_by_us(model_name: Optional[str] = None):
    if session_state.ollama_started_by_us:
        console.print("[cyan]Ollama otomatik başlatılmıştı, kapatılıyor...[/cyan]")
        stop_ollama()
    elif model_name:
        console.print(f"[cyan]Ollama çalışıyordu, {model_name} tahliye ediliyor...[/cyan]")
        unload_ollama_model(model=model_name)

def _is_ollama_running() -> bool:
    try:
        resp = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=REQ_TIMEOUT_QUICK)
        return resp.status_code == 200
    except requests.RequestException:
        return False

def start_ollama(max_retries: int = 3, retry_delay: int = 3) -> bool:
    console.print("\n[yellow]Ollama çalışmıyor, başlatılıyor...[/yellow]")
    if not check_ollama_installed() or not _launch_ollama_process():
        return False

    for _ in range(max_retries):
        time.sleep(retry_delay)
        if get_ollama_models():
            console.print("[green]Ollama başarıyla başlatıldı![/green]")
            session_state.ollama_started_by_us = True
            return True
    return False

def get_ollama_models(base_url: str = OLLAMA_BASE_URL, timeout: int = REQ_TIMEOUT_DEFAULT) -> list[str]:
    try:
        resp = requests.get(f"{base_url}/api/tags", timeout=timeout)
        resp.raise_for_status()
        return [m["name"] for m in resp.json().get("models", [])]
    except requests.RequestException:
        return []

def download_ollama_model(model_name: str, base_url: str = OLLAMA_BASE_URL) -> bool:
    console.print(f"\n[bold yellow]Model indiriliyor:[/bold yellow] [bold cyan]{model_name}[/bold cyan]")
    try:
        with requests.post(f"{base_url}/api/pull", json={"name": model_name}, stream=True) as resp:
            resp.raise_for_status()
            with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), BarColumn(), TaskProgressColumn(), TimeElapsedColumn(), console=console, transient=True) as progress:
                task = progress.add_task(f"İndiriliyor {model_name}...", total=100)
                for line in resp.iter_lines():
                    if line:
                        data = json.loads(line.decode("utf-8"))
                        status = data.get("status", "")
                        if "total" in data and "completed" in data and data["total"] > 0:
                            percent = (data["completed"] / data["total"]) * 100
                            progress.update(task, completed=percent, description=f"{status} ({data['completed']/1024/1024:.1f}MB / {data['total']/1024/1024:.1f}MB)")
                        else:
                            progress.update(task, description=status)
                            
                        if status == "success":
                            progress.update(task, completed=100)
                            break
        console.print(f"[bold green]✔ {model_name} indirildi![/bold green]")
        return True
    except requests.RequestException as e:
        console.print(f"[bold red]✘ İndirme hatası:[/bold red] {e}")
        return False

def ensure_ollama_model(model_name: str, available_models: list[str] | None = None, base_url: str = OLLAMA_BASE_URL, max_attempts: int = 2) -> bool:
    current = available_models if available_models is not None else get_ollama_models(base_url=base_url)
    target_model = model_name.lower()
    if any(target_model == m.lower() for m in current):
        return True
    for attempt in range(1, max_attempts + 1):
        if download_ollama_model(model_name, base_url=base_url):
            refreshed = get_ollama_models(base_url=base_url, timeout=8)
            if any(target_model == m.lower() for m in refreshed):
                return True
    return False


# --- Prompts ve Akış Yönetimi ---

def prompt_llm_backend() -> str:
    t = Table(box=None, show_header=False, padding=(0, 2))
    t.add_column(style="bold yellow", width=4)
    t.add_column(style="bold", width=12)
    t.add_column(style="dim")
    t.add_row("[1]", "LM Studio", "[bold green]Recommended for All Users[/bold green]")
    t.add_row("[2]", "Ollama", "[bold cyan]Recommended for NVIDIA GPU (Slow)[/bold cyan]")
    console.print(Panel(t, title="[bold cyan]LLM Provider[/bold cyan]", border_style="cyan", box=box.ROUNDED))
    choice = Prompt.ask("[bold]Provider[/bold]", choices=["1", "2"], default="1")
    clear_console()
    return "lm_studio" if choice == "1" else "ollama"

def _prompt_lm_studio_flow() -> tuple[Mode, str, str, Optional[int]]:
    available_models = get_lm_studio_models()

    if not available_models and check_lm_studio_installed() and not _is_lm_studio_server_running():
        start_lm_studio_server()
        available_models = get_lm_studio_models(timeout=8)

    if not available_models and not check_lm_studio_installed():
        console.print(Panel(
            "[bold yellow]LM Studio sistemde bulunamadı.[/bold yellow]\n\n"
            "LM Studio kurmak için bir yöntem seçin:\n"
            "[bold]1)[/bold] winget ile kur [dim](winget install ElementLabs.LMStudio)[/dim]\n"
            "[bold]2)[/bold] Resmi siteden indir [dim](https://lmstudio.ai)[/dim]\n"
            "[bold]3)[/bold] Kurulumu atla",
            title="[bold red]LM Studio Kurulu Değil[/bold red]",
            border_style="red",
            box=box.ROUNDED,
        ))

        install_choice = Prompt.ask("[bold]Kurulum seçeneği[/bold]", choices=["1", "2", "3"], default="1")

        if install_choice == "1":
            install_lm_studio()
        elif install_choice == "2":
            if open_lm_studio_download_page():
                console.print("[green]Tarayıcıda LM Studio indirme sayfası açıldı.[/green]")
            else:
                console.print("[yellow]Tarayıcı açılamadı. Lütfen manuel gidin: https://lmstudio.ai[/yellow]")
        else:
            console.print("[yellow]LM Studio kurulumu atlandı.[/yellow]")

    if not available_models:
        console.print(Panel(
            "[bold yellow]LM Studio'ya bağlanılamadı veya hiç model yüklü değil![/bold yellow]\n\n"
            "Otomatik indirme için LM Studio açık olmalı ve Local Server başlatılmalıdır.\n"
            "[bold]1)[/bold] LM Studio'yu açın\n"
            "[bold]2)[/bold] 'Local Server' sekmesinden 'Start Server' butonuna tıklayın\n"
            "[bold]3)[/bold] İsterseniz modeli buradan otomatik indirtin\n"
            "[bold]4)[/bold] Olmazsa Enter ile modeli yeniden listeleyin",
            title="[bold red]LM Studio Gerekli[/bold red]",
            border_style="red",
            box=box.ROUNDED,
        ))
        if Confirm.ask("[bold]Hugging Face üzerinden model otomatik indirilsin mi?[/bold]", default=True):
            model_to_download = Prompt.ask("[bold cyan]Hugging Face model adı[/bold cyan] [dim](ör: Qwen/Qwen3-0.6B-GGUF)[/dim]").strip()
            if model_to_download and ensure_lm_studio_model(model_to_download, available_models=available_models):
                available_models = get_lm_studio_models(timeout=8)
                
        if not available_models:
            Prompt.ask("[dim]LM Studio hazırsa Enter'a basıp modeli tekrar listeleyin[/dim]")
            available_models = get_lm_studio_models()

        if not available_models:
            console.print("[red]Hâlâ model alınamıyor. Fast moduna geçiliyor.[/red]")
            return Mode.FAST, "qwen3.5:2B", "lm_studio", None

    mt = Table(box=None, show_header=False, padding=(0, 2))
    mt.add_column(style="bold yellow", width=4)
    mt.add_column(style="bold")
    for idx, model in enumerate(available_models, start=1):
        mt.add_row(f"[{idx}]", model)
    console.print(Panel(mt, title="[bold cyan]LM Studio Model[/bold cyan]", border_style="cyan", box=box.ROUNDED))

    choices = [str(i) for i in range(1, len(available_models) + 1)]
    model_choice = Prompt.ask("[bold]Model[/bold]", choices=choices, default="1")
    selected_model = available_models[int(model_choice) - 1]
    
    worker_input = Prompt.ask(
        "\n[bold cyan]Paralel işlemci (worker) sayısı[/bold cyan]\n"
        "[dim](Boş bırakılırsa sisteminiz için en uygun değer otomatik hesaplanır)[/dim]",
        default=""
    ).strip()
    manual_workers = int(worker_input) if worker_input.isdigit() and int(worker_input) > 0 else None
    
    # Yeni Özellik: Model arka planda VRAM'e yükleniyor
    preload_model_in_background(selected_model, "lm_studio")
    
    return Mode.PRO, selected_model, "lm_studio", manual_workers

def prompt_mode() -> tuple[Mode, str, str, Optional[int]]:
    t = Table(box=None, show_header=False, padding=(0, 2))
    t.add_column(style="bold yellow", width=4)
    t.add_column(style="bold", width=6)
    t.add_column(style="dim")
    t.add_row("[1]", "Fast", "Heuristic keyword matching | instant, no AI [yellow](Less accurate)[/yellow]")
    t.add_row("[2]", "Pro", "Heuristic + local LLM (Ollama / LM Studio) | more accurate [bold green](Recommended)[/bold green]")
    console.print(Panel(t, title="[bold cyan]Scan Mode[/bold cyan]", border_style="cyan", box=box.ROUNDED))
    choice = Prompt.ask("[bold]Mode[/bold]", choices=["1", "2"], default="1")
    mode = Mode.FAST if choice == "1" else Mode.PRO
    
    if mode == Mode.PRO:
        llm_backend = prompt_llm_backend()
        if llm_backend == "lm_studio":
            return _prompt_lm_studio_flow()

        console.print("\n[bold cyan]Select Ollama model:[/bold cyan]")
        available_models = get_ollama_models()

        if not available_models:
            if not check_ollama_installed():
                console.print(Panel(
                    f"[bold red]✘ Bağımlılık Eksik:[/bold red] [cyan]Ollama[/cyan] yüklü değil.\n\n"
                    "Pro Mode (LLM) kullanmak için Ollama gereklidir.\n"
                    "Lütfen [bold white]ollama.com[/bold white] adresinden indirin ve kurun.",
                    title="Hata", border_style="red"
                ))
                if Confirm.ask("\n[bold yellow]Ollama şimdi otomatik kurulsun mu?[/bold yellow]", default=True):
                    if install_ollama():
                        console.print("\n[bold green]Ollama kuruldu. Şimdi otomatik başlatmayı deniyorum...[/bold green]")
                        time.sleep(2)
                        if start_ollama():
                            available_models = get_ollama_models()
                            if not available_models:
                                show_ollama_next_steps("Ollama çalışıyor ancak henüz model bulunamadı.")
                                return Mode.FAST, "qwen3.5:2B", llm_backend
                        else:
                            show_ollama_next_steps("Ollama kuruldu ancak otomatik başlatılamadı.")
                            return Mode.FAST, "qwen3.5:2B", llm_backend
                    else:
                        show_ollama_next_steps("Kurulum tamamlanamadı. Manuel kurulum gerekebilir.")
                        return Mode.FAST, "qwen3.5:2B", llm_backend
                else:
                    show_ollama_next_steps("Otomatik kurulum atlandı.")
                    console.print("[cyan]Fast moduna geçiliyor...[/cyan]")
                    return Mode.FAST, "qwen3.5:2B", llm_backend

            console.print("[yellow]Ollama çalışmıyor veya model bulunamadı.[/yellow]")
            if Confirm.ask("\n[bold]Ollama'yı otomatik başlatmak ister misiniz?[/bold]", default=True):
                if start_ollama():
                    available_models = get_ollama_models()
                else:
                    console.print("[red]Ollama başlatılamadı.[/red]")
            else:
                console.print("\n[bold cyan]Ollama'yı manuel olarak başlatın:[/bold cyan]")
                console.print("  terminal/cmd => ollama serve")
                Prompt.ask("\n[cyan]Model listesini yeniden almak için Enter'a basın...[/cyan]")
                available_models = get_ollama_models()

            if not available_models:
                console.print("[red]Hâlâ model alınamıyor. Fast moduna geçiliyor.[/red]")
                return Mode.FAST, "qwen3.5:2B", llm_backend

        mt = Table(box=None, show_header=False, padding=(0, 2))
        mt.add_column(style="bold yellow", width=4)
        mt.add_column(style="bold")
        mt.add_column(style="dim")
        
        recommended_models = [
            ("qwen3.5:0.8B", "%95 Accurate", "Fast"),
            ("qwen3.5:2B", "Recommended", "Balanced"),
            ("qwen3.5:4B", "Recommended", "Slower"),
        ]

        SPEED_COLOR = {
            "Fast": "[bold green]Fast[/bold green]",
            "Balanced": "[bold yellow]Balanced[/bold yellow]",
            "Slower": "[bold red]Slower[/bold red]",
        }

        available_models_lower = [m.lower() for m in available_models]
        available_models_set = set(available_models_lower)
        recommended_names = [name for name, _, _ in recommended_models]
        recommended_names_lower = [name.lower() for name in recommended_names]
        recommended_names_set = set(recommended_names_lower)

        for idx, (rec_model, badge, speed) in enumerate(recommended_models, start=1):
            is_available = rec_model.lower() in available_models_set
            status = "[bold green]Mevcut[/bold green]" if is_available else "[bold red]Mevcut Değil[/bold red]"
            colored_speed = SPEED_COLOR.get(speed, speed)
            mt.add_row(f"[{idx}]", rec_model, status + f" ({badge}) " + colored_speed)
        
        non_rec_models = [m for m in available_models if m.lower() not in recommended_names_set]
        
        for idx, md in enumerate(non_rec_models, start=len(recommended_models) + 1):
            mt.add_row(f"[{idx}]", md, "")
        
        console.print(Panel(mt, title="[bold cyan]Ollama Model[/bold cyan]", border_style="cyan", box=box.ROUNDED))

        choices = [str(i) for i in range(1, len(recommended_models) + len(non_rec_models) + 1)]
        model_choice = Prompt.ask("[bold]Model[/bold]", choices=choices, default="1")
        
        if int(model_choice) <= len(recommended_models):
            model = recommended_models[int(model_choice) - 1][0]
        else:
            idx = int(model_choice) - len(recommended_models) - 1
            model = non_rec_models[idx]
        
        if model.lower() in recommended_names_set and model.lower() not in available_models_set:
            if not ensure_ollama_model(model, available_models=available_models):
                console.print("[yellow]Model hazır olmadığı için Fast moduna geçiliyor.[/yellow]")
                return Mode.FAST, "qwen3.5:2B", llm_backend

        worker_input = Prompt.ask(
            "\n[bold cyan]Paralel işlemci (worker) sayısı[/bold cyan]\n"
            "[dim](Boş bırakılırsa sisteminiz için en uygun değer otomatik hesaplanır)[/dim]",
            default=""
        ).strip()
        
        manual_workers = int(worker_input) if worker_input.isdigit() and int(worker_input) > 0 else None

        # Yeni Özellik: Model arka planda VRAM'e yükleniyor
        preload_model_in_background(model, llm_backend)

        return mode, model, llm_backend, manual_workers

    return mode, "qwen3.5:2B", "ollama", None

def prompt_credentials(provider: Provider, preset_username: Optional[str] = None, preset_password: Optional[str] = None) -> tuple[str, str]:
    if not preset_username or not preset_password:
        saved_creds = _get_saved_credentials(provider)
        if saved_creds:
            saved_username, saved_password = saved_creds
            if Confirm.ask(
                f"[bold]Kaydedilmiş hesap bulundu:[/bold] [cyan]{saved_username}[/cyan]\n"
                "  Kullanılsın mı?", default=True
            ):
                clear_console()
                return saved_username, saved_password
            clear_console()

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
                title="[bold yellow]Gmail App Password[/bold yellow]", border_style="yellow", box=box.ROUNDED,
            ))
            Prompt.ask("[dim]Devam etmek için Enter'a basın[/dim]")
            clear_console()
    elif provider == Provider.PROTON:
        username = preset_username.strip() if preset_username else _prompt_email("Proton Bridge e-posta adresi")
    else:
        username = preset_username.strip() if preset_username else Prompt.ask("\n[bold cyan]IMAP Kullanıcı Adı[/bold cyan]").strip()

    password = preset_password if preset_password else getpass.getpass("Şifre: ")

    if Confirm.ask("[bold]Kaydedilsin mi?[/bold]", default=True):
        if _save_credentials(provider, username, password):
            console.print("[green]Kimlik bilgileri kaydedildi.[/green]")
        else:
            console.print("[yellow]Kimlik bilgileri kaydedilemedi.[/yellow]")

    clear_console()
    return username, password


# --- Keywords ve Uninstall ---

def handle_keywords(list_flag: bool, add_w: Optional[str], rm_w: Optional[str], add_b: Optional[str], rm_b: Optional[str]) -> bool:
    handled = False
    if list_flag:
        w, b = list_keywords()
        console.print(Panel(
            f"[bold cyan]Whitelist ({len(w)} keywords):[/bold cyan]\n" + (", ".join(w) if w else "[dim]Empty[/dim]"),
            title="[bold green]Keywords[/bold green]", border_style="green", box=box.ROUNDED,
        ))
        console.print(Panel(
            f"[bold red]Blacklist ({len(b)} keywords):[/bold red]\n" + (", ".join(b) if b else "[dim]Empty[/dim]"),
            border_style="red", box=box.ROUNDED,
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
    console.print(Panel("[bold red]MailShift Tam Kaldırma[/bold red]", border_style="red", box=box.ROUNDED))
    if not Confirm.ask(
        "\n[bold yellow]Bu işlem MailShift'i ve tüm verilerini siler:[/bold yellow]\n"
        "  • Python paketleri (sadece proje dizinindekiler)\n"
        "  • Önbellek dosyaları (.db cache)\n"
        "  • Python __pycache__ klasörleri\n"
        "  • Proje dosyaları\n\n"
        "[bold]Devam?[/bold]", default=False
    ):
        console.print("[yellow]İşlem iptal edildi.[/yellow]")
        return
    
    base_path = ROOT_DIR
    console.print("\n[cyan]Kaldırma işlemi başlıyor...[/cyan]\n")
    items_removed = []
    
    files_to_remove = ["whitelist.json", "blacklist.json", "mailshift.db", "requirements.txt", "README.md", "LICENSE", ".gitignore"]
    for f in files_to_remove:
        fp = base_path / f
        if fp.exists():
            try:
                fp.unlink()
                items_removed.append(f"  • {f}")
            except OSError as e:
                console.print(f"[dim]Uyarı: {f} silinemedi ({e})[/dim]")
                
    dirs_to_remove = ["__pycache__", ".git", "tests"]
    for d in dirs_to_remove:
        dp = base_path / d
        if dp.exists() and dp.is_dir():
            try:
                shutil.rmtree(dp)
                items_removed.append(f"  • {d}/")
            except OSError as e:
                console.print(f"[dim]Uyarı: {d} klasörü silinemedi ({e})[/dim]")
                
    for pf in base_path.glob("*.py"):
        try:
            pf.unlink()
            items_removed.append(f"  • {pf.name}")
        except OSError:
            pass

    console.print("[green]Kaldırılan öğeler:[/green]")
    for item in items_removed:
        console.print(item)
        
    console.print(Panel(
        "[bold green]✔ MailShift başarıyla kaldırıldı![/bold green]\n\n"
        "[dim]Klasörü manuel olarak silebilirsiniz:[/dim]\n"
        f"[dim]{base_path}[/dim]", border_style="green", box=box.ROUNDED
    ))
