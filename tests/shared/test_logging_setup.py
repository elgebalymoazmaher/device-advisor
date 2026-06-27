"""Tests for src.shared.logging_setup."""

from __future__ import annotations

import logging
from collections.abc import Generator

import pytest
from rich.logging import RichHandler

from src.shared.console import console
from src.shared.logging_setup import setup_logging


@pytest.fixture
def _restore_root_logger() -> Generator[None, None, None]:
    """Save and restore the root logger's handlers and level after each test."""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    saved_httpx_level = logging.getLogger("httpx").level
    saved_httpcore_level = logging.getLogger("httpcore").level
    yield
    root.handlers = saved_handlers
    root.setLevel(saved_level)
    logging.getLogger("httpx").setLevel(saved_httpx_level)
    logging.getLogger("httpcore").setLevel(saved_httpcore_level)


def test_setup_logging_installs_a_rich_handler_on_root_logger(
    _restore_root_logger,
) -> None:
    setup_logging()
    root = logging.getLogger()
    assert any(isinstance(h, RichHandler) for h in root.handlers)


def test_setup_logging_handler_uses_the_shared_console(
    _restore_root_logger,
) -> None:
    setup_logging()
    root = logging.getLogger()
    rich_handlers = [h for h in root.handlers if isinstance(h, RichHandler)]
    assert rich_handlers
    assert rich_handlers[0].console is console


def test_setup_logging_quiets_httpx_and_httpcore(
    _restore_root_logger,
) -> None:
    setup_logging()
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING


def test_setup_logging_respects_requested_level(
    _restore_root_logger,
) -> None:
    setup_logging(level=logging.DEBUG)
    assert logging.getLogger().level == logging.DEBUG
