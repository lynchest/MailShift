import pytest
from unittest.mock import patch, mock_open
from pydantic import ValidationError

from config import (
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


@patch("config._load_keywords")
@patch("config._save_keywords")
def test_keywords_management(_save_mock, _load_mock):
    # Test whitelist scenarios
    _load_mock.return_value = ["important"]

    # adding new word
    assert add_to_whitelist("urgent") is True
    _save_mock.assert_called_with("whitelist.json", ["important", "urgent"])

    # adding existing word
    _load_mock.return_value = ["important", "urgent"]
    assert add_to_whitelist("urgent") is False

    # removing existing word
    assert remove_from_whitelist("important") is True
    _save_mock.assert_called_with("whitelist.json", ["urgent"])

    # removing non-existent word
    assert remove_from_whitelist("missing") is False

    # Test blacklist scenarios
    _load_mock.return_value = ["spam"]
    assert add_to_blacklist("offer") is True
    _save_mock.assert_called_with("blacklist.json", ["spam", "offer"])

    _load_mock.return_value = ["spam", "offer"]
    assert remove_from_blacklist("spam") is True
    _save_mock.assert_called_with("blacklist.json", ["offer"])
