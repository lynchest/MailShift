import sys
import os
import re

# Add src to path
sys.path.append(os.path.abspath("src"))

from mailshift.core.analyzers.pro import OllamaProvider
from mailshift.config.config import OllamaConfig

def test_extract_reason():
    cfg = OllamaConfig(model="test", base_url="http://test")
    provider = OllamaProvider(cfg)

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
        actual_reason = provider._extract_reason(response, decision)
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
