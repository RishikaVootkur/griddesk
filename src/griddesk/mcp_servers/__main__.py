"""Run one MCP server over Streamable HTTP: `python -m griddesk.mcp_servers billing --port 8101`."""

from __future__ import annotations

import argparse
import os

from griddesk.db.session import live_session_factory
from griddesk.mcp_servers import server_factories
from griddesk.tools.common import ToolContext


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("server", choices=sorted(server_factories()))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8101)
    args = parser.parse_args()
    # The servers trust the runtime to set conversation_id and have no auth of their own, so they
    # only listen on loopback or the private compose network (see docs/threat-model.md).
    if (
        args.host not in {"127.0.0.1", "localhost", "::1"}
        and os.environ.get("GRIDDESK_MCP_PRIVATE_NET") != "1"
    ):
        parser.error(
            "refusing to listen beyond loopback; set GRIDDESK_MCP_PRIVATE_NET=1 inside a private network"
        )
    ctx = ToolContext(sessions=live_session_factory())
    server = server_factories()[args.server](ctx)
    server.run("streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
