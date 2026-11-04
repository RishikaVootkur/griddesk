"""Run one MCP server over Streamable HTTP: `python -m griddesk.mcp_servers billing --port 8101`."""

from __future__ import annotations

import argparse

from griddesk.db.session import live_session_factory
from griddesk.mcp_servers import server_factories
from griddesk.tools.common import ToolContext


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("server", choices=sorted(server_factories()))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8101)
    args = parser.parse_args()
    ctx = ToolContext(sessions=live_session_factory())
    server = server_factories()[args.server](ctx)
    server.run("streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
