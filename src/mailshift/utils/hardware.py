"""
|
hardware.py – System hardware detection for optimal performance.
"""

from __future__ import annotations

import re
import platform
import subprocess
import os
import json
import time
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from .worker_profile_store import (
    WorkerProfileMetrics,
    build_device_signature,
    get_recommended_worker,
    record_worker_profile_run,
    set_recommended_worker,
)

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


@dataclass(frozen=True)
class WorkerPlan:
    workers: int
    requested_workers: Optional[int]
    upper_limit: int
    source: str
    reason: str
    backend: str
    mode: str
    is_effective: bool
    was_clamped: bool


def get_system_info() -> SystemInfo:
    cpu_count, total_ram, available_ram = _get_cpu_ram()
    
    if platform.system() == "Darwin":
        if platform.machine() == "arm64":
            gpu_info = _get_apple_silicon_info(total_ram, available_ram)
        else:
            gpu_info = _get_intel_mac_gpu_info(total_ram, available_ram)
    else:
        gpu_info = _get_nvidia_gpu_info(total_ram, available_ram)

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


def _get_intel_mac_gpu_info(total_ram_gb: float, available_ram_gb: float) -> dict:
    result = {"has_gpu": False, "name": "None", "total_vram_gb": 0.0, "available_vram_gb": 0.0, "driver": "None"}
    try:
        output = subprocess.check_output(
            ["system_profiler", "SPDisplaysDataType"],
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).decode("utf-8", errors="replace")
    except Exception:
        return result

    matches = re.findall(r"(?:Chipset Model|Model):\s*(.+)", output)
    for model in matches:
        gpu_name = model.strip()
        lowered = gpu_name.lower()
        if "intel" not in lowered and "amd" not in lowered and "radeon" not in lowered:
            continue

        total_vram, available_vram = _intel_shared_vram_estimate(total_ram_gb, available_ram_gb)
        result.update({
            "has_gpu": True,
            "name": gpu_name,
            "total_vram_gb": total_vram,
            "available_vram_gb": available_vram,
            "driver": "Metal API",
        })
        return result

    return result


def _intel_shared_vram_estimate(total_ram_gb: float, available_ram_gb: float) -> tuple[float, float]:
    """Estimate Intel iGPU shared memory to avoid reporting zero VRAM on UMA systems."""
    # Typical integrated GPU shared budget is up to ~50% RAM; keep this conservative.
    total_shared = min(total_ram_gb * 0.5, 16.0)
    available_shared = min(available_ram_gb * 0.5, total_shared)
    return round(total_shared, 1), round(max(available_shared, 0.0), 1)


def _get_windows_video_controllers() -> list[dict]:
    if platform.system() != "Windows":
        return []

    cmd = [
        "powershell",
        "-NoProfile",
        "-Command",
        "Get-CimInstance Win32_VideoController | "
        "Select-Object Name,AdapterRAM,DriverVersion | ConvertTo-Json -Compress",
    ]

    try:
        raw = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=5).decode("utf-8", errors="replace").strip()
        if not raw:
            return []
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else [parsed]
    except Exception:
        return []


def _get_windows_gpu_info(
    total_ram_gb: float,
    available_ram_gb: float,
    vendor_tokens: tuple[str, ...],
) -> dict:
    result = {"has_gpu": False, "name": "None", "total_vram_gb": 0.0, "available_vram_gb": 0.0, "driver": "None"}

    gpus = _get_windows_video_controllers()
    if not gpus:
        return result

    for gpu in gpus:
        name = str(gpu.get("Name", "") or "").strip()
        lowered = name.lower()
        if not any(token in lowered for token in vendor_tokens):
            continue

        adapter_ram_raw = gpu.get("AdapterRAM")
        adapter_ram = int(adapter_ram_raw) if isinstance(adapter_ram_raw, (int, float, str)) and str(adapter_ram_raw).isdigit() else 0
        total_vram = round(adapter_ram / (1024 ** 3), 1) if adapter_ram > 0 else 0.0
        if total_vram <= 0:
            total_vram, available_vram = _intel_shared_vram_estimate(total_ram_gb, available_ram_gb)
        else:
            available_vram = min(total_vram, round(available_ram_gb * 0.5, 1))

        result.update({
            "has_gpu": True,
            "name": name,
            "total_vram_gb": total_vram,
            "available_vram_gb": available_vram,
            "driver": str(gpu.get("DriverVersion", "Unknown") or "Unknown").strip(),
        })
        return result

    return result


