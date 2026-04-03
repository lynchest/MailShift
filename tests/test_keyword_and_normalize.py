import pytest

from mailshift.config.config import KeywordManager, keyword_manager
from mailshift.core.analyzers.fast import _normalize


def test_fast_normalize_handles_none_and_empty() -> None:
    assert _normalize(None) == ""
    assert _normalize("") == ""


def test_fast_normalize_handles_turkish_i_variants() -> None:
    assert _normalize("I") == "ı"
    assert _normalize("İ") == "i"
    assert _normalize("Istanbul İndirim") == "ıstanbul indirim"


@pytest.mark.parametrize(
    ("keyword", "expected"),
    [
        ("weekly newsletter", "newsletter"),
        ("unsubscribe now", "subscription"),
        ("flash sale", "promotion"),
        ("random token", "uncategorized"),
    ],
)
def test_infer_category(keyword: str, expected: str) -> None:
    assert keyword_manager._infer_category(keyword) == expected


def test_reload_migrates_legacy_blacklist_with_inferred_categories() -> None:
    manager = KeywordManager.__new__(KeywordManager)

    state = {
        "whitelist.json": ["invoice"],
        "blacklist.json": ["weekly newsletter", "unsubscribe", "flash sale"],
    }

    def fake_load(filename, default):
        return state.get(filename, default)

    def fake_save(filename, data):
        state[filename] = data

    manager._load_json = fake_load  # type: ignore[attr-defined]
    manager._save_json = fake_save  # type: ignore[attr-defined]

    manager.reload()

    assert manager.blacklist_dict["newsletter"] == ["weekly newsletter"]
    assert manager.blacklist_dict["subscription"] == ["unsubscribe"]
    assert manager.blacklist_dict["promotion"] == ["flash sale"]
    assert manager.get_category_for_match("FLASH SALE") == "promotion"
    assert manager.get_category_for_match("missing") == "uncategorized"
