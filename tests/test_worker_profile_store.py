import json

from mailshift.utils.worker_profile_store import (
    WorkerProfileMetrics,
    build_device_signature,
    get_recommended_worker,
    record_worker_profile_run,
    set_recommended_worker,
)


def test_build_device_signature_is_stable_for_same_input():
    first = build_device_signature(
        os_name="Darwin",
        architecture="arm64",
        cpu_count=10,
        total_ram_gb=16.0,
        gpu_name="Apple Silicon (Metal)",
        vram_total_gb=16.0,
    )
    second = build_device_signature(
        os_name="Darwin",
        architecture="arm64",
        cpu_count=10,
        total_ram_gb=16.0,
        gpu_name="Apple Silicon (Metal)",
        vram_total_gb=16.0,
    )

    assert first == second
    assert len(first) == 24


def test_record_and_lookup_profile_recommendation_roundtrip(tmp_path):
    cache_path = tmp_path / "worker_profiles.json"
    metrics = WorkerProfileMetrics(
        sample_count=24,
        timeout_rate=0.0,
        error_rate=0.0,
        p95_latency_s=2.0,
        throughput=3.5,
    )

    recommended = record_worker_profile_run(
        device_signature="sig-1",
        backend="ollama",
        model_name="qwen3.5:2B",
        observed_workers=4,
        upper_limit=8,
        metrics=metrics,
        cache_path=cache_path,
    )
    looked_up = get_recommended_worker(
        device_signature="sig-1",
        backend="ollama",
        model_name="qwen3.5:2B",
        upper_limit=8,
        cache_path=cache_path,
    )

    assert cache_path.exists()
    assert looked_up == recommended
    assert looked_up is not None


def test_lookup_clamps_cached_recommendation_to_upper_limit(tmp_path):
    cache_path = tmp_path / "worker_profiles.json"
    raw = {
        "version": 1,
        "profiles": {
            "sig-2": {
                "cases": {
                    "ollama::qwen3.5:2b": {
                        "recommended_workers": 11,
                    }
                }
            }
        },
    }
    cache_path.write_text(json.dumps(raw), encoding="utf-8")

    looked_up = get_recommended_worker(
        device_signature="sig-2",
        backend="ollama",
        model_name="qwen3.5:2B",
        upper_limit=3,
        cache_path=cache_path,
    )

    assert looked_up == 3


def test_record_unstable_metrics_biases_recommendation_down(tmp_path):
    cache_path = tmp_path / "worker_profiles.json"
    stable_metrics = WorkerProfileMetrics(
        sample_count=30,
        timeout_rate=0.0,
        error_rate=0.0,
        p95_latency_s=1.8,
        throughput=4.0,
    )
    unstable_metrics = WorkerProfileMetrics(
        sample_count=30,
        timeout_rate=0.25,
        error_rate=0.0,
        p95_latency_s=15.0,
        throughput=1.0,
    )

    first = record_worker_profile_run(
        device_signature="sig-3",
        backend="lm_studio",
        model_name="gemma-4-26b-a4b",
        observed_workers=6,
        upper_limit=8,
        metrics=stable_metrics,
        cache_path=cache_path,
    )
    second = record_worker_profile_run(
        device_signature="sig-3",
        backend="lm_studio",
        model_name="gemma-4-26b-a4b",
        observed_workers=6,
        upper_limit=8,
        metrics=unstable_metrics,
        cache_path=cache_path,
    )

    assert first is not None
    assert second is not None
    assert second < first


def test_set_recommended_worker_persists_direct_value(tmp_path):
    cache_path = tmp_path / "worker_profiles.json"

    saved = set_recommended_worker(
        device_signature="sig-4",
        backend="ollama",
        model_name="qwen3.5:2B",
        recommended_workers=5,
        upper_limit=8,
        source="power-worker-probe",
        cache_path=cache_path,
    )
    looked_up = get_recommended_worker(
        device_signature="sig-4",
        backend="ollama",
        model_name="qwen3.5:2B",
        upper_limit=8,
        cache_path=cache_path,
    )

    assert saved == 5
    assert looked_up == 5
