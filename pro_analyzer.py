"""
pro_analyzer.py – Ollama LLM-based email analysis.
"""

import requests

from config import OllamaConfig

from models import MailMeta, ScanResult


def pro_analyze(meta: MailMeta, ollama_cfg: OllamaConfig) -> ScanResult:
    """
    Tier-2 LLM analysis via Ollama API.
    Falls back to 'TUT' (keep) on any error so we never lose important mail.
    """
    snippet = meta.body_preview or f"{meta.subject} {meta.sender}"
    payload = {
        "model": ollama_cfg.model,
        "system": ollama_cfg.system_prompt,
        "prompt": snippet[: ollama_cfg.max_body_chars],
        "stream": False,
    }
    try:
        resp = requests.post(
            f"{ollama_cfg.base_url}/api/generate",
            json=payload,
            timeout=ollama_cfg.timeout,
        )
        resp.raise_for_status()
        answer = resp.json().get("response", "").strip().upper()
        if "SIL" in answer:
            return ScanResult(mail=meta, decision="SIL", reason="llm:SIL")
        return ScanResult(mail=meta, decision="TUT", reason="llm:TUT")
    except Exception as exc:
        return ScanResult(mail=meta, decision="TUT", reason=f"llm-error:{exc}")
