# AGENTS.md

## Must-follow constraints

- Decision strings are **Turkish**: `"SIL"` (delete) and `"TUT"` (keep). Never use English equivalents anywhere in `ScanResult.decision`.
- `dry_run=True` is the default. Actual deletion only happens with `--no-dry-run`. Never change this default.
- On any LLM or analysis error, fall back to `"TUT"` — never `"SIL"`. Safety-first.
- Emails with `has_attachment=True` must always return `"TUT"` (enforced in `fast_analyzer.py`). Do not remove this guard.
- Only update `AGENTS.md` and `README.md` if the changes introduce new conventions, logic, or significant architectural shifts. Minor bugfixes or trivial cleanups do not require documentation updates.
- `IMAPConfig.password` is a `SecretStr` (Pydantic). Access its value with `.get_secret_value()`, never direct attribute access.

## Validation before finishing

```
py -3.14 -m pytest tests/
```

No other build or lint step is configured.

- Gmail delete/move-to-trash behavior is covered by `tests/test_google_delete_trash.py`.

## Repo-specific conventions

- **Python interpreter**: `py -3.14` — not `python`, not `python3`.
- **Keyword lists**: `whitelist.json` and `blacklist.json` are plain JSON arrays at the project root. `JUNK_PATTERN` and `WHITELIST_PATTERN` are compiled once at import time in `config.py`. Runtime mutations via `add_to_blacklist()` / `add_to_whitelist()` update the JSON file but do **not** update the in-memory regex — a process restart is required for changes to take effect in analysis.
- **Fast analyzer safety guards**: `fast_analyzer.py` has a built-in keep-guard that forces `TUT` for premium lifecycle expiry notices, verification-code/OTP mails, and Drive/cloud storage fullness alerts before junk matching. Keep this guard intact unless there is a stronger safety replacement.
- **Fast analyzer order**: Decision flow is `has_attachment -> whitelist(TUT) -> safety-guard(TUT) -> blacklist(SIL) -> no match(TUT)`. Preserve this order unless a requirement explicitly changes it.
- **Fast analyzer text split**: Whitelist and safety-guard checks run against `full_text` (subject + sender + body_preview). Blacklist matching runs against `content_text` (subject + body_preview only, **excluding sender**) to prevent false positives from legitimate automated senders such as `no-reply@github.com` matching the `"no-reply"` rule.
- **Turkish case normalization**: `fast_analyzer._normalize()` replaces `İ` (U+0130) with `i` then lowercases before matching. Both `full_text` and `content_text` pass through this function, so all regex matches return lowercase tokens in `ScanResult.reason`.
- **`analyzer.py`** is a re-export shim only. Do not add logic there.
- **Console output**: Use `from ui import console` (Rich console) for any new terminal output. Do not use bare `print()` — stdout is wrapped for UTF-8 on Windows and `print()` bypasses Rich's rendering.
- **Progress UI stability**: Keep per-item progress labels short/sanitized (single-line, no control chars) and prefer ASCII status tags (`SIL`/`TUT`) over emoji to avoid wrapped/duplicated-looking progress bars in narrow Windows terminals.
- **Logger stream**: Keep the console logger on `sys.stderr` so Rich progress output on stdout is not visually disrupted by warning/error logs.
- **No YAML**: The project uses JSON throughout. `requirements.txt` does not include pyyaml.

## Important locations

- `mailshift.db` — SQLite cache, created at **CWD** (not a fixed path). Contains `mails_cache` (header/body cache) and `fetch_checkpoint` (resume table). Persists across runs. Delete it or call `clear_mails_cache()` to force a fresh scan.
- `whitelist.json` / `blacklist.json` — project root, not in a subdirectory.
- `credentials.json` — project root, stores provider-based credential cache used by interactive "reuse previous credentials" prompt.

## Change safety rules

