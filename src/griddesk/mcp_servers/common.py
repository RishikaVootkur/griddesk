"""Pieces shared by the GridDesk MCP servers."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

# The runtime always overwrites this argument with the real conversation id, and hides it from the
# model's view of the schema, so the model cannot act in another conversation's verified session.
ConversationId = Annotated[
    str, Field(description="Conversation id. Set by the runtime; any value the model passes is replaced.")
]

# Tools carrying this meta are for the runtime (e.g. executing approved actions) and are never shown
# to the model. They are still safe if called directly: they act only on database-approved requests.
INTERNAL_KEY = "griddesk/internal"
INTERNAL: dict[str, Any] = {INTERNAL_KEY: True}
