"""
updater.py – GitHub üzerinden otomatik güncelleme kontrolü.
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
    """Uygulama açılışında GitHub'daki en yeni commit ile yerel commit'i karşılaştırır.
    Fark varsa kullanıcıya sorar; onaylanırsa git pull çalıştırır ve çıkar."""
    try:
        from rich.prompt import Confirm

        if _is_working_tree_dirty():
            return

        local = _get_local_commit()
        remote = _get_remote_commit()

        if not local or not remote:
            return

        if local == remote:
            return

        short_local = local[:7]
        short_remote = remote[:7]

        console.print(
            Panel(
                f"[bold]Yerel sürüm :[/bold] [dim]{short_local}[/dim]\n"
                f"[bold]Son sürüm   :[/bold] [green]{short_remote}[/green]\n\n"
                "GitHub'da yeni bir güncelleme mevcut.",
                title="[bold green]Güncelleme Mevcut[/bold green]",
                border_style="green",
            )
        )

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

    except Exception:
        pass
