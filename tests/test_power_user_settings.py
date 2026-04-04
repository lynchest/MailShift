import json

from mailshift.utils.power_user_settings import (
    get_worker_probe_preference,
    load_power_user_settings,
    set_worker_probe_preference,
)


def test_load_power_user_settings_returns_defaults_when_missing(tmp_path):
    settings_path = tmp_path / "power_user_settings.json"

    settings = load_power_user_settings(cache_path=settings_path)

    assert settings["worker_probe"]["configured"] is False
    assert settings["worker_probe"]["enabled"] is False


def test_set_worker_probe_preference_persists_and_can_be_reloaded(tmp_path):
    settings_path = tmp_path / "power_user_settings.json"

    set_worker_probe_preference(True, cache_path=settings_path)
    preference = get_worker_probe_preference(cache_path=settings_path)

    assert preference is True
    raw = json.loads(settings_path.read_text(encoding="utf-8"))
    assert raw["worker_probe"]["configured"] is True
    assert raw["worker_probe"]["enabled"] is True
