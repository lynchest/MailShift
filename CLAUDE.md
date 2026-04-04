# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run all tests
py -3.14 -m pytest tests/

# Run a single test file
py -3.14 -m pytest tests/test_fast_analyzer.py

# Run the app
python main.py
```

**Python interpreter**: Always use `py -3.14`, not `python` or `python3`. No lint step is configured.

## Architecture

MailShift is a privacy-first, AI-assisted Gmail/Proton/IMAP mail cleaner. Entry point: `main.py` (root) → `src/mailshift/main.py` (Click CLI).

**Data flow for a scan**:
1. `main.py` + `ui/cli.py` — collect provider, credentials (stored in OS Keyring), scan mode, LLM backend
2. `config/config.py` — assembles frozen Pydantic models (`AppConfig`, `IMAPConfig`, `OllamaConfig`/`LMStudioConfig`); `PROVIDER_DEFAULTS` maps provider names to IMAP hosts
3. `core/engine.py` (`MailEngine`) — IMAP connect, `list_uids()` (returns newest-first), `fetch_headers_concurrent()` (chunked, single connection despite the name), SQLite checkpoint resume via `db/database.py`
4. **Fast mode** (`core/analyzers/fast.py`): heuristic-only. Decision order: `has_attachment → whitelist(TUT) → safety-guard(TUT) → blacklist(SIL) → no-match(TUT)`
5. **Pro mode** (`core/analyzers/pro.py`): two-phase — Fast runs first, then LLM (Ollama or LM Studio) is called only for `SIL` candidates
6. `core/session.py` — `LLMWorker` (threaded) + Rich progress handlers
7. `core/engine.py` — `delete_mails()` / `move_to_trash()`: chunk, flag `\Deleted`, expunge, retry on SSL/EOF
8. `utils/history.py` — JSON logs under `logs/`, CSV/JSON export; `ui/styles.py` — Rich result tables
9. **Unsubscribe flow** (`utils/unsubscribe.py`): after scan results are shown, `_prompt_unsubscribe()` in `main.py` collects all `SIL` results that carry a `List-Unsubscribe` HTTP URL, deduplicates by URL, and presents a 4-option menu (auto-all / select / export / skip). Triggered in both dry-run and live paths.

**Key files**:
- `blacklist.json` / `whitelist.json` — project root, compiled to regex at import time in `config.py`. `JUNK_PATTERN`/`WHITELIST_PATTERN` may be `None` if empty — always guard with `if pattern:` before `.search()`
- `mailshift.db` — SQLite cache at CWD (not a fixed path); delete to force fresh scan
- `utils/unsubscribe.py` — `UnsubscribeEntry`, `build_unsubscribe_entries()`, `perform_unsubscribe()`, `export_unsubscribe_links()`

## Critical Conventions

- **Decision strings are Turkish**: `"SIL"` (delete), `"TUT"` (keep). Never use English equivalents in `ScanResult.decision`.
- **`dry_run=True` is the immutable default.** Actual deletion only occurs with `--no-dry-run`.
- **Any LLM or analysis error must fall back to `"TUT"`**, never `"SIL"`. Safety-first.
- **Emails with `has_attachment=True` always return `"TUT"`** — do not remove this guard in `fast_analyzer.py`.
- **`IMAPConfig.password` is `SecretStr`** — access via `.get_secret_value()` only, never direct attribute.
- **Fast analyzer text split**: whitelist + safety-guard check `full_text` (subject + sender + body); blacklist checks `content_text` (subject + body only, sender excluded) to prevent false positives from senders like `no-reply@github.com`.
- **Turkish normalization**: `fast_analyzer._normalize()` replaces `İ` (U+0130) → `i` before lowercasing. All regex tokens in `ScanResult.reason` are lowercase.
- **Console output**: Use `from ui import console` (Rich). Never use bare `print()` — it bypasses Rich's UTF-8 wrapper.
- **Logger on `sys.stderr`**: keeps Rich progress on stdout visually clean.
- **No YAML**: JSON throughout (`blacklist.json`, `whitelist.json`, logs).
- `analyzer.py` is a re-export shim only — do not add logic there.
- Pydantic models `AppConfig`, `IMAPConfig`, `OllamaConfig` are frozen — no in-place mutation.

## Gotchas

- `MailMeta.unsubscribe_url` is populated from the `List-Unsubscribe` header during `_fetch_mails_bulk()` using `_UNSUB_URL_RE` (HTTP-only; `mailto:` entries are silently ignored). The second body-fetch pass only updates `body_preview`, so the URL from the first header fetch is preserved.
- `database.py` auto-migrates old DBs: `init_db()` runs `ALTER TABLE mails_cache ADD COLUMN unsubscribe_url TEXT DEFAULT ''` if the column is absent.
- `perform_unsubscribe()` tries GET first, then falls back to an RFC 8058 one-click POST (`List-Unsubscribe=One-Click`). Never import `requests` for this — use stdlib `urllib.request` only.
- `fetch_headers_concurrent` is sequential at the IMAP level (single connection). Do not add per-thread IMAP connections without careful locking.
- Runtime mutations via `add_to_blacklist()` / `add_to_whitelist()` update JSON but **do not update the in-memory regex** — a process restart is needed.
- `list_uids()` reverses UIDs (`[::-1]`) so newest-first; `scan_limit` skips oldest emails.
- `database.py` resolves `DB_FILE` relative to CWD at import time — if tests change directory, the DB lands in the wrong place.
- `ScanStats.errors` is initialized in `__post_init__`, not as a class-level default list.
- Pro mode sends `think: false` and higher `num_predict` for `qwen3.5` models to avoid empty `message.content` from hidden reasoning tokens.
- LM Studio: after analysis, Pro mode unloads the model from VRAM via `POST /api/v1/models/unload`.
- Proton Mail requires Bridge at `127.0.0.1:1143` (no SSL); Gmail is `imap.gmail.com:993` (SSL).
