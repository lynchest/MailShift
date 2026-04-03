import time
import re

def extract_reason_original(response: str, decision: str) -> str:
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

REASON_PATTERNS = [
    re.compile(r'çünkü\s+(.+?)(?:\.|$)', re.IGNORECASE),
    re.compile(r'nedeni[:\s]\s*(.+?)(?:\.|$)', re.IGNORECASE),
    re.compile(r'sebebi[:\s]\s*(.+?)(?:\.|$)', re.IGNORECASE),
    re.compile(r'because\s+(.+?)(?:\.|$)', re.IGNORECASE),
    re.compile(r'since\s+(.+?)(?:\.|$)', re.IGNORECASE),
    re.compile(r'reason:\s*(.+?)(?:\.|$)', re.IGNORECASE),
    re.compile(r'it is\s+(a\s+\w+)\s+', re.IGNORECASE),
    re.compile(r'this is\s+(a\s+\w+)\s+', re.IGNORECASE),
]

def extract_reason_optimized(response: str, decision: str) -> str:
    # Note: original used response.lower() and re.search(pattern, response_lower)
    # The optimized version uses re.IGNORECASE in compiled patterns and searches in original response
    # Actually original response_lower might be better if we want to avoid multiple lower() calls if not using IGNORECASE,
    # but re.IGNORECASE is generally efficient.
    # Wait, original does:
    # response_lower = response.lower()
    # match = re.search(pattern, response_lower)
    # Let's stick close to original for now to ensure exact same behavior.

    response_lower = response.lower()
    for pattern in REASON_PATTERNS:
        match = pattern.search(response_lower)
        if match:
            reason = match.group(1).strip()
            if 3 < len(reason) < 150:
                return reason
    if decision == "SIL":
        return "newsletter/spam"
    return "personal/important"

def benchmark():
    responses = [
        "Bu bir bülten çünkü abone oldunuz.",
        "Nedeni: gereksiz kampanya mesajı.",
        "This is a newsletter.",
        "It is a promotion.",
        "No reason given here.",
        "Since you are a member, we sent this.",
        "Reason: testing the speed of regex.",
        "Sebebi bilinmiyor ama silinmeli.",
        "it is a test",
        "this is a test"
    ] * 10000

    # Warmup
    for _ in range(100):
        extract_reason_original(responses[0], "SIL")
        extract_reason_optimized(responses[0], "SIL")

    start = time.time()
    for resp in responses:
        extract_reason_original(resp, "SIL")
    end = time.time()
    original_time = end - start
    print(f"Original time: {original_time:.4f}s")

    start = time.time()
    for resp in responses:
        extract_reason_optimized(resp, "SIL")
    end = time.time()
    optimized_time = end - start
    print(f"Optimized time: {optimized_time:.4f}s")

    improvement = (original_time - optimized_time) / original_time * 100
    print(f"Improvement: {improvement:.2f}%")

if __name__ == "__main__":
    benchmark()
