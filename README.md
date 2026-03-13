# MailShift

Privacy-first newsletter & junk mail cleaner for Gmail and Proton Mail.

## Features

- **Multi provider support**: Gmail (IMAP + App Password), Proton Mail (via Proton Bridge), and Custom IMAP servers
- **Attachment protection**: Emails with attachments are never deleted
- **Two scan modes**:
  - `fast` – heuristic keyword matching (blacklist/whitelist)
  - `pro`  – heuristic + local Ollama LLM for smarter detection
- **Dry-run default** – preview before any deletion
- **Concurrent fetching** – multi-threaded IMAP operations
- **Cache support** – skip re-fetching headers on repeat scans
- **Rich CLI UI** – progress bars, tables, colored output
- **Cleanup history** – view past deletion reports

## Install

```bash
pip install -r requirements.txt
```

## Usage

```bash
# Interactive mode
python main.py

# Non-interactive
python main.py --provider gmail --mode fast \
    --username you@gmail.com --password "app-password"

# Custom IMAP server
python main.py --provider custom --mode fast \
    --username you@example.com --password "your-password" \
    --host imap.example.com --port 993

# Real deletion (disable dry-run)
python main.py --provider gmail --mode pro \
    --username you@gmail.com --password "app-password" --no-dry-run

# View cleanup history
python main.py --history

# Export scan results to CSV (before deletion)
python main.py --provider gmail --mode fast \
    --username you@gmail.com --password "app-password" \
    --export results.csv

# Export to JSON
python main.py --provider gmail --mode fast \
    --username you@gmail.com --password "app-password" \
    --export results.json
```

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
| `--scan-limit` | Max messages to scan | all |
| `--workers` | Concurrent IMAP threads | 8 |
| `--ollama-url` | Ollama API URL | `http://localhost:11434` |
| `--ollama-model` | Ollama model | `qwen2.5:3b` |
| `--history` | Show cleanup history | - |
| `--export` | Export results to CSV/JSON | - |

## Keyword Management

Manage whitelist and blacklist keywords directly from CLI:

```bash
# Add a keyword to whitelist
python main.py --add-whitelist "fatura"

# Remove a keyword from whitelist
python main.py --remove-whitelist "fatura"

# Add a keyword to blacklist
python main.py --add-blacklist "spam"

# Remove a keyword from blacklist
python main.py --remove-blacklist "spam"

# List all keywords
python main.py --list-keywords
```

## How It Works

1. **Connect** – IMAP authentication to inbox
2. **Fetch** – retrieve email headers (concurrent)
3. **Analyze**:
   - Fast: regex match against `blacklist.json` / `whitelist.json`
   - Pro:  run matched mail through Ollama LLM
4. **Review** – table of messages marked for deletion
5. **Delete** – move to Trash (empty Trash to permanent delete)

## Files

```
main.py           CLI entry point
engine.py         IMAP engine + cache
config.py         Config models + keyword patterns
models.py         Data classes
fast_analyzer.py  Heuristic analysis
pro_analyzer.py   LLM analysis (Ollama)
blacklist.json    Keywords → mark for deletion
whitelist.json   Keywords → always keep
```

## Gmail App Password

1. Enable 2-Factor Authentication
2. Go to https://myaccount.google.com/apppasswords
3. Generate 16-char password for Mail

## Proton Bridge

Run Proton Bridge locally, then connect with bridge credentials.
