"""FastAPI backend: customer chat (SSE), operator console (approvals, audit, conversations), eval runs.

The contract the web app relies on is documented in docs/api-contract.md. Conversations run on the
triage + specialists graph with a Postgres checkpointer, so a conversation paused for approval
survives an API restart.

Access:
- A customer conversation is reachable only with the secret returned when it was created, sent as
  the `X-Conversation-Token` header. Unknown conversation ids are a 404.
- Operator endpoints require `Authorization: Bearer $OPERATOR_TOKEN`. The API refuses to start
  without a token unless `GRIDDESK_OPERATOR_AUTH=off` is set explicitly (local development only).
  The identity recorded on a decision is `OPERATOR_ID` (default `console`), never the display name
  typed in the console.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import uuid
from collections.abc import AsyncIterator, Coroutine
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sse_starlette.sse import EventSourceResponse

from griddesk.agents.graph import AgentConfig, is_internal_note
from griddesk.agents.runtime import ConversationRunner, TurnResult
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
TOKEN_HEADER = "X-Conversation-Token"  # noqa: S105 (a header name)
_IDENTITY = re.compile(r"^[A-Za-z0-9._-]{1,31}$")
_RUN_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass
class AppState:
    ctx: ToolContext
    runner: ConversationRunner
    toolbox: Toolbox
    # Turns run as tasks of their own so a client disconnect never cancels one halfway.
    tasks: set[asyncio.Task[Any]] = field(default_factory=set)


def _mcp_targets(ctx: ToolContext) -> dict[str, Any]:
    """In-process servers by default; Streamable HTTP when MCP_<NAME>_URL is set (docker compose)."""
    targets: dict[str, Any] = {}
    for name, factory in server_factories().items():
        url = os.environ.get(f"MCP_{name.upper()}_URL")
        targets[name] = url or factory(ctx)
    return targets


def _operator_auth_off() -> bool:
    return os.environ.get("GRIDDESK_OPERATOR_AUTH", "").lower() == "off"


def operator_identity() -> str:
    ident = os.environ.get("OPERATOR_ID") or "console"
    if not _IDENTITY.match(ident):
        raise RuntimeError("OPERATOR_ID must be 1 to 31 letters, digits, '.', '_' or '-'.")
    return ident


def check_operator_config() -> None:
    """Fail closed: refuse to start with open operator endpoints unless that is asked for explicitly."""
    operator_identity()
    if not os.environ.get("OPERATOR_TOKEN") and not _operator_auth_off():
        raise RuntimeError(
            "OPERATOR_TOKEN is not set. Set it (for example `openssl rand -hex 32`), or set "
            "GRIDDESK_OPERATOR_AUTH=off for local development without operator auth."
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    check_operator_config()
    if os.environ.get("GRIDDESK_PRESIDIO", "1") == "1":
        from griddesk.guardrails.pii import install

        install()
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    async with AsyncExitStack() as stack:
        if s.tracing_enabled:
            from griddesk.observability.tracing import setup_tracing, shutdown_tracing

            setup_tracing(s.phoenix_collector_endpoint)
            stack.callback(shutdown_tracing)
        # A pool, not one connection: checkpoint reads and writes of different conversations run in
        # parallel, and a connection that died with a Postgres restart is replaced on checkout.
        pool: AsyncConnectionPool[Any] = AsyncConnectionPool(
            s.database_url.get_secret_value().replace("+psycopg", ""),
            min_size=1,
            max_size=8,
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
            check=AsyncConnectionPool.check_connection,
            open=False,
        )
        await pool.open(wait=True, timeout=30)
        stack.push_async_callback(pool.close)
        saver = AsyncPostgresSaver(pool)
        await saver.setup()
        sessions = live_session_factory(get_engine())
        ctx = ToolContext(sessions=sessions, clock=Clock())
        # The API keeps no call history: it never reads it, and it would grow for the process lifetime.
        toolbox = await Toolbox.open(_mcp_targets(ctx), history=0)
        stack.push_async_callback(toolbox.close)
        toolbox.guard = ActionGuard(sessions)
        # Live customer text must not be written to the disk cache or answered from a stale entry.
        gateway = build_gateway(cache_mode="off")
        stack.push_async_callback(gateway.aclose)
        model = os.environ.get("GRIDDESK_AGENT_MODEL", DEFAULT_CHAIN[0])
        config = AgentConfig(
            model=model, fallbacks=[m_ for m_ in DEFAULT_CHAIN if m_ != model], today=s.simulated_now.date()
        )
        runner = ConversationRunner(toolbox, gateway, config, design="multi", checkpointer=saver)
        st = AppState(ctx=ctx, runner=runner, toolbox=toolbox)
        app.state.griddesk = st
        log.info("api ready", model=model)
        try:
            yield
        finally:
            if st.tasks:  # let turns that are still running finish and checkpoint
                await asyncio.wait(st.tasks, timeout=30)


app = FastAPI(title="GridDesk API", version="0.1.0", lifespan=lifespan)


def state(request: Request) -> AppState:
    st: AppState = request.app.state.griddesk
    return st


def _bearer_ok(authorization: str | None) -> bool:
    token = os.environ.get("OPERATOR_TOKEN")
    if not token or not authorization:
        return False
    scheme, _, given = authorization.partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(given.strip().encode(), token.encode())


def operator(authorization: str | None = Header(default=None)) -> str:
    """Operator authentication. Returns the trusted operator identity."""
    if _operator_auth_off():
        return operator_identity()
    if not os.environ.get("OPERATOR_TOKEN"):
        raise HTTPException(status_code=503, detail="Operator authentication is not configured.")
    if not _bearer_ok(authorization):
        raise HTTPException(status_code=401, detail="Operator token required.")
    return operator_identity()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _token_ok(row: m.Conversation, token: str | None) -> bool:
    return bool(row.token_hash and token and hmac.compare_digest(_hash_token(token), row.token_hash))


def _conversation(st: AppState, conv_id: str) -> m.Conversation:
    with st.ctx.sessions() as s:
        row = s.get(m.Conversation, conv_id)
        if row is None:
            raise HTTPException(status_code=404, detail="No such conversation.")
        s.expunge(row)
        return row


def customer(
    conv_id: str,
    st: AppState = Depends(state),
    token: str | None = Header(default=None, alias=TOKEN_HEADER),
) -> str:
    """The conversation's own customer: the secret handed out by `POST /api/conversations`."""
    if not _token_ok(_conversation(st, conv_id), token):
        raise HTTPException(status_code=401, detail="Conversation token required.")
    return conv_id


