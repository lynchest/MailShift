# MailShift

Privacy-first newsletter & junk mail cleaner for Gmail and Proton Mail.

## Screenshots

### Welcome Screen

![MailShift Welcome Screen](first.png)

### Fast/Pro Mode and AI Model Selection

![MailShift Fast/Pro Mode and AI Model Selection](second.png)

## Features

- **Multi provider support**: Gmail (IMAP + App Password), Proton Mail (Requires [Proton Bridge](https://proton.me/mail/bridge) and a paid account), and Custom IMAP servers
- **Proton Bridge preflight check**: Proton mode probes `127.0.0.1:1143` before IMAP login and guides you to start Bridge with a retry prompt
- **Attachment protection**: Emails with attachments are never deleted
- **Phishing detection limitation**: This tool is not designed to detect and delete phishing emails. LLM models cannot reliably distinguish between phishing and legitimate emails.
- **Fast-mode safety guards**: Premium lifecycle expiry notices, verification code (OTP) emails, and Google Drive/cloud storage fullness alerts are force-kept in Fast mode before junk checks
- **Fast-mode false-positive reduction**: Blacklist matching excludes the sender address so legitimate automated senders (e.g. `no-reply@github.com`) never trigger a junk decision; whitelist and safety-guard still consider the full sender context
- **Turkish case normalization**: Fast-mode text is normalized (Turkish İ → i) before heuristic matching, fixing missed matches on properly-capitalised Turkish subjects
- **Two scan modes**:
  - `fast` – heuristic keyword matching (blacklist/whitelist)
  - `pro`  – two-phase analysis: heuristic + local Ollama LLM for smarter detection
    - Phase 1: Fast heuristic scan
    - Phase 2: LLM verification on suspicious messages
        - Robust decision parser accepts `SIL/TUT` text or JSON-style outputs and normalizes Turkish `SİL/SIL` variants
        - Ollama call uses `/api/chat` with structured JSON decision output for better small-model reliability
        - Pro mode disables model "thinking" output and uses a larger generation budget to prevent empty decision responses on 2B/4B models
    - **LM Studio auto-download support**: If no LM Studio model is loaded, interactive Pro mode can trigger LM Studio's download API (`/api/v1/models/download`) and track progress via `/api/v1/models/download/status`
    - **LM Studio install options**: If LM Studio is missing, interactive Pro mode can offer Windows install via `winget install ElementLabs.LMStudio` or direct download from the official website (`https://lmstudio.ai`)
    - **LM Studio server lifecycle**: If LM Studio is installed but local server is not running, MailShift can auto-run `lms server start`; if it started the server itself, it attempts `lms server stop` during cleanup
- **Body preview in Pro mode**: Fetches email body content for better LLM analysis
- **Dry-run default** – preview before any deletion
- **Dry-run history logs**: Dry-run candidate results are also saved under `logs/cleanup_log_*.json` for later review
- **OS Keyring** (Windows Credential Manager) — stores provider-based credentials securely via the `keyring` library for the interactive "reuse previous credentials" prompt. No longer stored in plain-text JSON files.
- **Unsubscribe suggestions**: After a scan, MailShift detects `List-Unsubscribe` headers and offers three options — auto-unsubscribe from all detected senders, pick individual senders from a numbered list, or export all unsubscribe links to a JSON/TXT file for manual processing. Available in both dry-run and live modes.
- **Delete options**: Permanent delete or move to Trash
- **Resilient IMAP deletion**: Delete/trash chunks and expunge retry with exponential back-off and automatic IMAP reconnect on SSL/EOF disconnects
- **Concurrent fetching** – multi-threaded IMAP operations
- **Auto worker calculation** – automatically calculates optimal thread count based on hardware
    - Detects NVIDIA GPUs and Intel/AMD GPUs on Windows for Pro mode worker sizing
    - Intel/AMD integrated GPU VRAM may be estimated from shared system RAM when dedicated VRAM is not exposed by the driver
    - Manual worker input is safety-clamped to a backend-aware upper limit (VRAM/RAM/CPU caps); CLI prints a clear warning when clamped
    - Fast mode does not use worker parallelism for analysis; configuration panel shows workers as "not used" to avoid misleading UX
    - Pro mode auto-worker can learn from previous phase-2 metrics (timeout/error/p95 latency) and warm-start the next run from local `worker_profiles.json` recommendations
    - Power users can enable a one-time hardware worker probe with `--power-worker-probe`; this preference is persisted in `power_user_settings.json` and reused in later runs
- **Cache support** – skip re-fetching headers on repeat scans
- **Rich CLI UI** – progress bars, tables, colored output with Turkish/English
    - Progress status labels are sanitized and shortened to stay single-line on narrow terminals (prevents duplicated-looking bars)
    - Live progress uses ASCII status tags (`SIL`/`TUT`) for more stable rendering across Windows terminals
- **Cleanup history** – view past deletion reports
- **Logging** – detailed operation logs
    - Console warnings/errors are written to stderr to reduce interference with live progress rendering

## Quick Start (Recommended)

MailShift is now available on PyPI.

```bash
# Install from PyPI
pip install mailshift

# Start interactive mode
mailshift
```

That's it. Most users only need these two commands.

## Install

### Option A: PyPI (recommended)

```bash
# Install
pip install mailshift

# Upgrade
pip install --upgrade mailshift

# Optional: NVIDIA extra for improved Pro-mode worker sizing
pip install "mailshift[nvidia]"
```

### Option B: Source install (development)

```bash
git clone https://github.com/lynchest/MailShift.git
cd MailShift
pip install -r requirements.txt
python main.py
```

## Usage

Use the `mailshift` command if installed via `pip install mailshift`.

```bash
# Interactive mode
mailshift

# Non-interactive
mailshift --provider gmail --mode fast \
    --username you@gmail.com --password "app-password"

# Custom IMAP server
mailshift --provider custom --mode fast \
    --username you@example.com --password "your-password" \
    --host imap.example.com --port 993

# Pro mode with LLM (two-phase analysis)
mailshift --provider gmail --mode pro \
    --username you@gmail.com --password "app-password"

# Real deletion (disable dry-run)
mailshift --provider gmail --mode pro \
    --username you@gmail.com --password "app-password" --no-dry-run

# Scan only a date window (IMAP SINCE/BEFORE)
mailshift --provider gmail --mode fast \
    --username you@gmail.com --password "app-password" \
    --since 2025-01-01 --before 2026-01-01

# Move to Trash instead of permanent delete
# (select option 2 when prompted)

# View cleanup history
mailshift --history

# Export scan results to CSV (before deletion)
mailshift --provider gmail --mode fast \
    --username you@gmail.com --password "app-password" \
    --export results.csv

# Export to JSON
mailshift --provider gmail --mode fast \
    --username you@gmail.com --password "app-password" \
    --export results.json

# Custom Ollama settings
mailshift --provider gmail --mode pro \
    --username you@gmail.com --password "app-password" \
    --ollama-url http://localhost:11434 \
    --ollama-model qwen3.5:2B

# Custom system prompt for LLM
mailshift --provider gmail --mode pro \
    --username you@gmail.com --password "app-password" \
    --ollama-prompt "Custom prompt here"
```

If you run from source, replace `mailshift` with `python main.py`.

## Options

| Flag | Description | Default |
|------|-------------|---------|
| `--provider` | `gmail`, `proton` or `custom` | prompt |
| `--mode` | `fast` or `pro` | prompt |
| `--username` | IMAP email/username | prompt |
| `--password` | App Password / Bridge password | prompt |
| `--host` | Custom IMAP server host | - |
| `--port` | Custom IMAP server port | 993 |
| `--use-ssl` | Use SSL for IMAP | enabled |
| `--dry-run` | Preview only (no deletion) | enabled |
| `--no-dry-run` | Actually delete emails | - |
| `--scan-limit` | Max messages to scan | all |
| `--since` | Scan emails on/after date (`YYYY-MM-DD` or `DD-Mon-YYYY`) | - |
| `--before` | Scan emails before date (`YYYY-MM-DD` or `DD-Mon-YYYY`) | - |
| `--ollama-url` | Ollama API URL | `http://localhost:11434` |
| `--ollama-model` | Ollama model | `qwen3.5:2B` |
| `--ollama-prompt` | Custom system prompt for LLM | default prompt |
| `--workers` | Manual worker hint (Pro mode); values above safe limit are auto-clamped | auto |
| `--power-worker-probe` | Enable persisted power-user hardware probe for worker auto tuning | saved preference |
| `--no-power-worker-probe` | Disable persisted power-user hardware probe | saved preference |
| `--history` | Show cleanup history | - |
| `--export` | Export results to CSV/JSON | - |
| `--uninstall` | Remove MailShift from system | - |

- In interactive credential flow, MailShift can store credentials securely in the **OS Keyring** (e.g. Windows Credential Manager) using the **`keyring`** library.
- **Security**: Passwords and sensitive data are handled using **Pydantic `SecretStr`** in memory and stored in the encrypted system vault on disk. They are never saved in plain text files or accidentally printed in logs. 
- On later runs, if saved credentials exist, it asks whether to reuse previous values from the secure vault.
- You can still override credentials anytime via `--username` and `--password` flags.

## Keyword Management

Manage whitelist and blacklist keywords directly from CLI:

```bash
# Add a keyword to whitelist
mailshift --add-whitelist "fatura"

# Remove a keyword from whitelist
mailshift --remove-whitelist "fatura"

# Add a keyword to blacklist
mailshift --add-blacklist "spam"

# Remove a keyword from blacklist
mailshift --remove-blacklist "spam"

# List all keywords
mailshift --list-keywords
```

## How It Works

1. **Connect** – IMAP authentication to inbox
2. **Fetch** – retrieve email headers (concurrent), body for Pro mode
3. **Analyze**:
    - Fast: whitelist-first flow (`whitelist.json` match => `TUT`), then suspicious check via `blacklist.json` (`SIL`)
    - Plus built-in safety guards that force `TUT` for premium expiry lifecycle notices, verification-code mails, and Drive/cloud storage quota-full alerts
   - Pro: two-phase analysis
     - Phase 1: heuristic scan to find suspicious messages
     - Phase 2: run matched mail through Ollama LLM for verification
4. **Review** – table of messages marked for deletion
5. **Unsubscribe** *(optional)* – after review, MailShift checks for `List-Unsubscribe` HTTP URLs and offers:
   - Auto-unsubscribe from all detected senders at once
   - Select individual senders from a numbered list
   - Export all links to `logs/unsubscribe_links.json` (or a custom path) for manual processing
6. **Delete** – permanent delete or move to Trash (empty Trash to permanent delete)
    - Delete/trash operations automatically retry transient IMAP/SSL socket failures and reconnect before retrying

## Files

```
main.py           CLI entry point
engine.py         IMAP engine + cache
config.py         Config models + keyword patterns
models.py         Data classes
hardware.py       System info + worker calculation
fast_analyzer.py  Heuristic analysis
pro_analyzer.py   LLM analysis (Ollama)
database.py       Cache storage (SQLite)
history.py        Cleanup history + export
logger.py         Logging utilities
ui.py             Rich UI components
cli_utils.py      CLI helper functions
blacklist.json    Keywords → mark for deletion
whitelist.json   Keywords → always keep
```

## Gmail App Password

1. Enable 2-Factor Authentication
2. Go to https://myaccount.google.com/apppasswords
3. Generate 16-char password for Mail

## Proton Bridge

Run Proton Bridge locally, then connect with bridge credentials.
If Bridge is closed, MailShift now checks `127.0.0.1:1143` first and prompts you to start Bridge before retrying IMAP login.

## Requirements

- Python 3.10+
- IMAP access to your email provider
- For Pro mode: [Ollama](https://ollama.com) running locally.
    - On Windows with Intel GPU, MailShift starts `ollama serve` with `OLLAMA_INTEL_GPU=1` (when auto-start is used) and requests higher GPU layer offload in Pro mode to reduce unintended CPU-only inference.
    - Intel/AMD GPU acceleration still depends on Ollama backend support/driver stack; if Ollama cannot offload, inference can continue on CPU even when MailShift detects the GPU.
  - **Power User Tip**: Set the `OLLAMA_NUM_PARALLEL` environment variable to increase concurrent workers (default is 4).
  - **Note**: To close Ollama completely on Windows, you must use the Task Manager as it often runs without a visible window or system tray icon.

## Ollama Kurulduktan Sonra Ne Yapmalıyım?

Pro mode seçiminde Ollama yüklü değilse MailShift otomatik kurulum önerebilir. Kurulum bittiğinde Pro mode'a devam edebilmek için şu adımları izleyin:

1. Terminali kapatıp yeniden açın (PATH güncellemesi için).
2. Ollama servisini başlatın: `ollama serve`
3. MailShift'i yeniden çalıştırıp Pro mode seçin.
4. Model seçim ekranında eksik önerilen model otomatik indirilir (manuel `ollama pull` gerekmez).

Not: Uygulama bu adımları ayrıca panel olarak da gösterir ve otomatik başlatma denemesi yapar. Otomatik başlatma başarısız olursa Fast mode'a güvenli şekilde geri döner.

Not: Önerilen modeller listesine `qwen3.5:0.8B` eklidir ve seçim ekranında `%95 Accurate` etiketiyle gösterilir.

## Tests

- Run all tests with: `py -3.14 -m pytest tests/`
- Gmail delete and move-to-trash flows are covered in `tests/test_google_delete_trash.py`.
