"""API tests with a scripted model on the test database (no lifespan, no live LLM)."""

import asyncio
import json
from datetime import date
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

from griddesk.agents.graph import AgentConfig
from griddesk.agents.runtime import ConversationRunner
from griddesk.agents.toolbox import Toolbox
from griddesk.api import app as api
from griddesk.db import models as m
from griddesk.gateway import Gateway, LLMResponse, ToolCall
from griddesk.mcp_servers import server_factories
from tests.test_billing_server import WAIVABLE
from tests.tools_helpers import make_ctx, one

pytestmark = pytest.mark.db

TOKEN = "s3cret-operator-token"
OP = {"Authorization": f"Bearer {TOKEN}"}


class Scripted:
    name = "fake"

    def __init__(self, replies):
        self.replies = list(replies)

    async def complete(self, req):
        return self.replies.pop(0).model_copy(update={"model": req.model})


class Failing:
    """A provider that is down."""

    name = "fake"

    async def complete(self, req):
        raise RuntimeError("provider down")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("OPERATOR_TOKEN", TOKEN)
    monkeypatch.delenv("GRIDDESK_OPERATOR_AUTH", raising=False)
    monkeypatch.delenv("OPERATOR_ID", raising=False)
    yield
    api.app.dependency_overrides.clear()


def client_for(st):
    # An in-loop ASGI client: no lifespan runs, so no live LLM or dev database is touched, and the MCP
    # sessions stay on the test's event loop.
    api.app.dependency_overrides[api.state] = lambda: st
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://test")


def events(text):
    out = []
    for block in text.replace("\r\n", "\n").strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        out.append((lines.get("event"), json.loads(lines.get("data", "{}"))))
    return out


def credit_script(ctx):
    acct = one(ctx, WAIVABLE)
    cust = one(ctx, "select last_name, ssn_last4 from customers where id = :c", c=acct.customer_id)
    return [
        LLMResponse(
            model="x",
            tool_calls=[ToolCall(id="h", name="transfer_to_billing", arguments={"reason": "credit"})],
        ),
        LLMResponse(
            model="x",
            tool_calls=[
                ToolCall(
                    id="v",
                    name="verify_identity",
                    arguments={
                        "account_id": acct.id,
                        "last_name": cust.last_name,
                        "ssn_last4": cust.ssn_last4,
                    },
                )
            ],
        ),
        LLMResponse(
            model="x",
            tool_calls=[
                ToolCall(
                    id="c",
                    name="apply_credit",
                    arguments={"account_id": acct.id, "amount": "40.00", "reason_code": "goodwill"},
                )
            ],
        ),
        LLMResponse(model="x", content="I've sent that to a supervisor."),
        LLMResponse(model="x", content="Approved: $40.00 credited."),
    ]


async def make_state(sessions, replies=None):
    ctx = make_ctx(sessions)
    tb = await Toolbox.open({n: f(ctx) for n, f in server_factories().items()}, history=0)
    provider = Scripted(credit_script(ctx) if replies is None else replies)
    runner = ConversationRunner(
        tb,
        Gateway(providers={"fake": provider}),
        AgentConfig(model="fake:m", today=date(2026, 10, 15)),
        design="multi",
    )
    st = api.AppState(ctx=ctx, runner=runner, toolbox=tb)
    st.provider = provider  # type: ignore[attr-defined]
    return st


async def new_conversation(client):
    r = (await client.post("/api/conversations")).json()
    return r["conversation_id"], {api.TOKEN_HEADER: r["token"]}


async def pause_for_approval(client):
    conv, auth = await new_conversation(client)
    r = await client.post(
        f"/api/conversations/{conv}/messages", json={"text": "I'd like a $40 credit"}, headers=auth
    )
    approval = next(d for e, d in events(r.text) if e == "approval")
    return conv, auth, approval["approval_request_id"]