def reader(
    conv_id: str,
    st: AppState = Depends(state),
    token: str | None = Header(default=None, alias=TOKEN_HEADER),
    authorization: str | None = Header(default=None),
) -> str:
    """The conversation's customer, or an operator (the console reads transcripts)."""
    if _token_ok(_conversation(st, conv_id), token):
        return conv_id
    operator(authorization)
    return conv_id


class NewConversation(BaseModel):
    conversation_id: str
    token: str = Field(description=f"Secret for this conversation; send it as {TOKEN_HEADER}.")


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
    token_hash: str | None = None,
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
        if token_hash is not None:
            row.token_hash = token_hash
        s.commit()


@app.get("/api/health")
def health(st: AppState = Depends(state)) -> dict[str, str]:
    try:
        with st.ctx.sessions() as s:
            s.execute(text("select 1"))
    except Exception:
        raise HTTPException(status_code=503, detail="Database unreachable.") from None
    return {"status": "ok"}


@app.post("/api/conversations")
async def create_conversation(st: AppState = Depends(state)) -> NewConversation:
    conv_id = f"web-{uuid.uuid4().hex[:12]}"
    token = secrets.token_urlsafe(32)
    await run_in_threadpool(_touch, st, conv_id, token_hash=_hash_token(token))
    return NewConversation(conversation_id=conv_id, token=token)


def _sse(event: str, data: dict[str, Any]) -> dict[str, str]:
    return {"event": event, "data": json.dumps(data)}


def _start[T](st: AppState, coro: Coroutine[Any, Any, T]) -> asyncio.Task[T]:
    """Run `coro` as its own task, kept alive by the app state until it finishes."""
    task = asyncio.create_task(coro)
    st.tasks.add(task)
    task.add_done_callback(st.tasks.discard)
    return task


async def _refresh_index(st: AppState, conv_id: str, last: str | None, agent: str | None) -> None:
    """Bring the conversations index in line with the checkpoint. Best effort: never raises."""
    try:
        msgs = await st.runner.messages(conv_id)
        waiting = await st.runner.awaiting_approval(conv_id)
        await run_in_threadpool(
            lambda: _touch(
                st,
                conv_id,
                last=last,
                agent=agent,
                status="awaiting_approval" if waiting else "active",
                count=_visible_count(msgs),
            )
        )
    except Exception:
        log.exception("conversation index update failed", conversation_id=conv_id)


async def resume_if_decided(st: AppState, conv_id: str, *, wait: bool = True) -> TurnResult | None:
    """Resume a conversation paused on a request an operator already decided.

    This is also the recovery path: if a resume failed after the decision was committed (provider or
    database error), the request stays `approved` and the next decision call, transcript read or
    message tries again. Execution is safe to retry: the tool re-checks the request before acting.
    """
    pending = await st.runner.awaiting_approval(conv_id)
    if not pending:
        return None
    req = await run_in_threadpool(approvals.get, st.ctx, pending["approval_request_id"])
    if req is None or req.status == "pending":
        return None
    if not wait and st.runner.lock(conv_id).locked():
        return None  # a turn is already running on this conversation
    try:
        res = await st.runner.resume(conv_id)
    except ValueError:
        return None  # another call resumed it first
    await _refresh_index(st, conv_id, res.reply or None, res.agents[-1] if res.agents else None)
    return res