- Pro mode is **two-phase**: heuristic runs first; LLM is called only for `"SIL"` candidates. Any change to fast analysis directly affects what the LLM sees.
- Proton Mail requires Bridge running locally at `127.0.0.1:1143` (no SSL). Gmail is `imap.gmail.com:993` (SSL). These are set via `PROVIDER_DEFAULTS` in `config.py`.
- `delete_mails()` and `move_to_trash()` both call `self._conn.expunge()` after flagging. Removing expunge silently leaves messages flagged but not deleted.
- Delete/trash chunk operations and expunge are retry-protected; on socket/SSL EOF-style errors MailEngine reconnects and re-selects `INBOX` before the next retry.
- `fetch_headers_concurrent` is sequential at the IMAP level (single connection, chunked). The name is misleading — do not add actual per-thread IMAP connections without careful locking.
- `AppConfig`, `IMAPConfig`, and `OllamaConfig` are frozen Pydantic models. Do not attempt in-place mutation.
- `hardware.py` detects NVIDIA first, then Intel/AMD GPUs on Windows via `Win32_VideoController` (PowerShell CIM query). Shared VRAM can be estimated from system RAM when `AdapterRAM` is not usable.

## Known gotchas

- `JUNK_PATTERN` / `WHITELIST_PATTERN` are `None` if the respective JSON file is empty — always guard with `if JUNK_PATTERN:` before calling `.search()`.
- `ScanStats.errors` uses a mutable default in a dataclass — it is initialised via `__post_init__`. Do not set it as a class-level default list.
- `database.py` resolves `DB_FILE = Path("mailshift.db")` relative to CWD at import time. If tests change directory, the DB lands in the wrong place.
- `list_uids()` returns UIDs in **reverse order** (newest first) due to `[::-1]` before applying `scan_limit`. Ordering assumptions matter when reasoning about which emails are skipped by `scan_limit`.
- `pro_analyzer.py` decision parsing accepts plain-text and JSON-like LLM outputs, and normalizes Turkish dotted/dotless `i` so `SİL/SIL/sıl` are treated consistently. Keep the safety fallback to `"TUT"` when no valid decision is found.
- `pro_analyzer.py` uses Ollama `/api/chat` with a JSON decision schema (`decision: SIL|TUT`) and reads `message.content` first (then `response` fallback). This avoids empty-output failures seen with some small models on `/api/generate`.
- `pro_analyzer.py` sends `think: false` and a higher `num_predict` budget for `qwen3.5:2B/4B` so the model does not spend output tokens on hidden reasoning and leave `message.content` empty.
- **Ollama Visibility**: Ollama on Windows often runs without a visible window or system tray icon. To completely terminate it, users must use the Task Manager.
- Intel/AMD GPU detection in MailShift is for worker sizing, UI reporting, and **Intel GPU auto-restart**. When an Intel GPU is detected and Ollama is already running without `OLLAMA_INTEL_GPU=1`, `ensure_ollama_intel_gpu()` in `cli_utils.py` automatically stops and restarts Ollama with the correct environment variable. This is invoked in `main.py` before the Ollama health check in Pro mode.
- **Ollama launch helpers**: `_build_ollama_env()`, `_launch_ollama_process()`, `stop_ollama()`, and `_is_ollama_running()` in `cli_utils.py` are reusable primitives that `start_ollama()` and `ensure_ollama_intel_gpu()` both use. `stop_ollama()` uses `taskkill /F /IM ollama.exe` on Windows and `pkill -f ollama` on other platforms.
- **Post-install UX**: When Ollama is newly installed from interactive Pro-mode flow, MailShift prints a "next steps" panel (restart terminal, `ollama serve`, rerun Pro mode). Keep this guidance visible on fallback-to-fast paths and do not require manual `ollama pull` there.
- **Model selection UX**: Recommended model list includes `qwen3.5:0.8B` with `%95 Accurate` label, and missing recommended models are auto-downloaded/verified from the selection screen.
