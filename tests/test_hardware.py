from unittest.mock import patch

from mailshift.utils.hardware import calculate_optimal_workers, get_system_info, resolve_worker_plan


class _VM:
    def __init__(self, total: int, available: int):
        self.total = total
        self.available = available


@patch("mailshift.utils.hardware.psutil.cpu_count", return_value=8)
@patch("mailshift.utils.hardware.psutil.virtual_memory")
@patch("mailshift.utils.hardware.platform.machine", return_value="AMD64")
@patch("mailshift.utils.hardware.platform.system", return_value="Windows")
@patch("mailshift.utils.hardware.subprocess.check_output")
def test_get_system_info_detects_intel_gpu(
    mock_check_output,
    _mock_system,
    _mock_machine,
    mock_virtual_memory,
    _mock_cpu_count,
):
    vm = _VM(total=16 * 1024**3, available=8 * 1024**3)
    mock_virtual_memory.return_value = vm

    mock_check_output.side_effect = [
        Exception("nvidia-smi missing"),
        b'{"Name":"Intel(R) Arc(TM) Graphics","AdapterRAM":2147483648,"DriverVersion":"32.0.101.6078"}',
    ]

    info = get_system_info()

    assert info.has_gpu is True
    assert "Intel" in info.gpu_name
    assert info.vram_total_gb >= 2.0
    assert info.vram_available_gb > 0
    assert info.gpu_driver == "32.0.101.6078"


@patch("mailshift.utils.hardware.psutil.cpu_count", return_value=8)
@patch("mailshift.utils.hardware.psutil.virtual_memory")
@patch("mailshift.utils.hardware.platform.machine", return_value="AMD64")
@patch("mailshift.utils.hardware.platform.system", return_value="Windows")
@patch("mailshift.utils.hardware.subprocess.check_output")
def test_get_system_info_intel_uses_shared_ram_when_vram_missing(
    mock_check_output,
    _mock_system,
    _mock_machine,
    mock_virtual_memory,
    _mock_cpu_count,
):
    vm = _VM(total=32 * 1024**3, available=12 * 1024**3)
    mock_virtual_memory.return_value = vm

    mock_check_output.side_effect = [
        Exception("nvidia-smi missing"),
        b'{"Name":"Intel(R) UHD Graphics","AdapterRAM":0,"DriverVersion":"31.0.101.2114"}',
    ]

    info = get_system_info()

    assert info.has_gpu is True
    assert "Intel" in info.gpu_name
    assert info.vram_total_gb > 0.5
    assert info.vram_available_gb > 0.5


@patch("mailshift.utils.hardware.get_system_info")
def test_calculate_optimal_workers_uses_gpu_branch_for_intel(mock_get_system_info):
    info = type(
        "SysInfo",
        (),
        {
            "cpu_count": 12,
            "total_ram_gb": 32.0,
            "available_ram_gb": 12.0,
            "has_gpu": True,
            "gpu_name": "Intel(R) Arc(TM) Graphics",
            "vram_total_gb": 8.0,
            "vram_available_gb": 4.0,
            "gpu_driver": "x",
        },
    )
    mock_get_system_info.return_value = info

    workers = calculate_optimal_workers("qwen3.5:2B", mode="pro")

    assert workers >= 2


@patch("mailshift.utils.hardware.psutil.cpu_count", return_value=8)
@patch("mailshift.utils.hardware.psutil.virtual_memory")
@patch("mailshift.utils.hardware.platform.machine", return_value="AMD64")
@patch("mailshift.utils.hardware.platform.system", return_value="Windows")
@patch("mailshift.utils.hardware.subprocess.check_output")
def test_get_system_info_detects_amd_gpu(
    mock_check_output,
    _mock_system,
    _mock_machine,
    mock_virtual_memory,
    _mock_cpu_count,
):
    vm = _VM(total=16 * 1024**3, available=8 * 1024**3)
    mock_virtual_memory.return_value = vm

    mock_check_output.side_effect = [
        Exception("nvidia-smi missing"),
        b'{"Name":"AMD Radeon RX 6600","AdapterRAM":8589934592,"DriverVersion":"31.0.22000.1000"}',
        b'{"Name":"AMD Radeon RX 6600","AdapterRAM":8589934592,"DriverVersion":"31.0.22000.1000"}',
    ]

    info = get_system_info()

    assert info.has_gpu is True
    assert "Radeon" in info.gpu_name
    assert info.vram_total_gb >= 8.0
    assert info.vram_available_gb > 0
    assert info.gpu_driver == "31.0.22000.1000"


