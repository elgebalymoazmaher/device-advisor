"""Tests for src.shared.settings.

These exercise the private sizing helpers directly with monkeypatched
os.cpu_count/_total_ram_mb, since the public WORKER_COUNT constant is
computed once at import time from the real machine.
"""

from __future__ import annotations

import importlib
import os

from src.shared import settings


def test_env_override_positive_int_wins(monkeypatch) -> None:
    monkeypatch.setenv("DEVICE_ADVISOR_WORKERS", "7")
    assert settings._compute_worker_count() == 7


def test_env_override_invalid_falls_back_to_computed_base(monkeypatch) -> None:
    monkeypatch.setenv("DEVICE_ADVISOR_WORKERS", "not-a-number")
    monkeypatch.setattr(os, "cpu_count", lambda: 4)
    monkeypatch.setattr(settings, "_total_ram_mb", lambda: 4096)
    # base = min(4*8, max(4, 4096//50), 50) = min(32, 81, 50) = 32
    assert settings._compute_worker_count() == 32


def test_env_override_zero_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("DEVICE_ADVISOR_WORKERS", "0")
    monkeypatch.setattr(os, "cpu_count", lambda: 2)
    monkeypatch.setattr(settings, "_total_ram_mb", lambda: 4096)
    assert settings._compute_worker_count() == 16  # min(16, 81, 50)


def test_env_override_negative_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("DEVICE_ADVISOR_WORKERS", "-5")
    monkeypatch.setattr(os, "cpu_count", lambda: 2)
    monkeypatch.setattr(settings, "_total_ram_mb", lambda: 4096)
    assert settings._compute_worker_count() == 16


def test_no_env_uses_cpu_and_ram_based_default(monkeypatch) -> None:
    monkeypatch.delenv("DEVICE_ADVISOR_WORKERS", raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: 16)
    monkeypatch.setattr(settings, "_total_ram_mb", lambda: 1000)
    # base = min(16*8=128, max(4, 1000//50=20), 50) = 20
    assert settings._compute_worker_count() == 20


def test_low_ram_has_a_floor_of_4(monkeypatch) -> None:
    monkeypatch.delenv("DEVICE_ADVISOR_WORKERS", raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: 1)
    monkeypatch.setattr(settings, "_total_ram_mb", lambda: 10)
    # base = min(1*8=8, max(4, 10//50=0)=4, 50) = 4
    assert settings._compute_worker_count() == 4


def test_cpu_count_none_falls_back_to_4_cores(monkeypatch) -> None:
    monkeypatch.delenv("DEVICE_ADVISOR_WORKERS", raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    monkeypatch.setattr(settings, "_total_ram_mb", lambda: 4096)
    # base = min((None or 4)*8=32, max(4, 81), 50) = 32
    assert settings._compute_worker_count() == 32


def test_total_ram_mb_returns_a_positive_int_on_this_machine() -> None:
    assert settings._total_ram_mb() > 0


def test_total_ram_mb_falls_back_to_4096_if_meminfo_unreadable(monkeypatch) -> None:
    monkeypatch.setattr(settings.platform, "system", lambda: "Linux")

    def _boom(*args, **kwargs):
        raise OSError("no /proc/meminfo here")

    monkeypatch.setattr(settings.Path, "open", _boom)
    assert settings._total_ram_mb() == 4096


def test_data_dir_respects_env_override(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    try:
        importlib.reload(settings)
        assert tmp_path == settings.DATA_DIR
        assert tmp_path / "brands.json" == settings.BRANDS_FILE
        assert tmp_path / "specs" / "retries.json" == settings.RETRIES_FILE
    finally:
        importlib.reload(settings)  # restore real module state for later tests
