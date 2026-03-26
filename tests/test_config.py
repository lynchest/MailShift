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


@patch.object(KeywordManager, "_load_json")
@patch.object(KeywordManager, "_save_json")
def test_keyword_manager_whitelist(_save_mock, _load_mock):
    # Setup load mock for initialization
    def mock_load(filename, default):
        if filename == "whitelist.json":
            return ["important"]
        elif filename == "blacklist.json":
            return {"uncategorized": []}
        return default
    _load_mock.side_effect = mock_load

    manager = KeywordManager()

    # adding new word
    assert manager.add_whitelist("urgent") is True
    _save_mock.assert_any_call("whitelist.json", ["important", "urgent"])

    # adding existing word
    manager.whitelist = ["important", "urgent"]
    assert manager.add_whitelist("urgent") is False

    # removing existing word
    manager.whitelist = ["important", "urgent"]
    assert manager.remove_whitelist("important") is True
    _save_mock.assert_any_call("whitelist.json", ["urgent"])

    # removing non-existent word
    manager.whitelist = ["urgent"]
    assert manager.remove_whitelist("missing") is False


@patch.object(KeywordManager, "_load_json")
@patch.object(KeywordManager, "_save_json")
def test_keyword_manager_blacklist(_save_mock, _load_mock):
    # Setup load mock for initialization
    def mock_load(filename, default):
        if filename == "whitelist.json":
            return []
        elif filename == "blacklist.json":
            return {"uncategorized": ["spam"]}
        return default
    _load_mock.side_effect = mock_load

    manager = KeywordManager()

    # adding new word to blacklist
    assert manager.add_blacklist("offer") is True
    # "offer" infer_category -> "promotion"
    _save_mock.assert_any_call("blacklist.json", {"uncategorized": ["spam"], "promotion": ["offer"]})

    # update mock_load so reload() gets the updated blacklist_dict
    def mock_load_updated(filename, default):
        if filename == "whitelist.json":
            return []
        elif filename == "blacklist.json":
            return {"uncategorized": ["spam"], "promotion": ["offer"]}
        return default
    _load_mock.side_effect = mock_load_updated

    # adding existing word
    manager.reload()
    assert manager.add_blacklist("offer") is False

    # removing existing word
    assert manager.remove_blacklist("spam") is True
    _save_mock.assert_any_call("blacklist.json", {"uncategorized": [], "promotion": ["offer"]})

    # removing non-existent word
    assert manager.remove_blacklist("missing") is False