@patch("mailshift.utils.hardware.psutil.cpu_count", return_value=8)
@patch("mailshift.utils.hardware.psutil.virtual_memory")
@patch("mailshift.utils.hardware.platform.machine", return_value="AMD64")
@patch("mailshift.utils.hardware.platform.system", return_value="Windows")
@patch("mailshift.utils.hardware.subprocess.check_output")
def test_get_system_info_amd_uses_shared_ram_when_vram_missing(
    mock_check_output,
    _mock_system,
    _mock_machine,
    mock_virtual_memory,
    _mock_cpu_count,
):
    vm = _VM(total=24 * 1024**3, available=10 * 1024**3)
    mock_virtual_memory.return_value = vm

    mock_check_output.side_effect = [
        Exception("nvidia-smi missing"),
        b'{"Name":"AMD Radeon Graphics","AdapterRAM":0,"DriverVersion":"31.0.14000.5000"}',
        b'{"Name":"AMD Radeon Graphics","AdapterRAM":0,"DriverVersion":"31.0.14000.5000"}',
    ]

    info = get_system_info()

    assert info.has_gpu is True
    assert "AMD" in info.gpu_name
    assert info.vram_total_gb > 0.5
    assert info.vram_available_gb > 0.5


@patch("mailshift.utils.hardware.get_system_info")
def test_calculate_optimal_workers_uses_gpu_branch_for_amd(mock_get_system_info):
    info = type(
        "SysInfo",
        (),
        {
            "cpu_count": 12,
            "total_ram_gb": 32.0,
            "available_ram_gb": 12.0,
            "has_gpu": True,
            "gpu_name": "AMD Radeon RX 6600",
            "vram_total_gb": 8.0,
            "vram_available_gb": 4.0,
            "gpu_driver": "x",
        },
    )
    mock_get_system_info.return_value = info

    workers = calculate_optimal_workers("qwen3.5:2B", mode="pro")

    assert workers >= 2


@patch("mailshift.utils.hardware.psutil.cpu_count", return_value=8)
@patch("mailshift.utils.hardware.psutil.virtual_memory")
@patch("mailshift.utils.hardware.platform.machine", return_value="x86_64")
@patch("mailshift.utils.hardware.platform.system", return_value="Darwin")
@patch("mailshift.utils.hardware.subprocess.check_output")
def test_get_system_info_detects_intel_mac_gpu(
    mock_check_output,
    _mock_system,
    _mock_machine,
    mock_virtual_memory,
    _mock_cpu_count,
):
    vm = _VM(total=16 * 1024**3, available=8 * 1024**3)
    mock_virtual_memory.return_value = vm
    mock_check_output.return_value = b"Chipset Model: Intel Iris Plus Graphics"

    info = get_system_info()

    assert info.has_gpu is True
    assert "Intel" in info.gpu_name
    assert info.gpu_driver == "Metal API"
    assert info.vram_total_gb > 0
    assert info.vram_available_gb > 0


@patch("mailshift.utils.hardware.psutil.cpu_count", return_value=8)
@patch("mailshift.utils.hardware.psutil.virtual_memory")
@patch("mailshift.utils.hardware.platform.machine", return_value="arm64")
@patch("mailshift.utils.hardware.platform.system", return_value="Darwin")
def test_get_system_info_detects_apple_silicon(
    _mock_system,
    _mock_machine,
    mock_virtual_memory,
    _mock_cpu_count,
):
    vm = _VM(total=16 * 1024**3, available=12 * 1024**3)
    mock_virtual_memory.return_value = vm

    info = get_system_info()

    assert info.has_gpu is True
    assert info.gpu_name == "Apple Silicon (Metal)"
    assert info.gpu_driver == "Metal API"


