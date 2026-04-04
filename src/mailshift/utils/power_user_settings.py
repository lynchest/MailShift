"""
power_user_settings.py | Persistent power-user preferences.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .paths import get_path


SETTINGS_FILE = "power_user_settings.json"
_SETTINGS_VERSION = 1


def get_power_user_settings_path() -> Path:
    return get_path(SETTINGS_FILE)


def _default_settings() -> dict[str, object]:
    return {
        "version": _SETTINGS_VERSION,
        "worker_probe": {
            "configured": False,
            "enabled": False,
        },
    }


def load_power_user_settings(cache_path: Optional[Path] = None) -> dict[str, object]:
    path = cache_path or get_power_user_settings_path()
    if not path.exists():
        return _default_settings()

    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return _default_settings()

    if not isinstance(raw, dict):
        return _default_settings()

    merged = _default_settings()
    worker_probe_raw = raw.get("worker_probe")
    if isinstance(worker_probe_raw, dict):
        merged_worker_probe = merged["worker_probe"]
        if isinstance(merged_worker_probe, dict):
            merged_worker_probe["configured"] = bool(worker_probe_raw.get("configured", False))
            merged_worker_probe["enabled"] = bool(worker_probe_raw.get("enabled", False))

    return merged


def save_power_user_settings(settings: dict[str, object], cache_path: Optional[Path] = None) -> None:
    path = cache_path or get_power_user_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(settings, handle, ensure_ascii=False, indent=2)


def get_worker_probe_preference(cache_path: Optional[Path] = None) -> Optional[bool]:
    settings = load_power_user_settings(cache_path=cache_path)
    worker_probe = settings.get("worker_probe")
    if not isinstance(worker_probe, dict):
        return None

    if not bool(worker_probe.get("configured", False)):
        return None
    return bool(worker_probe.get("enabled", False))


def set_worker_probe_preference(enabled: bool, cache_path: Optional[Path] = None) -> bool:
    settings = load_power_user_settings(cache_path=cache_path)
    worker_probe = settings.get("worker_probe")
    if not isinstance(worker_probe, dict):
        worker_probe = {}
        settings["worker_probe"] = worker_probe

    worker_probe["configured"] = True
    worker_probe["enabled"] = bool(enabled)
    save_power_user_settings(settings, cache_path=cache_path)
    return bool(enabled)
