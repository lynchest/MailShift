"""
hardware.py – System hardware detection for optimal performance.

Detects CPU, RAM, and GPU/VRAM (including Apple Silicon Unified Memory) 
for Ollama model execution.
"""

from __future__ import annotations

import re
import platform
import subprocess
from dataclasses import dataclass
from typing import Optional

try:
    import psutil
except ImportError:
    raise ImportError("The 'psutil' library is required. Install it with: pip install psutil")

try:
    import pynvml
    PYNVML_AVAILABLE = True
except ImportError:
    PYNVML_AVAILABLE = False


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
    Supports NVIDIA (pynvml/nvidia-smi), Apple Metal (Unified Memory), and CPU fallback.
    """
    cpu_count, total_ram, available_ram = _get_cpu_ram()
    
    # Check for Apple Silicon (Mac M1/M2/M3) Unified Memory
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        gpu_info = _get_apple_silicon_info(total_ram, available_ram)
    else:
        # Default to NVIDIA checks
        gpu_info = _get_nvidia_gpu_info()

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
    cpu_count = psutil.cpu_count(logical=True) or 1
    vm = psutil.virtual_memory()
    total_ram = vm.total / (1024 ** 3)
    available_ram = vm.available / (1024 ** 3)
    
    return cpu_count, total_ram, available_ram


def _get_apple_silicon_info(total_ram: float, available_ram: float) -> dict:
    """
    Apple Silicon uses Unified Memory. The GPU shares system RAM.
    macOS dynamically allocates it, but typically up to 70-80% can be used as VRAM.
    """
    return {
        "has_gpu": True,
        "name": "Apple Silicon (Metal)",
        "total_vram_gb": round(total_ram, 1),
        "available_vram_gb": round(available_ram, 1), # Unified memory means available RAM = available VRAM
        "driver": "Metal API",
    }


def _get_nvidia_gpu_info() -> dict:
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
                
                try:
                    driver = pynvml.nvmlSystemGetDriverVersion()
                    if isinstance(driver, bytes):
                        driver = driver.decode("utf-8")
                except Exception:
                    driver = "Unknown"
                
                result.update({
                    "has_gpu": True,
                    "name": name,
                    "total_vram_gb": round(mem_info.total / (1024 ** 3), 1),
                    "available_vram_gb": round(mem_info.free / (1024 ** 3), 1),
                    "driver": driver
                })
            pynvml.nvmlShutdown()
            return result
        except Exception:
            pass # Fallback to nvidia-smi if NVML fails

    # Fallback to subprocess
    try:
        output = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.free,driver_version", "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        
        lines = output.decode("utf-8").strip().split("\n")
        if lines:
            parts = [p.strip() for p in lines[0].split(",")]
            if len(parts) >= 4:
                result.update({
                    "has_gpu": True,
                    "name": parts[0],
                    "total_vram_gb": round(float(parts[1]) / 1024, 1),
                    "available_vram_gb": round(float(parts[2]) / 1024, 1),
                    "driver": parts[3]
                })
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError, TimeoutError):
        pass
    
    return result


def detect_model_size(model_name: str) -> float:
    """
    Extract model size in billions from model name.
    Examples: 'qwen2.5:2B' -> 2.0, 'qwen:1.5B' -> 1.5, 'llama3:8B' -> 8.0
    """
    model_lower = model_name.lower()
    
    # Matches integers or floats followed by 'b' (e.g., "7b", "1.5b", ":8b")
    match = re.search(r"(\d+(?:\.\d+)?)b", model_lower)
    if match:
        return float(match.group(1))
    
    return 3.0 # Default fallback size


def get_vram_requirement(model_size_b: float) -> float:
    """
    KV-cache / context overhead per **additional concurrent** Ollama request.

    Ollama loads the model once into VRAM (this is already reflected in
    vram_available_gb being much smaller than vram_total_gb).  Each extra
    concurrent request only needs VRAM for its own attention key-value cache.

    For short email snippets (~500 chars ≈ 200 tokens) the KV-cache is tiny:
      - ≤3B  models: ~0.15 GB/request
      - 4-7B models: ~0.30 GB/request
      - >7B  models: ~0.60 GB/request
    """
    if model_size_b <= 3:
        return 0.15
    elif model_size_b <= 7:
        return 0.30
    else:
        return 0.60


def calculate_optimal_workers(
    model_name: str,
    mode: str = "pro",
    system_info: Optional[SystemInfo] = None,
) -> int:
    """
    Calculate optimal number of concurrent Ollama workers based on hardware.

    GPU path:
      Ollama keeps one copy of the model weights in VRAM; each additional
      concurrent request only needs KV-cache overhead (see get_vram_requirement).
      We therefore divide *free* VRAM by per-worker overhead, not by full
      model size, so the result is much more generous for small models.

      The CPU cap that previously limited GPU workers has been removed – the
      network I/O + HTTP threads are cheap and the GPU is the real bottleneck.

    CPU/RAM path:
      Without a GPU the full model must fit in RAM per worker, so we keep a
      stricter per-worker RAM estimate (1.5 × model_size_b GB).
    """
    if system_info is None:
        system_info = get_system_info()

    mode = mode.lower()
    model_size = detect_model_size(model_name)

    if mode == "pro":
        if system_info.has_gpu and system_info.vram_available_gb > 0.5:
            # The model is already loaded in VRAM; vram_available_gb is just
            # the leftover KV-cache space.  No extra headroom needed.
            overhead_per_worker = get_vram_requirement(model_size)  # GB
            max_by_vram = max(1, int(system_info.vram_available_gb / overhead_per_worker))

            # For GPU + small models always allow at least 2 concurrent workers
            gpu_minimum = 4 if model_size <= 3 else 2
            result = max(gpu_minimum, max_by_vram)

            # Hard ceiling heavily lowered to 4 for Ollama
            # Ollama by default queues concurrent requests over its OLLAMA_NUM_PARALLEL limit.
            # Allowing 9+ Python threaded HTTP connections creates severe HTTP timeouts simply waiting in queue.
            # Local queuing in python (ThreadPoolExecutor) avoids timeouts!
            return min(result, 4)
        else:
            # CPU-only: full model weight must reside in RAM per worker
            available_ram = system_info.available_ram_gb
            ram_per_worker = model_size * 1.5          # more realistic than 2.5×
            max_by_ram = int(available_ram / max(1.0, ram_per_worker))
            max_by_cpu = max(1, system_info.cpu_count // 2)

            return max(1, min(max_by_ram, max_by_cpu, 8))
    else:
        return max(1, min(system_info.cpu_count, 16))


def format_system_info(system_info: SystemInfo, mode: str, model_name: str = "") -> str:
    """Format system info for display in the CLI using Rich tags."""
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