async def _run_turn(st: AppState, conv_id: str, text_: str) -> tuple[TurnResult, list[dict[str, Any]]]:
    res: TurnResult | None = None
    try:
        try:
            await resume_if_decided(st, conv_id)
        except Exception:
            log.exception("resume before turn failed", conversation_id=conv_id)
        before = len(await st.runner.messages(conv_id))
        res = await st.runner.send(conv_id, text_)
        return res, _new_agent_messages((await st.runner.messages(conv_id))[before:], res.reply)
    finally:
        await _refresh_index(
            st,
            conv_id,
            (res.reply if res else None) or text_,
            res.agents[-1] if res and res.agents else None,
        )


@app.post("/api/conversations/{conv_id}/messages")
async def send_message(
    conv_id: str, body: MessageIn, st: AppState = Depends(state), _: str = Depends(customer)
) -> EventSourceResponse:
    # The turn runs on its own task: if the client goes away the stream is cancelled, but the turn
    # still finishes, writes its checkpoint and updates the index.
    turn = _start(st, _run_turn(st, conv_id, body.text))

    async def events() -> AsyncIterator[dict[str, str]]:
        try:
            res, new_msgs = await asyncio.shield(turn)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("turn failed", conversation_id=conv_id, error=type(e).__name__)
            yield _sse("error", {"message": "The assistant hit a problem; please try again."})
        else:
            for msg in new_msgs:
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
        yield _sse("done", {})

    return EventSourceResponse(events())


def _new_agent_messages(new: list[dict[str, Any]], reply: str) -> list[dict[str, Any]]:
    """The agent messages a turn added. A reply with no transcript entry (the "still with a
    supervisor" holding message) comes from the runtime, not from an agent, so its `agent` is null."""
    out = [
        {"text": x["content"], "agent": x.get("agent")}
        for x in new
        if x.get("role") == "assistant" and x.get("content")
    ]
    return out or ([{"text": reply, "agent": None}] if reply else [])


def _visible_count(msgs: list[dict[str, Any]]) -> int:
    return sum(
        1
        for x in msgs
        if x.get("role") in ("user", "assistant") and x.get("content") and not is_internal_note(x)
    )


@app.get("/api/conversations")
def list_conversations(st: AppState = Depends(state), _: str = Depends(operator)) -> list[dict[str, Any]]:
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
async def get_conversation(
    conv_id: str, st: AppState = Depends(state), _: str = Depends(reader)
) -> dict[str, Any]:
    try:
        await resume_if_decided(st, conv_id, wait=False)
    except Exception:
        log.exception("resume on read failed", conversation_id=conv_id)
    msgs = await st.runner.messages(conv_id)
    out = []
    for x in msgs:
        role, content = x.get("role"), x.get("content") or ""
        if is_internal_note(x):
            continue
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
def list_approvals(
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
        req = await run_in_threadpool(
            approvals.decide, st.ctx, approval_id, body.decision, who, display_name=body.operator
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="No such approval request.") from None
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    reply = ""
    # The decision is committed. Resuming is a separate step on its own task (a disconnect does not
    # cancel it); if it fails, the request stays `approved` and is resumed on the next read of, or
    # message to, the conversation.
    try:
        res = await asyncio.shield(_start(st, resume_if_decided(st, req.conversation_id)))
        reply = res.reply if res else ""
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("resume after decision failed", approval_request_id=approval_id)
    after = await run_in_threadpool(approvals.get, st.ctx, approval_id)
    return {"status": after.status if after else req.status, "reply": reply}


@app.get("/api/audit")
def audit_log(
    limit: int = Query(200, ge=1, le=1000),
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
        # The runner enforces only when both are on (`--defenses none` turns off in_tool_policy).
        "enforce_policy": bool(c["enforce_policy"] and (c.get("defenses") or {}).get("in_tool_policy", True)),
        "trials": c["trials"],
        "created_at": run["created_at"],
        "summary": run["summary"],
    }


def _valid_run_name(name: str) -> bool:
    """Run names are file stems: no separators, no leading dot (hidden files), no ".."."""
    return bool(_RUN_NAME.match(name)) and ".." not in name


@app.get("/api/eval/runs")
def eval_runs() -> list[dict[str, Any]]:
    out = []
    for p in sorted(RUNS_DIR.glob("*.json")):
        if not _valid_run_name(p.stem):
            continue
        out.append(run_summary(p, json.loads(p.read_text())))
    return sorted(out, key=lambda r: r["created_at"], reverse=True)


@app.get("/api/eval/runs/{name}")
def eval_run(name: str) -> dict[str, Any]:
    if not _valid_run_name(name):
        raise HTTPException(status_code=400, detail="Bad run name.")
    p = RUNS_DIR / f"{name}.json"
    if not p.exists():
        raise HTTPException(status_code=404, detail="No such run.")
    data: dict[str, Any] = json.loads(p.read_text())
    return data
