# AGENTS.md

## Must-follow constraints

- Decision strings are **Turkish**: `"SIL"` (delete) and `"TUT"` (keep). Never use English equivalents anywhere in `ScanResult.decision`.
- `dry_run=True` is the default. Actual deletion only happens with `--no-dry-run`. Never change this default.
- On any LLM or analysis error, fall back to `"TUT"` — never `"SIL"`. Safety-first.
- Emails with `has_attachment=True` must always return `"TUT"` (enforced in `fast_analyzer.py`). Do not remove this guard.
- After making changes to the codebase, ensure that both `AGENTS.md` and `README.md` are updated to reflect the new state and conventions.
- `IMAPConfig.password` is a `SecretStr` (Pydantic). Access its value with `.get_secret_value()`, never direct attribute access.

## Validation before finishing

```
py -3.14 -m pytest tests/
```

No other build or lint step is configured.

## Repo-specific conventions

- **Python interpreter**: `py -3.14` — not `python`, not `python3`.
- **Keyword lists**: `whitelist.json` and `blacklist.json` are plain JSON arrays at the project root. `JUNK_PATTERN` and `WHITELIST_PATTERN` are compiled once at import time in `config.py`. Runtime mutations via `add_to_blacklist()` / `add_to_whitelist()` update the JSON file but do **not** update the in-memory regex — a process restart is required for changes to take effect in analysis.
- **`analyzer.py`** is a re-export shim only. Do not add logic there.
- **Console output**: Use `from ui import console` (Rich console) for any new terminal output. Do not use bare `print()` — stdout is wrapped for UTF-8 on Windows and `print()` bypasses Rich's rendering.
- **No YAML**: The project uses JSON throughout. `requirements.txt` does not include pyyaml.

## Important locations

- `mailshift.db` — SQLite cache, created at **CWD** (not a fixed path). Contains `mails_cache` (header/body cache) and `fetch_checkpoint` (resume table). Persists across runs. Delete it or call `clear_mails_cache()` to force a fresh scan.
- `whitelist.json` / `blacklist.json` — project root, not in a subdirectory.

## Change safety rules

- Pro mode is **two-phase**: heuristic runs first; LLM is called only for `"SIL"` candidates. Any change to fast analysis directly affects what the LLM sees.
- Proton Mail requires Bridge running locally at `127.0.0.1:1143` (no SSL). Gmail is `imap.gmail.com:993` (SSL). These are set via `PROVIDER_DEFAULTS` in `config.py`.
- `delete_mails()` and `move_to_trash()` both call `self._conn.expunge()` after flagging. Removing expunge silently leaves messages flagged but not deleted.
- `fetch_headers_concurrent` is sequential at the IMAP level (single connection, chunked). The name is misleading — do not add actual per-thread IMAP connections without careful locking.
- `AppConfig`, `IMAPConfig`, and `OllamaConfig` are frozen Pydantic models. Do not attempt in-place mutation.

## Known gotchas

- `JUNK_PATTERN` / `WHITELIST_PATTERN` are `None` if the respective JSON file is empty — always guard with `if JUNK_PATTERN:` before calling `.search()`.
- `ScanStats.errors` uses a mutable default in a dataclass — it is initialised via `__post_init__`. Do not set it as a class-level default list.
- `database.py` resolves `DB_FILE = Path("mailshift.db")` relative to CWD at import time. If tests change directory, the DB lands in the wrong place.
- `list_uids()` returns UIDs in **reverse order** (newest first) due to `[::-1]` before applying `scan_limit`. Ordering assumptions matter when reasoning about which emails are skipped by `scan_limit`.
- `pro_analyzer.py` decision parsing accepts plain-text and JSON-like LLM outputs, and normalizes Turkish dotted/dotless `i` so `SİL/SIL/sıl` are treated consistently. Keep the safety fallback to `"TUT"` when no valid decision is found.
- `pro_analyzer.py` uses Ollama `/api/chat` with a JSON decision schema (`decision: SIL|TUT`) and reads `message.content` first (then `response` fallback). This avoids empty-output failures seen with some small models on `/api/generate`.
- `pro_analyzer.py` sends `think: false` and a higher `num_predict` budget for `qwen3.5:2B/4B` so the model does not spend output tokens on hidden reasoning and leave `message.content` empty.
- **Ollama Visibility**: Ollama on Windows often runs without a visible window or system tray icon. To completely terminate it, users must use the Task Manager.
