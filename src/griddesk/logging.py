"""Structured logging with structlog.

Every event passes through a redaction processor before it is rendered, so personal data
never reaches a log sink. The redactor is pluggable: the PII guardrail installs a Presidio
based one at startup, and the default masks obvious patterns.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, MutableMapping
from typing import Any

import structlog

Redactor = Callable[[str], str]

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"\(?\b\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")


def regex_redact(text: str) -> str:
    text = _SSN.sub("<SSN>", text)
    text = _EMAIL.sub("<EMAIL>", text)
    return _PHONE.sub("<PHONE>", text)


_redactor: Redactor = regex_redact


def set_redactor(fn: Redactor) -> None:
    global _redactor
    _redactor = fn


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return _redactor(value)
    if isinstance(value, dict):
        return {k: redact_value(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact_value(v) for v in value]
    return value


def _redact_processor(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    return {k: redact_value(v) for k, v in event_dict.items()}


def configure_logging(level: str = "INFO", json: bool = False) -> None:
    logging.basicConfig(format="%(message)s", level=level)
    renderer: Any = structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redact_processor,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> Any:
    return structlog.get_logger(name)