@patch("mailshift.utils.hardware.get_system_info")
def test_calculate_optimal_workers_manual_override(mock_get_system_info):
    info = type(
        "SysInfo",
        (),
        {
            "cpu_count": 12,
            "total_ram_gb": 32.0,
            "available_ram_gb": 12.0,
            "has_gpu": True,
            "gpu_name": "NVIDIA RTX 3060",
            "vram_total_gb": 12.0,
            "vram_available_gb": 10.0,
            "gpu_driver": "x",
        },
    )
    mock_get_system_info.return_value = info

    # Test manual override within limits
    workers = calculate_optimal_workers("qwen3.5:2B", mode="pro", manual_workers=4)
    assert workers == 4

    # Test manual override exceeding VRAM limits (should be capped)
    info.vram_available_gb = 0.7 # safe_vram = 0.2
    # 2B model requirement ~0.15. 0.2 / 0.15 = 1.3 -> 1 worker
    workers = calculate_optimal_workers("qwen3.5:2B", mode="pro", manual_workers=10)
    assert workers == 1


@patch("mailshift.utils.hardware.get_system_info")
def test_calculate_optimal_workers_clamps_manual_override_in_cpu_mode(mock_get_system_info):
    info = type(
        "SysInfo",
        (),
        {
            "cpu_count": 8,
            "total_ram_gb": 32.0,
            "available_ram_gb": 12.0,
            "has_gpu": False,
            "gpu_name": "None",
            "vram_total_gb": 0.0,
            "vram_available_gb": 0.0,
            "gpu_driver": "None",
        },
    )
    mock_get_system_info.return_value = info

    workers = calculate_optimal_workers("qwen3.5:4B", mode="pro", manual_workers=20)
    assert workers == 2


@patch("mailshift.utils.hardware.get_system_info")
def test_resolve_worker_plan_clamps_to_lm_studio_backend_limit(mock_get_system_info):
    info = type(
        "SysInfo",
        (),
        {
            "cpu_count": 12,
            "total_ram_gb": 32.0,
            "available_ram_gb": 16.0,
            "has_gpu": True,
            "gpu_name": "NVIDIA RTX 3060",
            "vram_total_gb": 12.0,
            "vram_available_gb": 8.0,
            "gpu_driver": "x",
        },
    )
    mock_get_system_info.return_value = info

    plan = resolve_worker_plan("qwen3.5:2B", mode="pro", manual_workers=12, backend="lm_studio")

    assert plan.workers == 10
    assert plan.was_clamped is True
    assert "backend-cap=10" in plan.reason


@patch("mailshift.utils.hardware.get_system_info")
def test_resolve_worker_plan_applies_ollama_env_cap_as_upper_limit(mock_get_system_info):
    info = type(
        "SysInfo",
        (),
        {
            "cpu_count": 12,
            "total_ram_gb": 32.0,
            "available_ram_gb": 16.0,
            "has_gpu": True,
            "gpu_name": "NVIDIA RTX 3060",
            "vram_total_gb": 12.0,
            "vram_available_gb": 8.0,
            "gpu_driver": "x",
        },
    )
    mock_get_system_info.return_value = info

    with patch.dict("mailshift.utils.hardware.os.environ", {"OLLAMA_NUM_PARALLEL": "3"}):
        plan = resolve_worker_plan("qwen3.5:2B", mode="pro", manual_workers=8, backend="ollama")

    assert plan.workers == 3
    assert plan.was_clamped is True
    assert "env-cap=3" in plan.reason


