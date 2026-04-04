"""
worker_profile_store.py | Local cache for learned worker recommendations.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Optional

from .paths import get_path


PROFILE_STORE_FILE = "worker_profiles.json"
_PROFILE_VERSION = 1


@dataclass(frozen=True)
class WorkerProfileMetrics:
    sample_count: int
    timeout_rate: float
    error_rate: float
    p95_latency_s: float
    throughput: float


def get_profile_store_path() -> Path:
    return get_path(PROFILE_STORE_FILE)


def build_device_signature(
    os_name: str,
    architecture: str,
    cpu_count: int,
    total_ram_gb: float,
    gpu_name: str,
    vram_total_gb: float,
) -> str:
    normalized_gpu = " ".join((gpu_name or "none").strip().lower().split())
    payload = "|".join(
        [
            (os_name or "").strip().lower(),
            (architecture or "").strip().lower(),
            f"cpu={max(1, int(cpu_count))}",
            f"ram={round(max(0.0, float(total_ram_gb)), 1):.1f}",
            f"gpu={normalized_gpu}",
            f"vram={round(max(0.0, float(vram_total_gb)), 1):.1f}",
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _empty_store() -> dict[str, object]:
    return {"version": _PROFILE_VERSION, "profiles": {}}


def load_profile_store(cache_path: Optional[Path] = None) -> dict[str, object]:
    path = cache_path or get_profile_store_path()
    if not path.exists():
        return _empty_store()

    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return _empty_store()

    if not isinstance(raw, dict):
        return _empty_store()

    profiles = raw.get("profiles")
    if not isinstance(profiles, dict):
        profiles = {}

    version = raw.get("version")
    if not isinstance(version, int):
        version = _PROFILE_VERSION

    return {"version": version, "profiles": profiles}


def save_profile_store(store: dict[str, object], cache_path: Optional[Path] = None) -> None:
    path = cache_path or get_profile_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(store, handle, ensure_ascii=False, indent=2)


def _case_key(backend: str, model_name: str) -> str:
    backend_key = (backend or "ollama").strip().lower()
    model_key = (model_name or "").strip().lower()
    return f"{backend_key}::{model_key}"


def _clamp_workers(workers: int, upper_limit: int) -> int:
    return max(1, min(int(workers), max(1, int(upper_limit))))


def _derive_target_workers(observed_workers: int, upper_limit: int, metrics: WorkerProfileMetrics) -> int:
    target = _clamp_workers(observed_workers, upper_limit)
    if metrics.sample_count < 4:
        return target

    if metrics.timeout_rate >= 0.10 or metrics.error_rate >= 0.10:
        return max(1, target - 1)

    if metrics.timeout_rate == 0.0 and metrics.error_rate <= 0.02 and metrics.p95_latency_s <= 4.5:
        return min(max(1, upper_limit), target + 1)

    return target


def get_recommended_worker(
    device_signature: str,
    backend: str,
    model_name: str,
    upper_limit: int,
    cache_path: Optional[Path] = None,
) -> Optional[int]:
    if not device_signature:
        return None

    store = load_profile_store(cache_path)
    profiles = store.get("profiles", {})
    if not isinstance(profiles, dict):
        return None

    profile_entry = profiles.get(device_signature)
    if not isinstance(profile_entry, dict):
        return None

    cases = profile_entry.get("cases", {})
    if not isinstance(cases, dict):
        return None

    case_entry = cases.get(_case_key(backend, model_name))
    if not isinstance(case_entry, dict):
        return None

    recommended = case_entry.get("recommended_workers")
    if not isinstance(recommended, int) or recommended <= 0:
        return None

    return _clamp_workers(recommended, upper_limit)


def record_worker_profile_run(
    device_signature: str,
    backend: str,
    model_name: str,
    observed_workers: int,
    upper_limit: int,
    metrics: WorkerProfileMetrics,
    device_context: Optional[dict[str, object]] = None,
    cache_path: Optional[Path] = None,
) -> Optional[int]:
    if not device_signature or metrics.sample_count <= 0:
        return None

    safe_upper = max(1, int(upper_limit))
    observed = _clamp_workers(observed_workers, safe_upper)
    target = _derive_target_workers(observed, safe_upper, metrics)

    previous = get_recommended_worker(
        device_signature=device_signature,
        backend=backend,
        model_name=model_name,
        upper_limit=safe_upper,
        cache_path=cache_path,
    )
    if previous is None:
        recommended = target
    else:
        blended = int(round(previous * 0.70 + target * 0.30))
        recommended = _clamp_workers(blended, safe_upper)

    store = load_profile_store(cache_path)
    profiles = store.setdefault("profiles", {})
    if not isinstance(profiles, dict):
        profiles = {}
        store["profiles"] = profiles

    profile_entry = profiles.setdefault(device_signature, {})
    if not isinstance(profile_entry, dict):
        profile_entry = {}
        profiles[device_signature] = profile_entry

    cases = profile_entry.setdefault("cases", {})
    if not isinstance(cases, dict):
        cases = {}
        profile_entry["cases"] = cases

    case_key = _case_key(backend, model_name)
    previous_case = cases.get(case_key)
    previous_run_count = previous_case.get("run_count", 0) if isinstance(previous_case, dict) else 0
    if not isinstance(previous_run_count, int):
        previous_run_count = 0

    now_iso = datetime.now(timezone.utc).isoformat()
    profile_entry["last_updated"] = now_iso
    if device_context:
        profile_entry["device"] = dict(device_context)

    cases[case_key] = {
        "backend": (backend or "").strip().lower(),
        "model": model_name,
        "recommended_workers": recommended,
        "last_observed_workers": observed,
        "upper_limit": safe_upper,
        "run_count": previous_run_count + 1,
        "last_metrics": asdict(metrics),
        "updated_at": now_iso,
    }

    save_profile_store(store, cache_path=cache_path)
    return recommended


def set_recommended_worker(
    device_signature: str,
    backend: str,
    model_name: str,
    recommended_workers: int,
    upper_limit: int,
    source: str = "manual",
    device_context: Optional[dict[str, object]] = None,
    cache_path: Optional[Path] = None,
) -> Optional[int]:
    if not device_signature:
        return None

    safe_upper = max(1, int(upper_limit))
    recommended = _clamp_workers(recommended_workers, safe_upper)

    store = load_profile_store(cache_path)
    profiles = store.setdefault("profiles", {})
    if not isinstance(profiles, dict):
        profiles = {}
        store["profiles"] = profiles

    profile_entry = profiles.setdefault(device_signature, {})
    if not isinstance(profile_entry, dict):
        profile_entry = {}
        profiles[device_signature] = profile_entry

    cases = profile_entry.setdefault("cases", {})
    if not isinstance(cases, dict):
        cases = {}
        profile_entry["cases"] = cases

    case_key = _case_key(backend, model_name)
    previous_case = cases.get(case_key)
    previous_run_count = previous_case.get("run_count", 0) if isinstance(previous_case, dict) else 0
    if not isinstance(previous_run_count, int):
        previous_run_count = 0

    now_iso = datetime.now(timezone.utc).isoformat()
    profile_entry["last_updated"] = now_iso
    if device_context:
        profile_entry["device"] = dict(device_context)

    previous_last_metrics = {}
    if isinstance(previous_case, dict):
        prev_metrics = previous_case.get("last_metrics")
        if isinstance(prev_metrics, dict):
            previous_last_metrics = prev_metrics

    cases[case_key] = {
        "backend": (backend or "").strip().lower(),
        "model": model_name,
        "recommended_workers": recommended,
        "last_observed_workers": recommended,
        "upper_limit": safe_upper,
        "run_count": previous_run_count + 1,
        "last_metrics": previous_last_metrics,
        "source": source,
        "updated_at": now_iso,
    }

    save_profile_store(store, cache_path=cache_path)
    return recommended