def _get_intel_gpu_info_windows(total_ram_gb: float, available_ram_gb: float) -> dict:
    return _get_windows_gpu_info(total_ram_gb, available_ram_gb, ("intel",))


def _get_amd_gpu_info_windows(total_ram_gb: float, available_ram_gb: float) -> dict:
    # ATI token is kept for older AMD driver/device naming.
    return _get_windows_gpu_info(total_ram_gb, available_ram_gb, ("amd", "radeon", "ati"))


def _get_nvidia_gpu_info(total_ram_gb: float, available_ram_gb: float) -> dict:
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

    intel_result = _get_intel_gpu_info_windows(total_ram_gb, available_ram_gb)
    if intel_result["has_gpu"]:
        return intel_result

    amd_result = _get_amd_gpu_info_windows(total_ram_gb, available_ram_gb)
    if amd_result["has_gpu"]:
        return amd_result
    
    return result


def detect_model_size(model_name: str) -> float:
    match = re.search(r"(\d+(?:\.\d+)?)b", model_name.lower())
    return float(match.group(1)) if match else 3.0


def get_vram_requirement(model_size_b: float) -> float:
    if model_size_b <= 1.0: return 0.10
    if model_size_b <= 3.0: return 0.15
    if model_size_b <= 7.0: return 0.30
    return 0.60


def _normalize_backend(backend: str) -> str:
    return "lm_studio" if str(backend).lower() == "lm_studio" else "ollama"


def _build_device_context(system_info: SystemInfo) -> dict[str, object]:
    return {
        "os": platform.system(),
        "arch": platform.machine(),
        "cpu_count": int(system_info.cpu_count),
        "total_ram_gb": round(float(system_info.total_ram_gb), 1),
        "gpu_name": system_info.gpu_name if system_info.has_gpu else "None",
        "vram_total_gb": round(float(system_info.vram_total_gb), 1),
    }


def _build_signature(system_info: SystemInfo) -> str:
    context = _build_device_context(system_info)
    return build_device_signature(
        os_name=str(context["os"]),
        architecture=str(context["arch"]),
        cpu_count=int(context["cpu_count"]),
        total_ram_gb=float(context["total_ram_gb"]),
        gpu_name=str(context["gpu_name"]),
        vram_total_gb=float(context["vram_total_gb"]),
    )


def _probe_candidates(upper_limit: int) -> list[int]:
    safe_upper = max(1, int(upper_limit))
    candidates = {
        1,
        min(2, safe_upper),
        max(1, safe_upper // 2),
        safe_upper,
    }
    if safe_upper >= 4:
        candidates.add(4)
    if safe_upper >= 6:
        candidates.add(6)
    if safe_upper >= 8:
        candidates.add(8)
    return sorted(v for v in candidates if 1 <= v <= safe_upper)


def _probe_task(seed: int) -> int:
    value = seed & 0xFFFFFFFF
    for _ in range(900):
        value = (1664525 * value + 1013904223) & 0xFFFFFFFF
    time.sleep(0.0015)
    return value


def run_worker_hardware_probe(upper_limit: int) -> int:
    candidates = _probe_candidates(upper_limit)
    if not candidates:
        return 1
    if len(candidates) == 1:
        return candidates[0]

    task_count = max(24, min(72, max(candidates) * 6))
    throughput_by_worker: dict[int, float] = {}

    for workers in candidates:
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(_probe_task, idx + workers) for idx in range(task_count)]
            for future in as_completed(futures):
                future.result()

        elapsed = max(0.001, time.perf_counter() - started)
        throughput_by_worker[workers] = task_count / elapsed

    best_throughput = max(throughput_by_worker.values())
    conservative_threshold = best_throughput * 0.97
    viable_workers = [
        workers
        for workers, throughput in throughput_by_worker.items()
        if throughput >= conservative_threshold
    ]
    if viable_workers:
        return min(viable_workers)

    return max(throughput_by_worker, key=throughput_by_worker.get)


