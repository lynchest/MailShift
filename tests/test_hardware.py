from unittest.mock import patch

from hardware import calculate_optimal_workers, get_system_info


@patch("hardware.psutil.cpu_count", return_value=8)
@patch("hardware.psutil.virtual_memory")
@patch("hardware.platform.machine", return_value="AMD64")
@patch("hardware.platform.system", return_value="Windows")
@patch("hardware.subprocess.check_output")
def test_get_system_info_detects_intel_gpu(
    mock_check_output,
    _mock_system,
    _mock_machine,
    mock_virtual_memory,
    _mock_cpu_count,
):
    vm = type("VM", (), {"total": 16 * 1024**3, "available": 8 * 1024**3})
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


@patch("hardware.psutil.cpu_count", return_value=8)
@patch("hardware.psutil.virtual_memory")
@patch("hardware.platform.machine", return_value="AMD64")
@patch("hardware.platform.system", return_value="Windows")
@patch("hardware.subprocess.check_output")
def test_get_system_info_intel_uses_shared_ram_when_vram_missing(
    mock_check_output,
    _mock_system,
    _mock_machine,
    mock_virtual_memory,
    _mock_cpu_count,
):
    vm = type("VM", (), {"total": 32 * 1024**3, "available": 12 * 1024**3})
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


@patch("hardware.get_system_info")
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


@patch("hardware.psutil.cpu_count", return_value=8)
@patch("hardware.psutil.virtual_memory")
@patch("hardware.platform.machine", return_value="AMD64")
@patch("hardware.platform.system", return_value="Windows")
@patch("hardware.subprocess.check_output")
def test_get_system_info_detects_amd_gpu(
    mock_check_output,
    _mock_system,
    _mock_machine,
    mock_virtual_memory,
    _mock_cpu_count,
):
    vm = type("VM", (), {"total": 16 * 1024**3, "available": 8 * 1024**3})
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


@patch("hardware.psutil.cpu_count", return_value=8)
@patch("hardware.psutil.virtual_memory")
@patch("hardware.platform.machine", return_value="AMD64")
@patch("hardware.platform.system", return_value="Windows")
@patch("hardware.subprocess.check_output")
def test_get_system_info_amd_uses_shared_ram_when_vram_missing(
    mock_check_output,
    _mock_system,
    _mock_machine,
    mock_virtual_memory,
    _mock_cpu_count,
):
    vm = type("VM", (), {"total": 24 * 1024**3, "available": 10 * 1024**3})
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


@patch("hardware.get_system_info")
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
