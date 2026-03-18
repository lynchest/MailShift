import time
import re
import sys
import os

# Add src to path
sys.path.append(os.path.abspath("src"))

from mailshift.core.analyzers.pro import LLMProvider

class OriginalLLMProvider(LLMProvider):
    def analyze(self, meta, fast_reason="", cancel_event=None):
        pass

    def _extract_reason(self, response: str, decision: str) -> str:
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

def benchmark():
    responses = [
        "Bu bir bülten çünkü abone oldunuz.",
        "Nedeni: gereksiz kampanya mesajı.",
        "This is a newsletter.",
        "It is a promotion.",
        "No reason given here.",
        "Since you are a member, we sent this.",
        "Reason: testing the speed of regex.",
        "Sebebi bilinmiyor ama silinmeli."
    ] * 10000

    provider = OriginalLLMProvider()

    # Warmup
    for _ in range(100):
        provider._extract_reason(responses[0], "SIL")

    start = time.time()
    for resp in responses:
        provider._extract_reason(resp, "SIL")
    end = time.time()
    print(f"Original time: {end - start:.4f}s")

if __name__ == "__main__":
    benchmark()
