"""Disk cache for LLM responses, keyed on the full request.

The key is a SHA-256 over the canonical JSON of the whole request (model, messages, tools and
sampling parameters) plus a namespace. Eval trials pass their trial index as the namespace, so
repeated trials of one task are independent samples rather than copies of the first, while a
replay of the same trial hits the cache byte for byte. Entries are small JSON files fanned out
by key prefix, which keeps the committed CI cache diff-friendly.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from pydantic import ValidationError

from griddesk.gateway.types import LLMRequest, LLMResponse

# Bump when the gateway's own wire mapping changes in a way that alters model output.
CACHE_VERSION = 1


def request_key(req: LLMRequest, namespace: str = "", provider_fingerprint: str = "") -> str:
    """Hash of the full request, the namespace, and the provider settings that shape the output."""
    payload = json.dumps(
        {
            "v": CACHE_VERSION,
            "ns": namespace,
            "provider": provider_fingerprint,
            "req": req.model_dump(mode="json"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class DiskCache:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> LLMResponse | None:
        p = self._path(key)
        if not p.exists():
            return None
        try:
            return LLMResponse.model_validate_json(p.read_text())
        except (ValidationError, OSError, UnicodeDecodeError):
            return None  # a corrupt or conflicted entry is a miss; the next write replaces it

    def put(self, key: str, resp: LLMResponse) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = resp.model_copy(update={"cached": False}).model_dump_json(indent=1, exclude_defaults=True)
        # Atomic write so concurrent trials never read a half-written entry.
        fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(data + "\n")
            os.replace(tmp, p)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
