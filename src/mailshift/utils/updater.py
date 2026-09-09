"""
updater.py – GitHub ve PyPI üzerinden otomatik güncelleme kontrolü.
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request

from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn


_GITHUB_API_URL = "https://api.github.com/repos/lynchest/MailShift/commits/main"
_PYPI_API_URL = "https://pypi.org/pypi/mailshift/json"
_TIMEOUT = 5


def _get_local_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _get_remote_commit() -> str | None:
    try:
        req = urllib.request.Request(
            _GITHUB_API_URL,
            headers={"User-Agent": "MailShift-Updater"},
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
            return data.get("sha")
    except Exception:
        pass
    return None


def _get_installed_version() -> str | None:
    try:
        from importlib.metadata import version
        return version("mailshift")
    except Exception:
        pass
    return None


def _get_pypi_version() -> str | None:
    try:
        req = urllib.request.Request(
            _PYPI_API_URL,
            headers={"User-Agent": "MailShift-Updater"},
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
            return data.get("info", {}).get("version")
    except Exception:
        pass
    return None


def _parse_version(v: str) -> tuple[int, ...]:
    parts = []
    for p in v.split("."):
        clean = ""
        for ch in p:
            if ch.isdigit():
                clean += ch
            else:
                break
        if clean:
            parts.append(int(clean))
        else:
            parts.append(0)
    return tuple(parts)


def _is_working_tree_dirty() -> bool:
    """Uncommitted değişiklik veya push edilmemiş commit varsa True döner."""
    try:
        # Uncommitted changes
        r = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            return True
        # Unpushed commits (local is ahead of or diverged from remote)
        r2 = subprocess.run(
            ["git", "rev-list", "--count", "--left-only", "HEAD...@{u}"],
            capture_output=True, text=True, timeout=5,
        )
        if r2.returncode == 0 and r2.stdout.strip() not in ("", "0"):
            return True
    except Exception:
        pass
    return False


def check_and_prompt_update(console) -> None:
    """Uygulama açılışında güncelleme kontrolü yapar:
    1. Git clone ile çalışıyorsa: GitHub'daki son commit ile yerel commit'i karşılaştırır, fark varsa git pull önerir.
    2. pipx / pip paketi olarak çalışıyorsa: PyPI'daki son sürüm ile kurulu sürümü karşılaştırır, yeni sürüm varsa güncelleme uyarısı gösterir.
    """
    try:
        local_commit = _get_local_commit()

        if local_commit:
            # Git deposu tabanlı kurulum
            if _is_working_tree_dirty():
                return

            remote_commit = _get_remote_commit()
            if not remote_commit or local_commit == remote_commit:
                return

            short_local = local_commit[:7]
            short_remote = remote_commit[:7]

            console.print(
                Panel(
                    f"[bold]Yerel sürüm :[/bold] [dim]{short_local}[/dim]\n"
                    f"[bold]Son sürüm   :[/bold] [green]{short_remote}[/green]\n\n"
                    "GitHub'da yeni bir güncelleme mevcut.",
                    title="[bold green]Güncelleme Mevcut (Git)[/bold green]",
                    border_style="green",
                )
            )

            from rich.prompt import Confirm
            if not Confirm.ask("Şimdi güncellensin mi?", default=False):
                return

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
                transient=True,
            ) as progress:
                progress.add_task("Güncelleniyor...", total=None)
                result = subprocess.run(
                    ["git", "pull", "origin", "main"],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )

            if result.returncode == 0:
                console.print(
                    Panel(
                        "Güncelleme başarıyla tamamlandı.\n\n"
                        "[bold]Lütfen uygulamayı yeniden başlatın.[/bold]",
                        title="[bold green]Güncelleme Tamamlandı[/bold green]",
                        border_style="green",
                    )
                )
                sys.exit(0)
            else:
                console.print(
                    Panel(
                        f"[red]git pull başarısız oldu:[/red]\n{result.stderr.strip()}",
                        title="[bold red]Güncelleme Başarısız[/bold red]",
                        border_style="red",
                    )
                )
        else:
            # Pip / pipx paket kurulumu
            installed_ver = _get_installed_version()
            if not installed_ver:
                return

            pypi_ver = _get_pypi_version()
            if not pypi_ver:
                return

            if _parse_version(pypi_ver) > _parse_version(installed_ver):
                console.print(
                    Panel(
                        f"[bold]Kurulu sürüm :[/bold] [dim]v{installed_ver}[/dim]\n"
                        f"[bold]Güncel sürüm :[/bold] [green]v{pypi_ver}[/green]\n\n"
                        "PyPI üzerinde yeni bir MailShift sürümü yayınlandı!\n\n"
                        "Güncellemek için terminalinizde çalıştırın:\n"
                        "  [bold cyan]pipx upgrade mailshift[/bold cyan]\n"
                        "  [dim](pip kullanıyorsanız: pip install --upgrade mailshift)[/dim]",
                        title="[bold green]Yeni Sürüm Mevcut (PyPI)[/bold green]",
                        border_style="green",
                    )
                )
    except Exception:
        pass