def resolve_worker_plan(
    model_name: str,
    mode: str = "pro",
    system_info: Optional[SystemInfo] = None,
    manual_workers: Optional[int] = None,
    backend: str = "ollama",
    power_worker_probe: bool = False,
) -> WorkerPlan:
    system_info = system_info or get_system_info()
    model_size = detect_model_size(model_name)
    mode_lower = mode.lower()
    backend_name = _normalize_backend(backend)

    profile_warm_start: Optional[int] = None
    probe_recommendation: Optional[int] = None

    if mode_lower == "pro":
        if system_info.has_gpu and system_info.vram_available_gb > 0.5:
            overhead = get_vram_requirement(model_size)
            safe_vram = max(0.0, system_info.vram_available_gb - 0.5)
            max_by_vram = max(1, int(safe_vram / overhead))

            base_ceiling = 2
            if system_info.vram_total_gb >= 20.0:
                base_ceiling = 8
            elif system_info.vram_total_gb >= 12.0:
                base_ceiling = 6
            elif system_info.vram_total_gb >= 8.0:
                base_ceiling = 4

            if model_size <= 1.0:
                backend_ceiling = base_ceiling * 3
            elif model_size <= 3.0:
                backend_ceiling = base_ceiling * 2
            else:
                backend_ceiling = base_ceiling

            if backend_name == "lm_studio":
                backend_ceiling = min(backend_ceiling, 10)

            env_limit = None
            env_limit_raw = os.environ.get("OLLAMA_NUM_PARALLEL")
            if backend_name == "ollama" and env_limit_raw and env_limit_raw.isdigit():
                env_limit = max(1, int(env_limit_raw))
                backend_ceiling = min(backend_ceiling, env_limit)

            upper_limit = max(1, min(max_by_vram, backend_ceiling))
            auto_workers = upper_limit
            reason_parts = [f"gpu-vram-cap={max_by_vram}", f"backend-cap={backend_ceiling}"]
            if env_limit is not None:
                reason_parts.append(f"env-cap={env_limit}")
            limit_reason = ", ".join(reason_parts)
        else:
            ram_per_worker = max(1.0, model_size * 1.2)
            max_by_ram = max(1, int(system_info.available_ram_gb / ram_per_worker))

            cpu_ceiling = max(2, system_info.cpu_count - 2)
            if backend_name == "lm_studio":
                cpu_ceiling = min(cpu_ceiling, 6)

            upper_limit = max(1, min(max_by_ram, cpu_ceiling))
            auto_workers = upper_limit
            limit_reason = f"cpu-ram-cap={max_by_ram}, cpu-cap={cpu_ceiling}"

        profile_warm_start = get_recommended_worker(
            device_signature=_build_signature(system_info),
            backend=backend_name,
            model_name=model_name,
            upper_limit=upper_limit,
        )
        if profile_warm_start is not None:
            auto_workers = profile_warm_start
            limit_reason = f"{limit_reason}, profile-warm-start={profile_warm_start}"
        elif power_worker_probe:
            probe_recommendation = run_worker_hardware_probe(upper_limit=upper_limit)
            auto_workers = probe_recommendation
            limit_reason = f"{limit_reason}, power-probe={probe_recommendation}"
            set_recommended_worker(
                device_signature=_build_signature(system_info),
                backend=backend_name,
                model_name=model_name,
                recommended_workers=probe_recommendation,
                upper_limit=upper_limit,
                source="power-worker-probe",
                device_context=_build_device_context(system_info),
            )

        is_effective = True
    else:
        upper_limit = max(1, min(system_info.cpu_count, 16))
        auto_workers = upper_limit
        limit_reason = f"fast-cpu-cap={upper_limit}"
        is_effective = False

    requested = manual_workers if manual_workers is not None and manual_workers > 0 else None
    if requested is None:
        return WorkerPlan(
            workers=auto_workers,
            requested_workers=None,
            upper_limit=upper_limit,
            source=(
                "auto-probe" if probe_recommendation is not None
                else "auto-profile" if profile_warm_start is not None
                else "auto"
            ),
            reason=f"auto ({limit_reason})",
            backend=backend_name,
            mode=mode_lower,
            is_effective=is_effective,
            was_clamped=False,
        )

    workers = min(requested, upper_limit)
    was_clamped = workers < requested
    if was_clamped:
        reason = f"manual {requested} -> {workers} (safe upper limit={upper_limit}; {limit_reason})"
        source = "manual-clamped"
    else:
        reason = f"manual {requested} accepted (safe upper limit={upper_limit}; {limit_reason})"
        source = "manual"

    return WorkerPlan(
        workers=workers,
        requested_workers=requested,
        upper_limit=upper_limit,
        source=source,
        reason=reason,
        backend=backend_name,
        mode=mode_lower,
        is_effective=is_effective,
        was_clamped=was_clamped,
    )


