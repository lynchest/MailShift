
import sys
import re
import json
import unicodedata
from typing import Optional, Union, Any

def _normalize_for_decision_parse(text: str) -> str:
    """Normalize Turkish dotted/dotless i so SİL/SIL/sıl all match."""
    lowered = text.lower().replace("ı", "i")
    decomposed = unicodedata.normalize("NFKD", lowered)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")

def _extract_decision_from_json(response: str) -> Optional[str]:
    """Extract SIL/TUT from JSON-like model responses."""
    candidates = [response]
    block_match = re.search(r"\{.*\}", response, flags=re.DOTALL)
    if block_match:
        candidates.insert(0, block_match.group(0))

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue

        values_to_check: list[str] = []
        if isinstance(parsed, dict):
            for key in ("decision", "karar", "label", "result"):
                value = parsed.get(key)
                if isinstance(value, str):
                    values_to_check.append(value)
        elif isinstance(parsed, str):
            values_to_check.append(parsed)

        for value in values_to_check:
            normalized = _normalize_for_decision_parse(value)
            if re.search(r"\bsil\b", normalized, flags=re.IGNORECASE):
                return "SIL"
            if re.search(r"\btut\b", normalized, flags=re.IGNORECASE):
                return "TUT"

    return None

def _extract_reason(response: str, decision: str) -> str:
    response_lower = response.lower()
    patterns = [
        r'çünkü\s+(.+?)(?:\.|$)',
        r'nedeni[:\s]\s*(.+?)(?:\.|$)',
        r'sebebi[:\s]\s*(.+?)(?:\.|$)',
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
            if 3 < len(reason) < 150:
                return reason
    if decision == "SIL":
        return "newsletter/spam"
    return "personal/important"

def _parse_llm_response(response: str) -> tuple[str, str]:
    """Shared parser logic for LLM decisions."""
    response = (response or "").strip()
    if not response:
        return ("TUT", "invalid-response")

    # 1. Try direct JSON parsing first (Structured Outputs)
    try:
        parsed = json.loads(response)
        if isinstance(parsed, dict) and "decision" in parsed:
            decision = str(parsed["decision"]).upper()
            if decision in {"SIL", "TUT"}:
                reason = str(parsed.get("reason", "no reason provided"))
                return (decision, reason)
    except json.JSONDecodeError:
        pass # Fallback to manual extraction

    # 2. Extract from <think> tags if present but keep the decision outside
    raw_thinking = ""
    think_match = re.search(r"<think>(.*?)</think>", response, flags=re.DOTALL | re.IGNORECASE)
    if think_match:
        raw_thinking = think_match.group(1).strip()
        # Remove thinking from response to parse decision better
        response = response.replace(think_match.group(0), "").strip()

    # 3. Fallback to extracting from pseudo-JSON or plain text
    decision = _extract_decision_from_json(response)
    if decision is None:
        normalized = _normalize_for_decision_parse(response)
        matches = list(re.finditer(r"\b(sil|tut)\b", normalized, flags=re.IGNORECASE))
        if not matches:
            return ("TUT", "invalid-response")
        first = matches[0].group(1).lower()
        decision = "SIL" if first == "sil" else "TUT"

    reason = _extract_reason(response, decision)

    # If we had thinking, we can prefix it or use it as partial reason
    if raw_thinking:
        # We keep it for logging/debugging but decision priority is clear
        # log.debug(f"LLM Thinking: {raw_thinking[:200]}...")
        pass

    return (decision, reason)

def test_thinking_parsing():
    # Case 1: Response with <think> and a JSON body
    response = "<think>This email is about a sale. It should be deleted.</think>{\"decision\": \"SIL\", \"reason\": \"promosyon\"}"
    decision, reason = _parse_llm_response(response)
    print(f"Test 1 - Decision: {decision}, Reason: {reason}")

    # Case 2: Response with <think> and plain text decision
    response = "<think>Personal mail from a friend.</think>TUT because it is personal"
    decision, reason = _parse_llm_response(response)
    print(f"Test 2 - Decision: {decision}, Reason: {reason}")

if __name__ == "__main__":
    test_thinking_parsing()
