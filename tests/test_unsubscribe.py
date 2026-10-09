import json
import http.client
import socket
import shutil
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

try:
    import pytest
except ImportError:
    pytest = None

from mailshift.utils.unsubscribe import (
    _PinnedHTTPSConnection,
    _SafeURLResolver,
    UnsubscribeEntry,
    build_unsubscribe_entries,
    export_unsubscribe_links,
    is_safe_url,
    perform_unsubscribe,
    unsubscribe_log_host,
)
from mailshift.models.models import MailMeta, ScanResult


def test_build_unsubscribe_entries():
    """Verify deduplication and sorting of unsubscribe entries."""
    results = [
        ScanResult(mail=MailMeta(uid="1", sender="sender1@example.com", unsubscribe_url="http://url1")),
        ScanResult(mail=MailMeta(uid="2", sender="sender1@example.com", unsubscribe_url="http://url1")),
        ScanResult(mail=MailMeta(uid="3", sender="sender2@example.com", unsubscribe_url="http://url2")),
        ScanResult(mail=MailMeta(uid="4", sender="sender3@example.com", unsubscribe_url="")),  # Should be ignored
        ScanResult(mail=MailMeta(uid="5", sender="sender2@example.com", unsubscribe_url="http://url2")),
        ScanResult(mail=MailMeta(uid="6", sender="sender2@example.com", unsubscribe_url="http://url2")),
    ]

    entries = build_unsubscribe_entries(results)

    # Should have 2 unique URLs, sorted by count descending
    assert len(entries) == 2

    # http://url2 has 3 occurrences
    assert entries[0].unsubscribe_url == "http://url2"
    assert entries[0].mail_count == 3
    assert entries[0].sender == "sender2@example.com"

    # http://url1 has 2 occurrences
    assert entries[1].unsubscribe_url == "http://url1"
    assert entries[1].mail_count == 2
    assert entries[1].sender == "sender1@example.com"


def test_export_unsubscribe_links_json(tmp_path):
    """Verify JSON export format and content."""
    entries = [
        UnsubscribeEntry(sender="sender2@example.com", unsubscribe_url="http://url2", mail_count=3),
        UnsubscribeEntry(sender="sender1@example.com", unsubscribe_url="http://url1", mail_count=2),
    ]
    output_file = tmp_path / "test_unsubscribe.json"

    export_unsubscribe_links(entries, str(output_file))

    assert output_file.exists()
    content = json.loads(output_file.read_text(encoding="utf-8"))

    assert "exported_at" in content
    assert content["total"] == 2
    assert len(content["entries"]) == 2

    # Verify first entry
    assert content["entries"][0]["sender"] == "sender2@example.com"
    assert content["entries"][0]["mail_count"] == 3
    assert content["entries"][0]["unsubscribe_url"] == "http://url2"


def test_export_unsubscribe_links_txt(tmp_path):
    """Verify TXT export format and content."""
    entries = [
        UnsubscribeEntry(sender="sender2@example.com", unsubscribe_url="http://url2", mail_count=3),
        UnsubscribeEntry(sender="sender1@example.com", unsubscribe_url="http://url1", mail_count=2),
    ]
    output_file = tmp_path / "test_unsubscribe.txt"

    export_unsubscribe_links(entries, str(output_file))

    assert output_file.exists()
    lines = output_file.read_text(encoding="utf-8").splitlines()

    assert "# MailShift – Unsubscribe Links" in lines[0]
    assert "# Total: 2" in lines[2]

    content = output_file.read_text(encoding="utf-8")
    assert "# sender2@example.com  (3 mail)" in content
    assert "http://url2" in content
    assert "# sender1@example.com  (2 mail)" in content
    assert "http://url1" in content


def test_is_safe_url():
    """Verify SSRF protection logic."""
    # Safe URLs
    assert is_safe_url("http://example.com/unsubscribe") is True
    assert is_safe_url("https://google.com") is True

    # Unsafe schemes
    assert is_safe_url("ftp://example.com") is False
    assert is_safe_url("file:///etc/passwd") is False
    assert is_safe_url("gopher://example.com") is False

    # Private/Local IPs
    assert is_safe_url("http://127.0.0.1/unsubscribe") is False
    assert is_safe_url("http://localhost/unsubscribe") is False
    assert is_safe_url("http://192.168.1.1/unsubscribe") is False
    assert is_safe_url("http://10.0.0.1/unsubscribe") is False
    assert is_safe_url("http://172.16.0.1/unsubscribe") is False
    assert is_safe_url("http://[::1]/unsubscribe") is False