@patch("mailshift.utils.hardware.get_recommended_worker", return_value=3)
@patch("mailshift.utils.hardware.get_system_info")
def test_resolve_worker_plan_uses_profile_warm_start_for_auto(
    mock_get_system_info,
    _mock_get_recommended_worker,
):
    info = type(
        "SysInfo",
        (),
        {
            "cpu_count": 12,
            "total_ram_gb": 32.0,
            "available_ram_gb": 16.0,
            "has_gpu": True,
            "gpu_name": "NVIDIA RTX 3060",
            "vram_total_gb": 12.0,
            "vram_available_gb": 8.0,
            "gpu_driver": "x",
        },
    )
    mock_get_system_info.return_value = info

    plan = resolve_worker_plan("qwen3.5:2B", mode="pro", manual_workers=None, backend="ollama")

    assert plan.workers == 3
    assert plan.source == "auto-profile"
    assert "profile-warm-start=3" in plan.reason


@patch("mailshift.utils.hardware.get_recommended_worker", return_value=3)
@patch("mailshift.utils.hardware.get_system_info")
def test_resolve_worker_plan_manual_override_takes_priority_over_profile(
    mock_get_system_info,
    _mock_get_recommended_worker,
):
    info = type(
        "SysInfo",
        (),
        {
            "cpu_count": 12,
            "total_ram_gb": 32.0,
            "available_ram_gb": 16.0,
            "has_gpu": True,
            "gpu_name": "NVIDIA RTX 3060",
            "vram_total_gb": 12.0,
            "vram_available_gb": 8.0,
            "gpu_driver": "x",
        },
    )
    mock_get_system_info.return_value = info

    plan = resolve_worker_plan("qwen3.5:2B", mode="pro", manual_workers=5, backend="ollama")

    assert plan.workers == 5
    assert plan.source == "manual"


@patch("mailshift.utils.hardware.set_recommended_worker", return_value=4)
@patch("mailshift.utils.hardware.run_worker_hardware_probe", return_value=4)
@patch("mailshift.utils.hardware.get_recommended_worker", return_value=None)
@patch("mailshift.utils.hardware.get_system_info")
def test_resolve_worker_plan_runs_power_probe_when_enabled_and_no_warm_start(
    mock_get_system_info,
    mock_get_recommended_worker,
    mock_run_probe,
    mock_set_recommended,
):
    info = type(
        "SysInfo",
        (),
        {
            "cpu_count": 12,
            "total_ram_gb": 32.0,
            "available_ram_gb": 16.0,
            "has_gpu": True,
            "gpu_name": "NVIDIA RTX 3060",
            "vram_total_gb": 12.0,
            "vram_available_gb": 8.0,
            "gpu_driver": "x",
        },
    )
    mock_get_system_info.return_value = info

    plan = resolve_worker_plan(
        "qwen3.5:2B",
        mode="pro",
        manual_workers=None,
        backend="ollama",
        power_worker_probe=True,
    )

    assert plan.workers == 4
    assert plan.source == "auto-probe"
    assert "power-probe=4" in plan.reason
    mock_get_recommended_worker.assert_called_once()
    mock_run_probe.assert_called_once()
    mock_set_recommended.assert_called_once()


@patch("mailshift.utils.hardware.set_recommended_worker")
@patch("mailshift.utils.hardware.run_worker_hardware_probe")
@patch("mailshift.utils.hardware.get_recommended_worker", return_value=3)
@patch("mailshift.utils.hardware.get_system_info")
def test_resolve_worker_plan_skips_probe_when_profile_warm_start_exists(
    mock_get_system_info,
    _mock_get_recommended_worker,
    mock_run_probe,
    mock_set_recommended,
):
    info = type(
        "SysInfo",
        (),
        {
            "cpu_count": 12,
            "total_ram_gb": 32.0,
            "available_ram_gb": 16.0,
            "has_gpu": True,
            "gpu_name": "NVIDIA RTX 3060",
            "vram_total_gb": 12.0,
            "vram_available_gb": 8.0,
            "gpu_driver": "x",
        },
    )
    mock_get_system_info.return_value = info

    plan = resolve_worker_plan(
        "qwen3.5:2B",
        mode="pro",
        manual_workers=None,
        backend="ollama",
        power_worker_probe=True,
    )

    assert plan.workers == 3
    assert plan.source == "auto-profile"
    mock_run_probe.assert_not_called()
    mock_set_recommended.assert_not_called()