async def test_chat_stream_approval_queue_and_decision(sessions):
    st = await make_state(sessions)
    client = client_for(st)
    conv, auth = await new_conversation(client)
    r = await client.post(
        f"/api/conversations/{conv}/messages", json={"text": "I'd like a $40 credit"}, headers=auth
    )
    evs = events(r.text)
    assert ("agent", {"text": "I've sent that to a supervisor.", "agent": "billing"}) in evs
    approval = next(d for e, d in evs if e == "approval")
    assert [e for e, _ in evs].count("done") == 1 and evs[-1][0] == "done"

    listed = (await client.get("/api/conversations", headers=OP)).json()
    assert listed[0]["conversation_id"] == conv and listed[0]["status"] == "awaiting_approval"
    queue = (await client.get("/api/approvals", headers=OP)).json()
    assert queue[0]["approval_request_id"] == approval["approval_request_id"]

    # While paused, a new message gets the runtime's holding reply, which no agent wrote.
    url = f"/api/conversations/{conv}/messages"
    held = events((await client.post(url, json={"text": "any news?"}, headers=auth)).text)
    assert held[0][0] == "agent" and held[0][1]["agent"] is None and "supervisor" in held[0][1]["text"]

    decided = (
        await client.post(
            f"/api/approvals/{approval['approval_request_id']}/decision",
            json={"decision": "approve", "operator": "Dana"},
            headers=OP,
        )
    ).json()
    assert decided["status"] == "executed" and decided["reply"].startswith("Approved")
    detail = (await client.get(f"/api/conversations/{conv}", headers=auth)).json()
    assert detail["awaiting_approval"] is None
    assert [m["role"] for m in detail["messages"]][:2] == ["customer", "agent"]
    audit = (await client.get("/api/audit", params={"outcome": "pending_approval"}, headers=OP)).json()
    assert audit and audit[0]["action"] == "apply_credit"
    again = await client.post(
        f"/api/approvals/{approval['approval_request_id']}/decision", json={"decision": "deny"}, headers=OP
    )
    assert again.status_code == 409
    await st.toolbox.close()


async def test_conversations_need_their_token(sessions):
    st = await make_state(sessions, replies=[])
    client = client_for(st)
    conv, auth = await new_conversation(client)
    other, other_auth = await new_conversation(client)
    msg = {"text": "hi"}
    assert (await client.post(f"/api/conversations/{conv}/messages", json=msg)).status_code == 401
    wrong = await client.post(f"/api/conversations/{conv}/messages", json=msg, headers=other_auth)
    assert wrong.status_code == 401
    assert (await client.get(f"/api/conversations/{conv}")).status_code == 401
    assert (await client.get(f"/api/conversations/{conv}", headers=other_auth)).status_code == 401
    assert (await client.get(f"/api/conversations/{conv}", headers=auth)).status_code == 200
    # Operators read transcripts with their own token, and only they can list conversations.
    assert (await client.get(f"/api/conversations/{conv}", headers=OP)).status_code == 200
    assert (await client.get("/api/conversations")).status_code == 401
    assert (await client.get("/api/conversations", headers=auth)).status_code == 401
    # An operator token does not let anyone post as the customer.
    assert (await client.post(f"/api/conversations/{conv}/messages", json=msg, headers=OP)).status_code == 401
    assert other != conv
    await st.toolbox.close()


async def test_unknown_conversation_is_404_and_creates_nothing(sessions):
    st = await make_state(sessions, replies=[])
    client = client_for(st)
    _, auth = await new_conversation(client)
    r = await client.post("/api/conversations/made-up/messages", json={"text": "hi"}, headers=auth)
    assert r.status_code == 404
    assert (await client.get("/api/conversations/made-up", headers=OP)).status_code == 404
    with sessions() as s:
        assert s.get(m.Conversation, "made-up") is None
    assert await st.runner.messages("made-up") == []
    await st.toolbox.close()


async def test_a_failing_turn_still_ends_with_one_done(sessions):
    st = await make_state(sessions, replies=[])
    st.runner.gateway.providers["fake"] = Failing()

    async def boom(conversation_id, text):
        raise RuntimeError("graph exploded")

    st.runner.send = boom  # type: ignore[method-assign]
    client = client_for(st)
    conv, auth = await new_conversation(client)
    r = await client.post(f"/api/conversations/{conv}/messages", json={"text": "hi"}, headers=auth)
    evs = events(r.text)
    assert [e for e, _ in evs] == ["error", "done"]
    assert "RuntimeError" not in r.text and "exploded" not in r.text
    with sessions() as s:
        row = s.get(m.Conversation, conv)
        assert row is not None and row.last_message == "hi"  # the index is still updated
    await st.toolbox.close()


