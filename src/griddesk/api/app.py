"""FastAPI backend: customer chat (SSE), operator console (approvals, audit, conversations), eval runs.

The contract the web app relies on is documented in docs/api-contract.md. Conversations run on the
triage + specialists graph with a Postgres checkpointer, so a conversation paused for approval
survives an API restart. Operator endpoints require `Authorization: Bearer $OPERATOR_TOKEN` when that
variable is set; the operator identity recorded on a decision comes from the server, and the
display name in the request body is only a label.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from griddesk.agents.graph import AgentConfig
from griddesk.agents.runtime import ConversationRunner
from griddesk.agents.toolbox import Toolbox
from griddesk.config import REPO_ROOT, get_settings
from griddesk.db import models as m
from griddesk.db.session import get_engine, live_session_factory
from griddesk.gateway import DEFAULT_CHAIN, build_gateway
from griddesk.guardrails.action_guard import ActionGuard
from griddesk.logging import configure_logging, get_logger, redact_text
from griddesk.mcp_servers import server_factories
from griddesk.runtime import Clock
from griddesk.tools import approvals
from griddesk.tools.common import ToolContext

log = get_logger("griddesk.api")
RUNS_DIR = REPO_ROOT / "evals" / "runs"


@dataclass
class AppState:
    ctx: ToolContext
    runner: ConversationRunner
    toolbox: Toolbox


def _mcp_targets(ctx: ToolContext) -> dict[str, Any]:
    """In-process servers by default; Streamable HTTP when MCP_<NAME>_URL is set (docker compose)."""
    targets: dict[str, Any] = {}
    for name, factory in server_factories().items():
        url = os.environ.get(f"MCP_{name.upper()}_URL")
        targets[name] = url or factory(ctx)
    return targets


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    if os.environ.get("GRIDDESK_PRESIDIO", "1") == "1":
        from griddesk.guardrails.pii import install

        install()
    if s.tracing_enabled:
        from griddesk.observability.tracing import setup_tracing

        setup_tracing(s.phoenix_collector_endpoint)
    sessions = live_session_factory(get_engine())
    ctx = ToolContext(sessions=sessions, clock=Clock())
    toolbox = await Toolbox.open(_mcp_targets(ctx))
    toolbox.guard = ActionGuard(sessions)
    gateway = build_gateway()
    model = os.environ.get("GRIDDESK_AGENT_MODEL", DEFAULT_CHAIN[0])
    config = AgentConfig(
        model=model, fallbacks=[m_ for m_ in DEFAULT_CHAIN if m_ != model], today=s.simulated_now.date()
    )
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    dsn = s.database_url.get_secret_value().replace("+psycopg", "")
    async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
        await saver.setup()
        runner = ConversationRunner(toolbox, gateway, config, design="multi", checkpointer=saver)
        app.state.griddesk = AppState(ctx=ctx, runner=runner, toolbox=toolbox)
        log.info("api ready", model=model)
        try:
            yield
        finally:
            await toolbox.close()
            await gateway.aclose()


app = FastAPI(title="GridDesk API", version="0.1.0", lifespan=lifespan)


def state(request: Request) -> AppState:
    st: AppState = request.app.state.griddesk
    return st


def operator(authorization: str | None = Header(default=None)) -> str:
    token = os.environ.get("OPERATOR_TOKEN")
    if token and authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="Operator token required.")
    return "operator"


class NewConversation(BaseModel):
    conversation_id: str


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class DecisionIn(BaseModel):
    decision: Literal["approve", "deny"]
    operator: str = Field(default="operator", max_length=60, description="Display name only.")


def _now() -> datetime:
    return datetime.now(UTC)


def _touch(
    st: AppState,
    conv_id: str,
    *,
    last: str | None = None,
    agent: str | None = None,
    status: str | None = None,
    count: int | None = None,
) -> None:
    with st.ctx.sessions() as s:
        row = s.get(m.Conversation, conv_id)
        if row is None:
            row = m.Conversation(
                id=conv_id,
                created_at=_now(),
                updated_at=_now(),
                status="active",
                last_message="",
                message_count=0,
            )
            s.add(row)
        row.updated_at = _now()
        if last is not None:
            row.last_message = redact_text(last)[:300]
        if agent is not None:
            row.agent = agent
        if status is not None:
            row.status = status
        if count is not None:
            row.message_count = count
        s.commit()


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/conversations")
async def create_conversation(st: AppState = Depends(state)) -> NewConversation:
    conv_id = f"web-{uuid.uuid4().hex[:12]}"
    _touch(st, conv_id)
    return NewConversation(conversation_id=conv_id)


def _sse(event: str, data: dict[str, Any]) -> dict[str, str]:
    return {"event": event, "data": json.dumps(data)}


@app.post("/api/conversations/{conv_id}/messages")
async def send_message(conv_id: str, body: MessageIn, st: AppState = Depends(state)) -> EventSourceResponse:
    async def events() -> AsyncIterator[dict[str, str]]:
        try:
            res = await st.runner.send(conv_id, body.text)
            for msg in await _new_agent_messages(st, conv_id, res.reply):
                yield _sse("agent", msg)
            if res.awaiting_approval:
                yield _sse(
                    "approval",
                    {
                        "approval_request_id": res.awaiting_approval["approval_request_id"],
                        "action": res.awaiting_approval["action"],
                    },
                )
            if res.error:
                yield _sse("error", {"message": "The assistant hit a problem; a specialist will follow up."})
            msgs = await st.runner.messages(conv_id)
            _touch(
                st,
                conv_id,
                last=res.reply or body.text,
                agent=res.agents[-1] if res.agents else None,
                status="awaiting_approval" if res.awaiting_approval else "active",
                count=_visible_count(msgs),
            )
        except Exception as e:
            log.exception("turn failed", conversation_id=conv_id)
            yield _sse("error", {"message": f"{type(e).__name__}"})
        yield _sse("done", {})

    return EventSourceResponse(events())


async def _new_agent_messages(st: AppState, conv_id: str, reply: str) -> list[dict[str, Any]]:
    msgs = await st.runner.messages(conv_id)
    out: list[dict[str, Any]] = []
    for msg in reversed(msgs):
        if msg.get("role") == "user" and not str(msg.get("content", "")).startswith("RUNTIME NOTICE"):
            break
        if msg.get("role") == "assistant" and msg.get("content"):
            out.append({"text": msg["content"], "agent": msg.get("agent")})
    return list(reversed(out)) or ([{"text": reply, "agent": None}] if reply else [])


def _visible_count(msgs: list[dict[str, Any]]) -> int:
    return sum(1 for x in msgs if x.get("role") in ("user", "assistant") and x.get("content"))


@app.get("/api/conversations")
async def list_conversations(st: AppState = Depends(state)) -> list[dict[str, Any]]:
    with st.ctx.sessions() as s:
        rows = s.scalars(select(m.Conversation).order_by(m.Conversation.updated_at.desc()).limit(100)).all()
        return [
            {
                "conversation_id": r.id,
                "status": r.status,
                "last_message": r.last_message,
                "agent": r.agent,
                "updated_at": r.updated_at.isoformat(),
                "message_count": r.message_count,
            }
            for r in rows
        ]


@app.get("/api/conversations/{conv_id}")
async def get_conversation(conv_id: str, st: AppState = Depends(state)) -> dict[str, Any]:
    msgs = await st.runner.messages(conv_id)
    out = []
    for x in msgs:
        role, content = x.get("role"), x.get("content") or ""
        if role == "user":
            kind = "system" if content.startswith("RUNTIME NOTICE") else "customer"
            out.append({"role": kind, "text": content, "agent": None})
        elif role == "assistant" and content:
            out.append({"role": "agent", "text": content, "agent": x.get("agent")})
    pending = await st.runner.awaiting_approval(conv_id)
    return {
        "conversation_id": conv_id,
        "messages": out,
        "awaiting_approval": {
            "approval_request_id": pending["approval_request_id"],
            "action": pending["action"],
        }
        if pending
        else None,
    }


@app.get("/api/approvals")
async def list_approvals(
    status: str = "pending", st: AppState = Depends(state), _: str = Depends(operator)
) -> list[dict[str, Any]]:
    with st.ctx.sessions() as s:
        rows = s.scalars(
            select(m.ApprovalRequest)
            .where(m.ApprovalRequest.status == status)
            .order_by(m.ApprovalRequest.requested_at.desc())
            .limit(200)
        ).all()
        return [
            {
                "approval_request_id": r.id,
                "conversation_id": r.conversation_id,
                "account_id": r.account_id,
                "action": r.action,
                "arguments": r.arguments,
                "rule_id": r.rule_id,
                "reason": r.reason,
                "status": r.status,
                "requested_at": r.requested_at.isoformat(),
            }
            for r in rows
        ]


@app.post("/api/approvals/{approval_id}/decision")
async def decide_approval(
    approval_id: str, body: DecisionIn, st: AppState = Depends(state), who: str = Depends(operator)
) -> dict[str, str]:
    try:
        req = approvals.decide(st.ctx, approval_id, body.decision, operator=f"{who}:{body.operator[:40]}")
    except KeyError:
        raise HTTPException(status_code=404, detail="No such approval request.") from None
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    reply = ""
    if await st.runner.awaiting_approval(req.conversation_id):
        res = await st.runner.resume(req.conversation_id)
        reply = res.reply
        _touch(
            st,
            req.conversation_id,
            last=reply,
            status="awaiting_approval" if res.awaiting_approval else "active",
        )
    after = approvals.get(st.ctx, approval_id)
    return {"status": after.status if after else req.status, "reply": reply}


@app.get("/api/audit")
async def audit_log(
    limit: int = Query(200, le=1000),
    outcome: str | None = None,
    st: AppState = Depends(state),
    _: str = Depends(operator),
) -> list[dict[str, Any]]:
    with st.ctx.sessions() as s:
        stmt = select(m.AuditLog).order_by(m.AuditLog.id.desc()).limit(limit)
        if outcome:
            stmt = stmt.where(m.AuditLog.outcome == outcome)
        return [
            {
                "id": r.id,
                "ts": r.ts.isoformat(),
                "conversation_id": r.conversation_id,
                "actor": r.actor,
                "action": r.action,
                "account_id": r.account_id,
                "outcome": r.outcome,
                "rule_id": r.rule_id,
                "details": r.details,
            }
            for r in s.scalars(stmt)
        ]


def run_summary(path: Path, run: dict[str, Any]) -> dict[str, Any]:
    c = run["config"]
    return {
        "name": path.stem,
        "label": c.get("label") or path.stem,
        "model": c["model"],
        "design": c["design"],
        "enforce_policy": c["enforce_policy"],
        "trials": c["trials"],
        "created_at": run["created_at"],
        "summary": run["summary"],
    }


@app.get("/api/eval/runs")
async def eval_runs() -> list[dict[str, Any]]:
    out = []
    for p in sorted(RUNS_DIR.glob("*.json")):
        out.append(run_summary(p, json.loads(p.read_text())))
    return sorted(out, key=lambda r: r["created_at"], reverse=True)


@app.get("/api/eval/runs/{name}")
async def eval_run(name: str) -> dict[str, Any]:
    if not name.replace("-", "").replace("_", "").replace(".", "").isalnum():
        raise HTTPException(status_code=400, detail="Bad run name.")
    p = RUNS_DIR / f"{name}.json"
    if not p.exists():
        raise HTTPException(status_code=404, detail="No such run.")
    data: dict[str, Any] = json.loads(p.read_text())
    return data
