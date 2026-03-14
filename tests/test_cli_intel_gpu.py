"""Tests for Intel GPU restart logic in cli_utils."""

import os
from unittest.mock import patch, MagicMock

import pytest

from hardware import SystemInfo
import cli_utils


INTEL_GPU_SYSTEM = SystemInfo(
    cpu_count=12,
    total_ram_gb=32.0,
    available_ram_gb=16.0,
    has_gpu=True,
    gpu_name="Intel(R) Arc(TM) A750 Graphics",
    vram_total_gb=8.0,
    vram_available_gb=4.0,
    gpu_driver="32.0.101.6078",
)

NVIDIA_GPU_SYSTEM = SystemInfo(
    cpu_count=12,
    total_ram_gb=32.0,
    available_ram_gb=16.0,
    has_gpu=True,
    gpu_name="NVIDIA RTX 4070",
    vram_total_gb=12.0,
    vram_available_gb=9.0,
    gpu_driver="555.85",
)


def test_build_ollama_env_sets_intel_gpu_flag(monkeypatch):
    monkeypatch.setattr(cli_utils, "get_system_info", lambda: INTEL_GPU_SYSTEM)
    env = cli_utils._build_ollama_env()
    assert env.get("OLLAMA_INTEL_GPU") == "1"


def test_build_ollama_env_no_flag_for_nvidia(monkeypatch):
    monkeypatch.setattr(cli_utils, "get_system_info", lambda: NVIDIA_GPU_SYSTEM)
    env = cli_utils._build_ollama_env()
    # Should NOT have OLLAMA_INTEL_GPU unless it was already in os.environ
    if "OLLAMA_INTEL_GPU" not in os.environ:
        assert "OLLAMA_INTEL_GPU" not in env


def test_ensure_ollama_intel_gpu_skips_nvidia(monkeypatch):
    monkeypatch.setattr(cli_utils, "get_system_info", lambda: NVIDIA_GPU_SYSTEM)
    result = cli_utils.ensure_ollama_intel_gpu()
    assert result is False


def test_ensure_ollama_intel_gpu_skips_when_env_already_set(monkeypatch):
    monkeypatch.setattr(cli_utils, "get_system_info", lambda: INTEL_GPU_SYSTEM)
    monkeypatch.setenv("OLLAMA_INTEL_GPU", "1")
    result = cli_utils.ensure_ollama_intel_gpu()
    assert result is False


def test_ensure_ollama_intel_gpu_skips_when_ollama_not_running(monkeypatch):
    monkeypatch.setattr(cli_utils, "get_system_info", lambda: INTEL_GPU_SYSTEM)
    monkeypatch.delenv("OLLAMA_INTEL_GPU", raising=False)
    monkeypatch.setattr(cli_utils, "_is_ollama_running", lambda: False)
    result = cli_utils.ensure_ollama_intel_gpu()
    assert result is False


def test_ensure_ollama_intel_gpu_restarts_when_needed(monkeypatch):
    monkeypatch.setattr(cli_utils, "get_system_info", lambda: INTEL_GPU_SYSTEM)
    monkeypatch.delenv("OLLAMA_INTEL_GPU", raising=False)

    running_calls = iter([True, False, False, True])  # first: already running → stop → restart → up
    monkeypatch.setattr(cli_utils, "_is_ollama_running", lambda: next(running_calls))
    monkeypatch.setattr(cli_utils, "stop_ollama", lambda: True)
    monkeypatch.setattr(cli_utils, "_launch_ollama_process", lambda env: True)

    import time as _time_mod
    monkeypatch.setattr(_time_mod, "sleep", lambda _s: None)  # skip sleeps

    result = cli_utils.ensure_ollama_intel_gpu()
    assert result is True
    assert os.environ.get("OLLAMA_INTEL_GPU") == "1"
    # Clean up
    monkeypatch.delenv("OLLAMA_INTEL_GPU", raising=False)


def test_ensure_ollama_intel_gpu_fails_gracefully_on_launch_failure(monkeypatch):
    monkeypatch.setattr(cli_utils, "get_system_info", lambda: INTEL_GPU_SYSTEM)
    monkeypatch.delenv("OLLAMA_INTEL_GPU", raising=False)
    monkeypatch.setattr(cli_utils, "_is_ollama_running", lambda: True)
    monkeypatch.setattr(cli_utils, "stop_ollama", lambda: True)
    monkeypatch.setattr(cli_utils, "_launch_ollama_process", lambda env: False)

    result = cli_utils.ensure_ollama_intel_gpu()
    assert result is False
