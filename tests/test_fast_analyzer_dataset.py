import json
import pytest
from pathlib import Path
from models import MailMeta
from fast_analyzer import fast_analyze

def load_dataset():
    dataset_path = Path(__file__).parent / "test_ai_dataset.json"
    if not dataset_path.exists():
        return []
    with open(dataset_path, "r", encoding="utf-8") as f:
        return json.load(f)

@pytest.mark.parametrize("item", load_dataset())
def test_fast_analyzer_heuristic(item):
    """
    Test FastAnalyzer against the AI dataset.
    Note: Fast mode is expected to have lower accuracy than Pro mode,
    but it should still handle common cases.
    """
    meta = MailMeta(
        uid=item["id"],
        subject=item["subject"],
        sender=item["sender"],
        body_preview=item["body_preview"]
    )
    
    res = fast_analyze(meta)
    
    # We don't necessarily want this to fail the whole test suite if one case fails,
    # as heuristic mode is inherently less accurate. 
    # But for a dedicated dataset test, we can assert or just log.
    # Given the user wants to "test" Fast mode, assertion is appropriate for cases 
    # we expect heuristics to catch.
    
    # However, since this is an evaluation dataset, let's keep it as a test
    # but maybe focus on specific categories if needed.
    # For now, let's assert equality to see which ones fail in standard pytest output.
    assert res.decision == item["expected_decision"], f"ID: {item['id']} | Subject: {item['subject']} | Reason: {res.reason}"
