from tests import benchmark_matrix_helpers as helpers
from mailshift.utils.hardware import calculate_optimal_workers


def test_parse_positive_int_csv_filters_invalid_tokens():
    parsed = helpers.parse_positive_int_csv("1,2,foo,-1,0,2,8")
    assert parsed == [1, 2, 8]


def test_resolve_profiles_supports_three_builtin_profiles():
    profiles = helpers.resolve_profiles(["low", "medium", "high"])
    assert [profile.name for profile in profiles] == ["low", "medium", "high"]


def test_default_worker_candidates_include_auto_worker_for_profile():
    profile = helpers.resolve_profiles(["medium"])[0]
    model_name = "qwen3.5:0.8B"
    workers = helpers.default_worker_candidates(model_name, "lm_studio", profile)

    auto_workers = calculate_optimal_workers(
        model_name,
        mode="pro",
        backend="lm_studio",
        system_info=profile.system_info,
    )

    assert workers == sorted(set(workers))
    assert auto_workers in workers
    assert all(value > 0 for value in workers)


def test_pick_best_run_prioritizes_accuracy_then_f1_then_throughput():
    runs = [
        {
            "workers": 2,
            "pro_accuracy": 0.90,
            "f1": 0.80,
            "throughput": 10.0,
            "timeout_rate": 0.0,
            "error_rate": 0.0,
        },
        {
            "workers": 4,
            "pro_accuracy": 0.92,
            "f1": 0.70,
            "throughput": 20.0,
            "timeout_rate": 0.0,
            "error_rate": 0.0,
        },
        {
            "workers": 6,
            "pro_accuracy": 0.92,
            "f1": 0.90,
            "throughput": 15.0,
            "timeout_rate": 0.0,
            "error_rate": 0.0,
        },
    ]

    best = helpers.pick_best_run(runs)
    assert best["workers"] == 6


def test_render_comparison_report_contains_summary_lines():
    matrix_result = {
        "created_at": "2026-04-04T00:00:00+00:00",
        "dataset_size": 10,
        "host_system": {
            "cpu_count": 8,
            "total_ram_gb": 16.0,
            "available_ram_gb": 12.0,
            "gpu_name": "None",
            "vram_total_gb": 0.0,
            "vram_available_gb": 0.0,
        },
        "cases": [
            {
                "profile": "low",
                "profile_description": "test",
                "backend": "lm_studio",
                "model": "gemma-4-26b-a4b",
                "health_ok": True,
                "health_msg": "ok",
                "stopped_early": False,
                "runs": [
                    {
                        "workers": 2,
                        "pro_accuracy": 1.0,
                        "f1": 1.0,
                        "elapsed_s": 1.0,
                        "throughput": 10.0,
                        "avg_ai_latency": 0.1,
                        "p95_ai_latency": 0.2,
                        "timeout_rate": 0.0,
                        "error_rate": 0.0,
                    }
                ],
                "best": {
                    "workers": 2,
                    "pro_accuracy": 1.0,
                    "f1": 1.0,
                    "throughput": 10.0,
                },
            }
        ],
    }

    report = helpers.render_comparison_report(matrix_result)
    assert "WORKER BENCHMARK MATRIX REPORT" in report
    assert "profile=low" in report
    assert "BEST=2" in report
