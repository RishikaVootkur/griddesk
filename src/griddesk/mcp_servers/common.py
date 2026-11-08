"""Pieces shared by the GridDesk MCP servers."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from pydantic import Field

from griddesk.db.session import is_lock_timeout

# The runtime always overwrites this argument with the real conversation id, and hides it from the
# model's view of the schema, so the model cannot act in another conversation's verified session.
ConversationId = Annotated[
    str, Field(description="Conversation id. Set by the runtime; any value the model passes is replaced.")
]

# Tools carrying this meta are for the runtime (e.g. executing approved actions) and are never shown
# to the model. They are still safe if called directly: they act only on database-approved requests.
INTERNAL_KEY = "griddesk/internal"
INTERNAL: dict[str, Any] = {INTERNAL_KEY: True}


class GridDeskServer(MCPServer):
    """MCPServer that names one crash to the client: a database lock timeout.

    The SDK keeps a crashed tool's exception text on the server, so the client would see only "Error
    executing tool". In an eval trial a lock timeout means another trial holds the row, and the runner
    must retry the trial instead of letting the model react to a fake tool failure.
    """

    async def call_tool(self, name: str, arguments: dict[str, Any], context: Any = None) -> Any:
        try:
            return await super().call_tool(name, arguments, context)
        except UnexpectedToolError as e:
            if e.__cause__ is not None and is_lock_timeout(e.__cause__):
                raise ToolError("lock timeout: blocked by another transaction") from e
            raise
