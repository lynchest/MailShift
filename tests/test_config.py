import pytest
from unittest.mock import patch, mock_open
from pydantic import ValidationError

from mailshift.config.config import (
    Provider,
    Mode,
    IMAPConfig,
    OllamaConfig,
    RateLimitConfig,
    AppConfig,
    build_imap_config,
    add_to_whitelist,
    remove_from_whitelist,
    add_to_blacklist,
    remove_from_blacklist,
)


def test_imap_config_frozen():
    config = IMAPConfig(host="localhost", username="user", password="pwd")
    with pytest.raises(ValidationError):
        config.host = "other"


def test_build_imap_config_defaults():
    config = build_imap_config(Provider.GMAIL, "user@gmail.com", "secret")
    assert config.host == "imap.gmail.com"
    assert config.port == 993
    assert config.use_ssl is True
    assert config.username == "user@gmail.com"
    assert config.password.get_secret_value() == "secret"

    config_proton = build_imap_config(Provider.PROTON, "user@proton.me", "secret")
    assert config_proton.host == "127.0.0.1"
    assert config_proton.port == 1143
    assert config_proton.use_ssl is False


def test_build_imap_config_overrides():
    config = build_imap_config(
        Provider.GMAIL, "user", "pass", host="custom.host", port=1234, use_ssl=False
    )
    assert config.host == "custom.host"
    assert config.port == 1234
    assert config.use_ssl is False


def test_app_config_initialization():
    imap_cfg = build_imap_config(Provider.CUSTOM, "u", "p")
    app_cfg = AppConfig(provider=Provider.CUSTOM, mode=Mode.FAST, imap=imap_cfg)

    assert app_cfg.provider == Provider.CUSTOM
    assert app_cfg.mode == Mode.FAST
    assert app_cfg.dry_run is True
    assert app_cfg.scan_limit is None
    assert isinstance(app_cfg.ollama, OllamaConfig)
    assert isinstance(app_cfg.rate_limit, RateLimitConfig)


@patch("mailshift.config.config.KeywordManager._load_json")
@patch("mailshift.config.config.KeywordManager._save_json")
def test_keywords_management(_save_mock, _load_mock):
    from mailshift.config.config import keyword_manager

    def side_effect(filename, default):
        if filename == "whitelist.json":
            return ["important"]
        if filename == "blacklist.json":
            return {"uncategorized": ["spam"]}
        return default

    _load_mock.side_effect = side_effect
    keyword_manager.reload()

    # adding new word
    assert add_to_whitelist("urgent") is True
    _save_mock.assert_any_call("whitelist.json", ["important", "urgent"])

    # adding existing word
    def side_effect_whitelist_has_urgent(filename, default):
        if filename == "whitelist.json":
            return ["important", "urgent"]
        if filename == "blacklist.json":
            return {"uncategorized": ["spam"]}
        return default
    _load_mock.side_effect = side_effect_whitelist_has_urgent
    keyword_manager.reload()
    assert add_to_whitelist("urgent") is False

    # removing existing word
    assert remove_from_whitelist("important") is True
    _save_mock.assert_any_call("whitelist.json", ["urgent"])

    # removing non-existent word
    assert remove_from_whitelist("missing") is False

    # Test blacklist scenarios
    _load_mock.side_effect = side_effect
    keyword_manager.reload()
    assert add_to_blacklist("offer") is True
    _save_mock.assert_any_call("blacklist.json", {"uncategorized": ["spam"], "promotion": ["offer"]})

    def side_effect_blacklist_has_offer(filename, default):
        if filename == "whitelist.json":
            return ["important"]
        if filename == "blacklist.json":
            return {"uncategorized": ["spam"], "promotion": ["offer"]}
        return default
    _load_mock.side_effect = side_effect_blacklist_has_offer
    keyword_manager.reload()

    assert remove_from_blacklist("spam") is True
    _save_mock.assert_any_call("blacklist.json", {"uncategorized": [], "promotion": ["offer"]})


@pytest.mark.parametrize(
    "keyword, expected_category",
    [
        # newsletter tests
        ("newsletter", "newsletter"),
        ("Bülten", "newsletter"),
        ("daily digest", "newsletter"),
        ("Mailchimp", "newsletter"),
        ("substack list", "newsletter"),

        # subscription tests
        ("unsubscribe", "subscription"),
        ("List-Unsubscribe", "subscription"),
        ("Abonelik", "subscription"),
        ("Listeden çık", "subscription"),
        ("preferences", "subscription"),
        ("opt out", "subscription"),

        # promotion tests
        ("Discount", "promotion"),
        ("Kampanya", "promotion"),
        ("Black Friday Sale", "promotion"),
        ("Sepet", "promotion"),
        ("özel fırsat", "promotion"),
        ("free shipping", "promotion"),

        # uncategorized / fallback tests
        ("fatura", "uncategorized"),
        ("random string", "uncategorized"),
        ("", "uncategorized"),
    ],
)
def test_infer_category(keyword, expected_category):
    from mailshift.config.config import KeywordManager
    km = KeywordManager()
    assert km._infer_category(keyword) == expected_category