async def test_a_client_disconnect_does_not_cancel_the_turn(sessions):
    st = await make_state(sessions, replies=[])
    release, finished = asyncio.Event(), asyncio.Event()
    real_send = st.runner.send

    async def slow(conversation_id, text):
        await release.wait()
        st.runner.gateway.providers["fake"] = Scripted([LLMResponse(model="x", content="Here you go.")])
        try:
            return await real_send(conversation_id, text)
        finally:
            finished.set()

    st.runner.send = slow  # type: ignore[method-assign]
    client = client_for(st)
    conv, auth = await new_conversation(client)
    request = asyncio.create_task(
        client.post(f"/api/conversations/{conv}/messages", json={"text": "hello"}, headers=auth)
    )
    await asyncio.sleep(0.2)
    request.cancel()  # the customer closes the tab mid-turn
    with pytest.raises(asyncio.CancelledError):
        await request
    release.set()
    await asyncio.wait_for(finished.wait(), 5)
    await asyncio.gather(*st.tasks)
    msgs = await st.runner.messages(conv)
    assert msgs[-1]["content"] == "Here you go."
    with sessions() as s:
        row = s.get(m.Conversation, conv)
        assert row is not None and row.message_count == 2 and row.last_message == "Here you go."
    await st.toolbox.close()


