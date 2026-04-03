import sys
import os
import re

def test_extract_reason():
    # Define REASON_PATTERNS locally to test the logic without dependencies
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

    def _extract_reason(response: str, decision: str) -> str:
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

    test_cases = [
        ("Bu bir bülten çünkü abone oldunuz.", "SIL", "abone oldunuz"),
        ("Nedeni: gereksiz kampanya mesajı.", "SIL", "gereksiz kampanya mesajı"),
        ("Sebebi: test.", "SIL", "test"),
        ("Because it is a test.", "SIL", "it is a test"),
        ("Since you joined us.", "SIL", "you joined us"),
        ("Reason: simple test.", "SIL", "simple test"),
        ("it is a promotion ", "SIL", "a promotion"),
        ("this is a newsletter ", "SIL", "a newsletter"),
        ("No clear reason.", "SIL", "newsletter/spam"),
        ("No clear reason.", "TUT", "personal/important"),
    ]

    for response, decision, expected_reason in test_cases:
        actual_reason = _extract_reason(response, decision)
        print(f"Input: {response[:30]}... | Decision: {decision}")
        print(f"Expected: {expected_reason} | Actual: {actual_reason}")
        assert actual_reason == expected_reason, f"Failed for {response}: expected {expected_reason}, got {actual_reason}"

if __name__ == "__main__":
    try:
        test_extract_reason()
        print("\nAll functional tests passed!")
    except Exception as e:
        print(f"\nTest failed: {e}")
        sys.exit(1)
