"""
hardware.py – System hardware detection for optimal performance.
"""

from __future__ import annotations
import re
import platform
import subprocess
import os
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
    cpu_count: int
    total_ram_gb: float
    available_ram_gb: float
    has_gpu: bool
    gpu_name: str
    vram_total_gb: float
    vram_available_gb: float
    gpu_driver: str


def get_system_info() -> SystemInfo:
    cpu_count, total_ram, available_ram = _get_cpu_ram()
    
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        gpu_info = _get_apple_silicon_info(total_ram, available_ram)
    else:
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
    cpu_count = psutil.cpu_count(logical=True) or 1
    vm = psutil.virtual_memory()
    return cpu_count, vm.total / (1024 ** 3), vm.available / (1024 ** 3)


def _get_apple_silicon_info(total_ram: float, available_ram: float) -> dict:
    return {
        "has_gpu": True,
        "name": "Apple Silicon (Metal)",
        "total_vram_gb": round(total_ram, 1),
        "available_vram_gb": round(available_ram, 1),
        "driver": "Metal API",
    }


def _get_nvidia_gpu_info() -> dict:
    result = {"has_gpu": False, "name": "None", "total_vram_gb": 0.0, "available_vram_gb": 0.0, "driver": "None"}
    
    if PYNVML_AVAILABLE:
        try:
            pynvml.nvmlInit()
            if pynvml.nvmlDeviceGetCount() > 0:
                handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                name = pynvml.nvmlDeviceGetName(handle)
                name = name.decode("utf-8") if isinstance(name, bytes) else name
                mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                
                try:
                    driver = pynvml.nvmlSystemGetDriverVersion()
                    driver = driver.decode("utf-8") if isinstance(driver, bytes) else driver
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
            pass 

    try:
        output = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.free,driver_version", "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL, timeout=5
        ).decode("utf-8").strip().split("\n")
        
        if output:
            parts = [p.strip() for p in output[0].split(",")]
            if len(parts) >= 4:
                result.update({
                    "has_gpu": True,
                    "name": parts[0],
                    "total_vram_gb": round(float(parts[1]) / 1024, 1),
                    "available_vram_gb": round(float(parts[2]) / 1024, 1),
                    "driver": parts[3]
                })
    except Exception:
        pass
    
    return result


def detect_model_size(model_name: str) -> float:
    match = re.search(r"(\d+(?:\.\d+)?)b", model_name.lower())
    return float(match.group(1)) if match else 3.0


def get_vram_requirement(model_size_b: float) -> float:
    if model_size_b <= 3: return 0.15
    if model_size_b <= 7: return 0.30
    return 0.60


def calculate_optimal_workers(model_name: str, mode: str = "pro", system_info: Optional[SystemInfo] = None) -> int:
    system_info = system_info or get_system_info()
    model_size = detect_model_size(model_name)

    if mode.lower() == "pro":
        if system_info.has_gpu and system_info.vram_available_gb > 0.5:
            overhead = get_vram_requirement(model_size)
            # 0.5 GB güvenlik payı bırakıldı
            safe_vram = system_info.vram_available_gb - 0.5 
            max_by_vram = max(1, int(safe_vram / overhead))

            # Hız (TPS) odaklı GPU işlem limiti (Compute-Bound)
            if system_info.vram_total_gb >= 12.0:
                ceiling = 6
            elif system_info.vram_total_gb >= 8.0:
                ceiling = 4 # RTX 2060S vb. için darboğaz sınırı
            else:
                ceiling = 2

            env_limit = os.environ.get("OLLAMA_NUM_PARALLEL")
            if env_limit and env_limit.isdigit():
                ceiling = int(env_limit)

            return min(max_by_vram, ceiling)
            
        # CPU Modu
        ram_per_worker = max(1.0, model_size * 1.5)
        max_by_ram = int(system_info.available_ram_gb / ram_per_worker)
        return max(1, min(max_by_ram, system_info.cpu_count // 2, 4))
        
    return max(1, min(system_info.cpu_count, 16))


def format_system_info(system_info: SystemInfo, mode: str, model_name: Optional[str] = None) -> str:
    if mode.lower() == "pro":
        if system_info.has_gpu:
            model_str = f" ([cyan]{model_name}[/cyan])" if model_name else ""
            return f"[bold]GPU:[/bold] {system_info.gpu_name}{model_str} | [bold]VRAM:[/bold] {system_info.vram_available_gb:.1f}GB / {system_info.vram_total_gb:.1f}GB"
        return f"[bold]GPU:[/bold] None | [bold]RAM:[/bold] {system_info.available_ram_gb:.1f}GB"
    return f"[bold]CPU:[/bold] {system_info.cpu_count} cores | [bold]RAM:[/bold] {system_info.available_ram_gb:.1f}GB"