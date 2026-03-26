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


@patch("mailshift.config.config.KeywordManager._load_json")
@patch("mailshift.config.config.KeywordManager._save_json")
def test_keywords_management(_save_mock, _load_mock):
    from mailshift.config.config import keyword_manager

    def side_effect(filename, default):
        if filename == "whitelist.json":
            return _load_mock.whitelist_return
        elif filename == "blacklist.json":
            return _load_mock.blacklist_return
        return default

    _load_mock.side_effect = side_effect
    _load_mock.whitelist_return = ["important"]
    _load_mock.blacklist_return = {"uncategorized": []}

    # Reset internal state to avoid bleeding test state
    keyword_manager.whitelist = ["important"]
    keyword_manager.blacklist_dict = {"uncategorized": []}

    # adding new word
    assert keyword_manager.add_whitelist("urgent") is True
    _save_mock.assert_any_call("whitelist.json", ["important", "urgent"])

    # adding existing word
    _load_mock.whitelist_return = ["important", "urgent"]
    keyword_manager.whitelist = ["important", "urgent"]
    assert keyword_manager.add_whitelist("urgent") is False

    # removing existing word
    assert keyword_manager.remove_whitelist("important") is True
    _save_mock.assert_any_call("whitelist.json", ["urgent"])

    # removing non-existent word
    assert keyword_manager.remove_whitelist("missing") is False

    # Test blacklist scenarios
    keyword_manager.blacklist_dict = {"uncategorized": ["spam"]}
    keyword_manager.blacklist_category_map = {"spam": "uncategorized"}
    _load_mock.blacklist_return = {"uncategorized": ["spam"]}
    assert keyword_manager.add_blacklist("offer") is True
    _save_mock.assert_any_call("blacklist.json", {"uncategorized": ["spam"], "promotion": ["offer"]})

    keyword_manager.blacklist_dict = {"uncategorized": ["spam"], "promotion": ["offer"]}
    keyword_manager.blacklist_category_map = {"spam": "uncategorized", "offer": "promotion"}
    _load_mock.blacklist_return = {"uncategorized": ["spam"], "promotion": ["offer"]}
    assert keyword_manager.remove_blacklist("spam") is True
    _save_mock.assert_any_call("blacklist.json", {"uncategorized": [], "promotion": ["offer"]})


@patch("mailshift.config.config.get_path")
def test_load_json_success(mock_get_path, tmp_path):
    test_file = tmp_path / "test.json"
    test_file.write_text('{"key": "value"}', encoding="utf-8")
    mock_get_path.return_value = test_file

    km = KeywordManager()
    result = km._load_json("test.json", default={"default": True})

    assert result == {"key": "value"}


@patch("mailshift.config.config.get_path")
def test_load_json_not_exists(mock_get_path, tmp_path):
    test_file = tmp_path / "nonexistent.json"
    mock_get_path.return_value = test_file

    km = KeywordManager()
    result = km._load_json("nonexistent.json", default={"default": True})

    assert result == {"default": True}


@patch("mailshift.config.config.get_path")
def test_load_json_invalid_json(mock_get_path, tmp_path):
    test_file = tmp_path / "invalid.json"
    test_file.write_text('{"key": "value", }', encoding="utf-8") # Invalid JSON
    mock_get_path.return_value = test_file

    km = KeywordManager()
    result = km._load_json("invalid.json", default={"default": True})

    assert result == {"default": True}
