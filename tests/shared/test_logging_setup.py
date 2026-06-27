"""Tests for src.shared.logging_setup."""

from __future__ import annotations

import logging

from rich.logging import RichHandler

from src.shared.console import console
from src.shared.logging_setup import setup_logging


def test_setup_logging_installs_a_rich_handler_on_root_logger() -> None:
    setup_logging()
    root = logging.getLogger()
    assert any(isinstance(h, RichHandler) for h in root.handlers)


def test_setup_logging_handler_uses_the_shared_console() -> None:
    setup_logging()
    root = logging.getLogger()
    rich_handlers = [h for h in root.handlers if isinstance(h, RichHandler)]
    assert rich_handlers
    assert rich_handlers[0].console is console


def test_setup_logging_quiets_httpx_and_httpcore() -> None:
    setup_logging()
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING


def test_setup_logging_respects_requested_level() -> None:
    setup_logging(level=logging.DEBUG)
    assert logging.getLogger().level == logging.DEBUG
    setup_logging(level=logging.INFO)  # reset for any tests that run after this one
