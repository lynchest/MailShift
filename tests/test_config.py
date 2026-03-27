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
    KeywordManager,
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


@pytest.fixture
def empty_keyword_manager():
    """Returns a fresh KeywordManager without disk I/O."""
    with patch.object(KeywordManager, "_load_json", return_value=[]), \
         patch.object(KeywordManager, "_save_json"):
        km = KeywordManager()
        km.whitelist = []
        km.blacklist_dict = {}
        km.junk_keywords_flat = []
        km.blacklist_category_map = {}
        yield km


def test_keyword_manager_add_blacklist(empty_keyword_manager):
    km = empty_keyword_manager

    with patch.object(km, "_save_json") as mock_save, \
         patch.object(km, "reload") as mock_reload:

        # Test 1: Adding a new word successfully
        assert km.add_blacklist("Discount") is True

        # Target category is inferred (discount -> promotion)
        expected_dict = {"promotion": ["Discount"]}
        mock_save.assert_called_with("blacklist.json", expected_dict)
        mock_reload.assert_called_once()

        # Manually trigger what reload would do to setup state for the next test
        km.blacklist_dict = expected_dict
        km.blacklist_category_map = {"discount": "promotion"}
        km.junk_keywords_flat = ["discount"]

        mock_save.reset_mock()
        mock_reload.reset_mock()

        # Test 2: Adding an existing word (case insensitive)
        assert km.add_blacklist("discount") is False
        assert km.add_blacklist("DISCOUNT") is False
        mock_save.assert_not_called()
        mock_reload.assert_not_called()

        # Test 3: Adding a word to a different category
        assert km.add_blacklist("Newsletter") is True
        expected_dict = {"promotion": ["Discount"], "newsletter": ["Newsletter"]}
        mock_save.assert_called_with("blacklist.json", expected_dict)
        mock_reload.assert_called_once()

        # Test 4: Default fallback category (uncategorized)
        assert km.add_blacklist("randomword") is True
        expected_dict = {
            "promotion": ["Discount"],
            "newsletter": ["Newsletter"],
            "uncategorized": ["randomword"]
        }
        mock_save.assert_called_with("blacklist.json", expected_dict)


def test_keyword_manager_whitelist(empty_keyword_manager):
    km = empty_keyword_manager

    with patch.object(km, "_save_json") as mock_save, \
         patch.object(km, "reload") as mock_reload:

        # Adding new word
        assert km.add_whitelist("urgent") is True
        mock_save.assert_called_with("whitelist.json", ["urgent"])
        mock_reload.assert_called_once()

        # Manually update state
        km.whitelist = ["urgent"]
        mock_save.reset_mock()
        mock_reload.reset_mock()

        # Adding existing word
        assert km.add_whitelist("urgent") is False
        mock_save.assert_not_called()
        mock_reload.assert_not_called()

        # Removing existing word
        assert km.remove_whitelist("urgent") is True
        mock_save.assert_called_with("whitelist.json", [])
        mock_reload.assert_called_once()

        # Manually update state
        km.whitelist = []
        mock_save.reset_mock()
        mock_reload.reset_mock()

        # Removing non-existent word
        assert km.remove_whitelist("missing") is False
        mock_save.assert_not_called()
        mock_reload.assert_not_called()