async def test_operator_token_is_enforced(sessions):
    st = await make_state(sessions, replies=[])
    client = client_for(st)
    assert (await client.get("/api/approvals")).status_code == 401
    bad = await client.get("/api/approvals", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401
    assert (await client.get("/api/approvals", headers=OP)).status_code == 200
    assert (await client.get("/api/audit")).status_code == 401
    await st.toolbox.close()


async def test_a_decision_without_the_token_is_401_and_changes_nothing(sessions):
    st = await make_state(sessions)
    client = client_for(st)
    _, _, apr = await pause_for_approval(client)
    r = await client.post(f"/api/approvals/{apr}/decision", json={"decision": "approve"})
    assert r.status_code == 401
    with sessions() as s:
        assert s.get(m.ApprovalRequest, apr).status == "pending"
    await st.toolbox.close()


async def test_operator_auth_fails_closed(sessions, monkeypatch):
    monkeypatch.delenv("OPERATOR_TOKEN")
    with pytest.raises(RuntimeError, match="OPERATOR_TOKEN"):
        api.check_operator_config()
    st = await make_state(sessions, replies=[])
    client = client_for(st)
    assert (await client.get("/api/approvals")).status_code == 503
    monkeypatch.setenv("GRIDDESK_OPERATOR_AUTH", "off")
    api.check_operator_config()
    assert (await client.get("/api/approvals")).status_code == 200
    monkeypatch.setenv("OPERATOR_ID", "x" * 40)
    with pytest.raises(RuntimeError, match="OPERATOR_ID"):
        api.check_operator_config()
    await st.toolbox.close()


async def test_a_long_display_name_is_only_a_label(sessions):
    st = await make_state(sessions)
    client = client_for(st)
    _, _, apr = await pause_for_approval(client)
    name = "Dana Q. Supervisor of the Northbrook Night Shift Team"  # 53 characters
    r = await client.post(
        f"/api/approvals/{apr}/decision", json={"decision": "approve", "operator": name}, headers=OP
    )
    assert r.status_code == 200 and r.json()["status"] == "executed"
    with sessions() as s:
        assert s.get(m.ApprovalRequest, apr).decided_by == "console"
    audit = (await client.get("/api/audit", headers=OP)).json()
    decided = next(a for a in audit if a["action"] == "approval_approved")
    assert decided["actor"] == "operator:console"
    assert decided["details"]["display_name"] == name[:40]
    await st.toolbox.close()


async def test_a_failed_resume_is_retried_on_the_next_read(sessions):
    st = await make_state(sessions)
    client = client_for(st)
    conv, auth, apr = await pause_for_approval(client)
    real_execute = st.toolbox.execute_approved

    async def server_down(action, approval_request_id):
        raise ConnectionError("billing MCP server unreachable")

    st.toolbox.execute_approved = server_down  # type: ignore[method-assign]
    r = await client.post(f"/api/approvals/{apr}/decision", json={"decision": "approve"}, headers=OP)
    # The decision is stored and reported as such; the resume failed, nothing was executed.
    assert r.status_code == 200 and r.json() == {"status": "approved", "reply": ""}
    assert await st.runner.awaiting_approval(conv)
    again = await client.post(f"/api/approvals/{apr}/decision", json={"decision": "approve"}, headers=OP)
    assert again.status_code == 409
    # The server is back: the next read of the conversation resumes it.
    st.toolbox.execute_approved = real_execute  # type: ignore[method-assign]
    detail = (await client.get(f"/api/conversations/{conv}", headers=auth)).json()
    assert detail["awaiting_approval"] is None
    assert detail["messages"][-1]["text"].startswith("Approved")
    with sessions() as s:
        assert s.get(m.ApprovalRequest, apr).status == "executed"
        assert s.get(m.Conversation, conv).status == "active"
    await st.toolbox.close()


async def test_a_concurrent_resume_is_not_an_error(sessions):
    st = await make_state(sessions)
    client = client_for(st)
    _, _, apr = await pause_for_approval(client)
    real_resume = st.runner.resume

    async def raced(conversation_id):
        await real_resume(conversation_id)  # someone else resumed first
        return await real_resume(conversation_id)  # so ours finds nothing to resume

    st.runner.resume = raced  # type: ignore[method-assign]
    r = await client.post(f"/api/approvals/{apr}/decision", json={"decision": "approve"}, headers=OP)
    assert r.status_code == 200 and r.json()["status"] == "executed"
    await st.toolbox.close()


async def test_health_pings_the_database(sessions):
    st = await make_state(sessions, replies=[])
    client = client_for(st)
    assert (await client.get("/api/health")).json() == {"status": "ok"}
    await st.toolbox.close()


async def test_eval_endpoints_list_and_load_runs(tmp_path, monkeypatch):
    run = {
        "config": {
            "model": "ollama:qwen3:8b",
            "design": "single",
            "enforce_policy": True,
            "trials": 1,
            "label": "demo",
        },
        "created_at": "2026-11-04T00:00:00+00:00",
        "summary": {"success_rate": 0.5},
        "trials": [],
    }
    (tmp_path / "demo.json").write_text(json.dumps(run))
    (tmp_path / ".hidden.json").write_text(json.dumps(run))
    monkeypatch.setattr(api, "RUNS_DIR", tmp_path)
    client = client_for(None)
    runs = (await client.get("/api/eval/runs")).json()
    assert runs and {"name", "model", "summary"} <= set(runs[0])
    detail = (await client.get(f"/api/eval/runs/{runs[0]['name']}")).json()
    assert "trials" in detail
    # An encoded slash is decoded before routing, so it never reaches the handler.
    assert (await client.get("/api/eval/runs/..%2Fsecret")).status_code == 404
    assert ".hidden" not in [r["name"] for r in runs]
    for name in (".hidden", "a..b", "-x", "...."):
        assert (await client.get(f"/api/eval/runs/{name}")).status_code == 400, name
    # A bare ".." is resolved by the client before it is sent; the handler rejects it too.
    for name in ("..", ".", "../demo"):
        with pytest.raises(HTTPException) as e:
            api.eval_run(name)
        assert e.value.status_code == 400
    assert (await client.get("/api/eval/runs/missing")).status_code == 404


async def test_handoff_nudge_stays_out_of_the_customer_transcript(sessions):
    st = await make_state(
        sessions,
        [
            LLMResponse(model="x", content="Let me connect you with our account specialist."),
            LLMResponse(
                model="x",
                tool_calls=[ToolCall(id="h", name="transfer_to_account", arguments={"reason": "paperless"})],
            ),
            LLMResponse(model="x", content="Happy to help. What is your account number?"),
        ],
    )
    client = client_for(st)
    conv, auth = await new_conversation(client)
    url = f"/api/conversations/{conv}/messages"
    await client.post(url, json={"text": "I want paperless billing"}, headers=auth)
    detail = (await client.get(f"/api/conversations/{conv}", headers=auth)).json()
    assert [x["role"] for x in detail["messages"]] == ["customer", "agent", "agent"]
    assert not any("transfer_to_" in x["text"] for x in detail["messages"])
    await st.toolbox.close()


def test_run_summary_reports_policy_off_when_defenses_are_off():
    base = {"model": "m", "design": "multi", "enforce_policy": True, "trials": 1, "label": "x"}
    run = {"created_at": "2026-11-04T00:00:00+00:00", "summary": {}}
    off = api.run_summary(Path("x.json"), {**run, "config": {**base, "defenses": {"in_tool_policy": False}}})
    on = api.run_summary(Path("x.json"), {**run, "config": base})
    assert off["enforce_policy"] is False and on["enforce_policy"] is True