def persist_worker_profile_run(
    model_name: str,
    used_workers: int,
    upper_limit: int,
    sample_count: int,
    timeout_rate: float,
    error_rate: float,
    p95_latency_s: float,
    throughput: float,
    backend: str = "ollama",
    mode: str = "pro",
    system_info: Optional[SystemInfo] = None,
) -> Optional[int]:
    mode_lower = str(mode).lower()
    if mode_lower != "pro":
        return None

    safe_sample_count = max(0, int(sample_count or 0))
    if safe_sample_count <= 0:
        return None

    info = system_info or get_system_info()
    backend_name = _normalize_backend(backend)
    device_context = _build_device_context(info)

    metrics = WorkerProfileMetrics(
        sample_count=safe_sample_count,
        timeout_rate=max(0.0, float(timeout_rate or 0.0)),
        error_rate=max(0.0, float(error_rate or 0.0)),
        p95_latency_s=max(0.0, float(p95_latency_s or 0.0)),
        throughput=max(0.0, float(throughput or 0.0)),
    )

    try:
        return record_worker_profile_run(
            device_signature=_build_signature(info),
            backend=backend_name,
            model_name=model_name,
            observed_workers=max(1, int(used_workers)),
            upper_limit=max(1, int(upper_limit)),
            metrics=metrics,
            device_context=device_context,
        )
    except Exception:
        return None


def calculate_optimal_workers(
    model_name: str, 
    mode: str = "pro", 
    system_info: Optional[SystemInfo] = None,
    manual_workers: Optional[int] = None,
    backend: str = "ollama",
    power_worker_probe: bool = False,
) -> int:
    plan = resolve_worker_plan(
        model_name=model_name,
        mode=mode,
        system_info=system_info,
        manual_workers=manual_workers,
        backend=backend,
        power_worker_probe=power_worker_probe,
    )
    return plan.workers


def format_system_info(system_info: SystemInfo, mode: str, model_name: Optional[str] = None) -> str:
    if mode.lower() == "pro":
        if system_info.has_gpu:
            model_str = f" ([cyan]{model_name}[/cyan])" if model_name else ""
            return f"[bold]GPU:[/bold] {system_info.gpu_name}{model_str} | [bold]VRAM:[/bold] {system_info.vram_available_gb:.1f}GB / {system_info.vram_total_gb:.1f}GB"
        return f"[bold]GPU:[/bold] None | [bold]RAM:[/bold] {system_info.available_ram_gb:.1f}GB"
    return f"[bold]CPU:[/bold] {system_info.cpu_count} cores | [bold]RAM:[/bold] {system_info.available_ram_gb:.1f}GB"