def test_perform_unsubscribe_blocked():
    """Verify that perform_unsubscribe blocks unsafe URLs."""
    success, message = perform_unsubscribe("http://127.0.0.1/unsubscribe")
    assert success is False
    assert "blocked" in message.lower()


def test_perform_unsubscribe_redirect_blocked():
    """Verify that perform_unsubscribe blocks unsafe redirects."""
    # Mock opener.open to simulate a redirect that fails safety check
    # In reality, SafeRedirectHandler would raise the error
    # Here we can just verify it uses our is_safe_url

    import urllib.request
    from mailshift.utils.unsubscribe import SafeRedirectHandler

    handler = SafeRedirectHandler()
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        handler.redirect_request(None, None, 302, "Found", {}, "http://127.0.0.1/malicious")
    assert excinfo.value.code == 403
    assert "unsafe URL" in excinfo.value.reason


def test_resolver_rejects_hostname_with_any_non_public_address(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.4", 0)),
        ],
    )
    assert _SafeURLResolver().resolve("https://mixed.example/") is None


def test_perform_unsubscribe_pins_ip_and_preserves_host(monkeypatch):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(self.headers["Host"])
            if self.path == "/leave":
                self.send_response(302)
                self.send_header("Location", f"http://next.example:{port}/done")
            else:
                self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    port = server.server_port
    original_getaddrinfo = socket.getaddrinfo
    original_connect = socket.create_connection
    dns_calls = []
    connect_targets = []

    def fake_getaddrinfo(host, requested_port, *args, **kwargs):
        if host in ("mail.example", "next.example"):
            dns_calls.append(host)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]
        return original_getaddrinfo(host, requested_port, *args, **kwargs)

    def fake_connect(address, timeout=None, source_address=None):
        connect_targets.append(address)
        if address[0] == "93.184.216.34":
            address = ("127.0.0.1", address[1])
        return original_connect(address, timeout, source_address)

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", fake_connect)
    try:
        success, message = perform_unsubscribe(f"http://mail.example:{port}/leave")
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert (success, message) == (True, "GET 200")
    assert dns_calls == ["mail.example", "next.example"]
    assert connect_targets == [("93.184.216.34", port)] * 2
    assert received == [f"mail.example:{port}", f"next.example:{port}"]


def test_pinned_https_uses_original_hostname_for_tls_sni(monkeypatch):
    calls = {}

    class FakeSocket:
        def setsockopt(self, *args):
            pass

    class FakeContext:
        def wrap_socket(self, sock, *, server_hostname):
            calls["server_hostname"] = server_hostname
            return sock

    conn = _PinnedHTTPSConnection(
        "mail.example", pinned_ip="93.184.216.34", context=FakeContext()
    )
    monkeypatch.setattr(
        conn,
        "_create_connection",
        lambda address, timeout, source: calls.update(target=address) or FakeSocket(),
    )
    conn.connect()
    assert calls == {
        "target": ("93.184.216.34", 443),
        "server_hostname": "mail.example",
    }


def test_unsubscribe_log_host_omits_path_and_token():
    assert unsubscribe_log_host("https://mail.example:8443/unsubscribe/secret?token=abc#fragment") == "mail.example"


if __name__ == "__main__":
    # Manual execution for environments without pytest
    print("Running tests standalone...")
    test_build_unsubscribe_entries()
    test_is_safe_url()
    test_perform_unsubscribe_blocked()

    tmp_dir = tempfile.mkdtemp()
    try:
        tmp_path = Path(tmp_dir)
        test_export_unsubscribe_links_json(tmp_path)
        test_export_unsubscribe_links_txt(tmp_path)
    finally:
        shutil.rmtree(tmp_dir)

    print("All tests passed successfully!")
