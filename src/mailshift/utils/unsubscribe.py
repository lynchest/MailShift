"""
unsubscribe.py – Utilities for handling List-Unsubscribe actions.
"""
from __future__ import annotations

import ipaddress
import http.client
import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse
from datetime import datetime
from pathlib import Path

from ..utils.logger import log


@dataclass
class UnsubscribeEntry:
    """Represents a unique unsubscribe target (one per distinct URL)."""

    sender: str
    unsubscribe_url: str
    mail_count: int = 1


def build_unsubscribe_entries(results) -> list[UnsubscribeEntry]:
    """
    Deduplicate ScanResults by unsubscribe URL and return a list of
    UnsubscribeEntry objects sorted by mail_count descending.
    """
    seen: dict[str, UnsubscribeEntry] = {}
    for r in results:
        url = r.mail.unsubscribe_url
        if not url:
            continue
        if url in seen:
            seen[url].mail_count += 1
        else:
            seen[url] = UnsubscribeEntry(
                sender=r.mail.sender,
                unsubscribe_url=url,
                mail_count=1,
            )
    return sorted(seen.values(), key=lambda e: e.mail_count, reverse=True)


def unsubscribe_log_host(url: str) -> str:
    """Return only the host for logs, omitting paths and token-bearing query data."""
    parsed = urlparse(url)
    return parsed.hostname or "unknown host"


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """
    Custom redirect handler that validates the target URL before following it.
    """

    def __init__(self, resolver=None):
        super().__init__()
        self.resolver = resolver or _SafeURLResolver()

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not self.resolver.resolve(newurl):
            log.warning(f"Blocking redirect to unsafe URL: {newurl}")
            raise urllib.error.HTTPError(
                newurl, 403, "Redirect to unsafe URL blocked", headers, None
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _is_public_ip(ip_str: str) -> bool:
    return ipaddress.ip_address(ip_str).is_global


class _SafeURLResolver:
    def __init__(self):
        self.pins: dict[str, str] = {}

    def resolve(self, url: str) -> str | None:
        if url in self.pins:
            return self.pins[url]
        try:
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                return None
            addr_info = socket.getaddrinfo(
                parsed.hostname, parsed.port, type=socket.SOCK_STREAM
            )
            addresses = [info[4][0] for info in addr_info]
            if not addresses or any(not _is_public_ip(address) for address in addresses):
                log.warning(f"Blocking potentially unsafe URL: {url}")
                return None
            self.pins[url] = addresses[0]
            return addresses[0]
        except Exception as exc:
            log.debug(f"Error validating URL {url}: {exc}")
            return None


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, *, pinned_ip, **kwargs):
        super().__init__(host, **kwargs)
        self.pinned_ip = pinned_ip

    def connect(self):
        self.sock = self._create_connection(
            (self.pinned_ip, self.port), self.timeout, self.source_address
        )
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if self._tunnel_host:
            self._tunnel()


class _PinnedHTTPSConnection(_PinnedHTTPConnection, http.client.HTTPSConnection):
    def connect(self):
        _PinnedHTTPConnection.connect(self)
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, resolver):
        super().__init__()
        self.resolver = resolver

    def http_open(self, req):
        pinned_ip = self.resolver.resolve(req.full_url)
        if not pinned_ip:
            raise urllib.error.URLError("URL blocked for security reasons")
        connection = lambda host, **kwargs: _PinnedHTTPConnection(
            host, pinned_ip=pinned_ip, **kwargs
        )
        return self.do_open(connection, req)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, resolver):
        super().__init__()
        self.resolver = resolver

    def https_open(self, req):
        pinned_ip = self.resolver.resolve(req.full_url)
        if not pinned_ip:
            raise urllib.error.URLError("URL blocked for security reasons")
        connection = lambda host, **kwargs: _PinnedHTTPSConnection(
            host, pinned_ip=pinned_ip, context=self._context, **kwargs
        )
        return self.do_open(connection, req)


def is_safe_url(url: str) -> bool:
    """
    Check if a URL is safe to request (prevents SSRF).
    Only allows http/https and blocks private/local IP ranges.
    """
    return _SafeURLResolver().resolve(url) is not None


def perform_unsubscribe(url: str) -> tuple[bool, str]:
    """
    Send an unsubscribe request to *url*.

    Tries GET first; if the server returns 4xx/5xx, falls back to a
    RFC 8058 one-click POST (body: ``List-Unsubscribe=One-Click``).

    Returns ``(success, status_message)``.
    """
    resolver = _SafeURLResolver()
    if not resolver.resolve(url):
        return False, "URL blocked for security reasons"

    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _PinnedHTTPHandler(resolver),
        _PinnedHTTPSHandler(resolver),
        SafeRedirectHandler(resolver),
    )
    headers = {"User-Agent": "Mozilla/5.0 (compatible; MailShift/1.0)"}

    # --- GET attempt ---
    try:
        req = urllib.request.Request(url, headers=headers)
        with opener.open(req, timeout=10) as resp:
            if 200 <= resp.status < 400:
                return True, f"GET {resp.status}"
    except urllib.error.HTTPError as exc:
        log.debug(f"Unsubscribe GET failed ({exc.code}), trying POST: {url}")
    except Exception as exc:
        log.debug(f"Unsubscribe GET error, trying POST: {exc}")

    # --- POST fallback (RFC 8058) ---
    try:
        post_data = b"List-Unsubscribe=One-Click"
        req = urllib.request.Request(
            url,
            data=post_data,
            headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
        )
        with opener.open(req, timeout=10) as resp:
            return 200 <= resp.status < 400, f"POST {resp.status}"
    except urllib.error.HTTPError as exc:
        return False, f"POST {exc.code}"
    except Exception as exc:
        return False, str(exc)


def export_unsubscribe_links(entries: list[UnsubscribeEntry], output_path: str) -> None:
    """
    Write unsubscribe entries to *output_path*.

    Supports ``.json`` and ``.txt`` extensions.
    """
    path = Path(output_path)
    suffix = path.suffix.lower()

    if suffix == ".json":
        data = {
            "exported_at": datetime.now().isoformat(),
            "total": len(entries),
            "entries": [
                {
                    "sender": e.sender,
                    "mail_count": e.mail_count,
                    "unsubscribe_url": e.unsubscribe_url,
                }
                for e in entries
            ],
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        # Plain text: one URL per line with sender info
        lines = [
            f"# MailShift – Unsubscribe Links",
            f"# Exported: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"# Total: {len(entries)}",
            "",
        ]
        for e in entries:
            lines.append(f"# {e.sender}  ({e.mail_count} mail)")
            lines.append(e.unsubscribe_url)
            lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")
