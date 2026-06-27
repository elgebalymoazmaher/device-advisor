"""Tests for src.shared.storage."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.shared.storage import json_atomic_save, json_load


def test_round_trip(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    json_atomic_save({"a": 1, "b": [1, 2, 3]}, target)
    assert json_load(target, None) == {"a": 1, "b": [1, 2, 3]}


def test_save_creates_parent_dirs(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "dir" / "data.json"
    json_atomic_save([1, 2, 3], target)
    assert target.exists()
    assert json_load(target, None) == [1, 2, 3]


def test_save_leaves_no_tmp_file_behind(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    json_atomic_save({"ok": True}, target)
    leftovers = list(tmp_path.glob("*.tmp.*"))
    assert leftovers == []


def test_save_overwrites_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    json_atomic_save({"version": 1}, target)
    json_atomic_save({"version": 2}, target)
    assert json_load(target, None) == {"version": 2}


def test_load_missing_file_returns_default(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.json"
    assert json_load(missing, "fallback") == "fallback"
    assert json_load(missing, []) == []


def test_load_corrupt_json_returns_default(tmp_path: Path) -> None:
    target = tmp_path / "corrupt.json"
    target.write_text("{not valid json", encoding="utf-8")
    assert json_load(target, {"default": True}) == {"default": True}


def test_load_accepts_str_path(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    json_atomic_save({"x": 1}, target)
    assert json_load(str(target), None) == {"x": 1}


def test_save_cleans_up_tmp_file_and_reraises_on_serialization_failure(
    tmp_path: Path,
) -> None:
    target = tmp_path / "data.json"

    class _Unserializable:
        pass

    with pytest.raises(TypeError):
        json_atomic_save({"bad": _Unserializable()}, target)

    assert not target.exists()
    assert list(tmp_path.glob("*.tmp.*")) == []
