"""
pro_analyzer.py – Ollama LLM-based email analysis.
"""

import re

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
        raw_response = resp.json().get("response", "").strip()
        
        decision, reason = _parse_llm_response(raw_response)
        
        return ScanResult(mail=meta, decision=decision, reason=f"llm:{decision} - {reason}")
    except Exception as exc:
        return ScanResult(mail=meta, decision="TUT", reason=f"llm-error:{exc}")


def _parse_llm_response(response: str) -> tuple[str, str]:
    """
    Parse LLM response to extract decision and optional reason.
    Returns (decision: str, reason: str)
    """
    upper_response = response.upper()
    
    has_sil = "SIL" in upper_response
    has_tut = "TUT" in upper_response
    
    if has_sil and not has_tut:
        decision = "SIL"
    elif has_tut and not has_sil:
        decision = "TUT"
    elif has_sil and has_tut:
        decision = "SIL" if upper_response.find("SIL") < upper_response.find("TUT") else "TUT"
    else:
        return ("TUT", "invalid-response")
    
    reason = _extract_reason(response, decision)
    return (decision, reason)


def _extract_reason(response: str, decision: str) -> str:
    """
    Try to extract a short reason from the LLM response.
    Looks for patterns like: 'because...', 'since...', 'reason:...', etc.
    """
    response_lower = response.lower()
    
    patterns = [
        r'because\s+(.+?)(?:\.|$)',
        r'since\s+(.+?)(?:\.|$)',
        r'reason:\s*(.+?)(?:\.|$)',
        r'it is\s+(a\s+\w+)\s+',
        r'this is\s+(a\s+\w+)\s+',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, response_lower)
        if match:
            reason = match.group(1).strip()
            if 3 < len(reason) < 50:
                return reason
    
    if decision == "SIL":
        return "newsletter/spam"
    return "personal/important"
