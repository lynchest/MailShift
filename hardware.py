"""
hardware.py – System hardware detection for optimal performance.

Detects CPU, RAM, and most importantly GPU/VRAM for Ollama model execution.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Optional

try:
    import pynvml
    PYNVML_AVAILABLE = True
except ImportError:
    PYNVML_AVAILABLE = False


GPU_MEMORY_REQUIREMENTS: dict[int, int] = {
    1: 2,
    2: 3,
    3: 5,
    4: 6,
    7: 8,
    8: 10,
    14: 16,
}


@dataclass
class SystemInfo:
    """System hardware information."""
    cpu_count: int
    total_ram_gb: float
    available_ram_gb: float
    has_gpu: bool
    gpu_name: str
    vram_total_gb: float
    vram_available_gb: float
    gpu_driver: str


def get_system_info() -> SystemInfo:
    """
    Get comprehensive system hardware information including GPU/VRAM.
    Falls back to CPU-only if no GPU detected or pynvml unavailable.
    """
    cpu_count, total_ram, available_ram = _get_cpu_ram()
    gpu_info = _get_gpu_info()
    
    return SystemInfo(
        cpu_count=cpu_count,
        total_ram_gb=total_ram,
        available_ram_gb=available_ram,
        has_gpu=gpu_info["has_gpu"],
        gpu_name=gpu_info["name"],
        vram_total_gb=gpu_info["total_vram_gb"],
        vram_available_gb=gpu_info["available_vram_gb"],
        gpu_driver=gpu_info["driver"],
    )


def _get_cpu_ram() -> tuple[int, float, float]:
    """Get CPU count and RAM information."""
    import psutil
    
    cpu_count = psutil.cpu_count(logical=True)
    vm = psutil.virtual_memory()
    total_ram = vm.total / (1024 ** 3)
    available_ram = vm.available / (1024 ** 3)
    
    return cpu_count, total_ram, available_ram


def _get_gpu_info() -> dict:
    """
    Detect GPU and VRAM using pynvml (NVIDIA) or nvidia-smi fallback.
    Returns dict with GPU information.
    """
    result = {
        "has_gpu": False,
        "name": "None",
        "total_vram_gb": 0.0,
        "available_vram_gb": 0.0,
        "driver": "None",
    }
    
    if PYNVML_AVAILABLE:
        try:
            pynvml.nvmlInit()
            device_count = pynvml.nvmlDeviceGetCount()
            
            if device_count > 0:
                handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                name = pynvml.nvmlDeviceGetName(handle)
                
                if isinstance(name, bytes):
                    name = name.decode("utf-8")
                
                mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                total_vram = mem_info.total / (1024 ** 3)
                available_vram = mem_info.free / (1024 ** 3)
                
                try:
                    driver = pynvml.nvmlSystemGetDriverVersion()
                except Exception:
                    driver = "Unknown"
                
                result["has_gpu"] = True
                result["name"] = name
                result["total_vram_gb"] = round(total_vram, 1)
                result["available_vram_gb"] = round(available_vram, 1)
                result["driver"] = driver
                
            pynvml.nvmlShutdown()
        except Exception:
            pass
    
    if not result["has_gpu"]:
        result = _get_gpu_info_fallback()
    
    return result


def _get_gpu_info_fallback() -> dict:
    """
    Fallback GPU detection using nvidia-smi subprocess.
    Used if pynvml is not available or fails.
    """
    result = {
        "has_gpu": False,
        "name": "None",
        "total_vram_gb": 0.0,
        "available_vram_gb": 0.0,
        "driver": "None",
    }
    
    try:
        output = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.free,driver_version", "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        
        lines = output.decode("utf-8").strip().split("\n")
        if lines:
            parts = lines[0].split(", ")
            if len(parts) >= 4:
                result["has_gpu"] = True
                result["name"] = parts[0].strip()
                result["total_vram_gb"] = round(int(parts[1].strip()) / 1024, 1)
                result["available_vram_gb"] = round(int(parts[2].strip()) / 1024, 1)
                result["driver"] = parts[3].strip()
                
    except Exception:
        pass
    
    return result


def detect_model_size(model_name: str) -> int:
    """
    Extract model size in billions from model name.
    Examples: 'qwen2.5:2b' -> 2, 'llama3:8b' -> 8, 'mistral:7b' -> 7
    """
    model_lower = model_name.lower()
    
    import re
    patterns = [
        r"(\d+)b",
        r":(\d+)b",
    ]
    
    for pattern in patterns:
        match = re.search(pattern, model_lower)
        if match:
            return int(match.group(1))
    
    return 3


def calculate_optimal_workers(
    model_name: str,
    mode: str = "pro",
    system_info: Optional[SystemInfo] = None,
) -> int:
    """
    Calculate optimal number of workers based on hardware and model.
    
    Priority:
    1. VRAM (for PRO mode with GPU) - most important for LLM inference
    2. CPU cores (for FAST mode or CPU-only inference)
    
    Args:
        model_name: Ollama model name (e.g., 'qwen2.5:2b')
        mode: 'pro' for LLM mode, 'fast' for heuristic mode
        system_info: Optional pre-fetched system info
    
    Returns:
        Optimal number of concurrent workers
    """
    if system_info is None:
        system_info = get_system_info()
    
    mode = mode.lower()
    
    if mode == "pro":
        if system_info.has_gpu and system_info.vram_available_gb > 1:
            model_size = detect_model_size(model_name)
            vram_per_worker = GPU_MEMORY_REQUIREMENTS.get(model_size, 4)
            
            max_by_vram = max(1, int(system_info.vram_available_gb / vram_per_worker))
            max_by_cpu = max(1, system_info.cpu_count - 1)
            
            optimal = min(max_by_vram, max_by_cpu, 16)
            return max(1, optimal)
        else:
            available_ram = system_info.available_ram_gb
            model_size = detect_model_size(model_name)
            ram_per_worker = model_size * 2.5
            
            max_by_ram = max(1, int(available_ram / ram_per_worker))
            max_by_cpu = max(1, system_info.cpu_count - 1)
            
            optimal = min(max_by_ram, max_by_cpu, 8)
            return max(1, optimal)
    else:
        return min(system_info.cpu_count, 16)


def format_system_info(system_info: SystemInfo, mode: str, model_name: str = "") -> str:
    """
    Format system info for display in the CLI.
    
    Args:
        system_info: System hardware information
        mode: 'pro' or 'fast'
        model_name: Model name (for PRO mode)
    
    Returns:
        Formatted string for display
    """
    if mode == "pro":
        if system_info.has_gpu:
            return (
                f"[bold]GPU:[/bold] {system_info.gpu_name} | "
                f"[bold]VRAM:[/bold] {system_info.vram_available_gb:.1f}GB / {system_info.vram_total_gb:.1f}GB | "
                f"[bold]Driver:[/bold] {system_info.gpu_driver}"
            )
        else:
            return (
                f"[bold]GPU:[/bold] Not detected (CPU mode) | "
                f"[bold]RAM:[/bold] {system_info.available_ram_gb:.1f}GB available"
            )
    else:
        return (
            f"[bold]CPU:[/bold] {system_info.cpu_count} cores | "
            f"[bold]RAM:[/bold] {system_info.available_ram_gb:.1f}GB available"
        )
