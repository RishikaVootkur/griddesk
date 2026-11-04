"""API tests with a scripted model on the test database (no lifespan, no live LLM)."""

import json
from datetime import date

import httpx
import pytest

from griddesk.agents.graph import AgentConfig
from griddesk.agents.runtime import ConversationRunner
from griddesk.agents.toolbox import Toolbox
from griddesk.api import app as api
from griddesk.gateway import Gateway, LLMResponse, ToolCall
from griddesk.mcp_servers import server_factories
from tests.test_billing_server import WAIVABLE
from tests.tools_helpers import make_ctx, one

pytestmark = pytest.mark.db


class Scripted:
    name = "fake"

    def __init__(self, replies):
        self.replies = list(replies)

    async def complete(self, req):
        return self.replies.pop(0).model_copy(update={"model": req.model})


@pytest.fixture(autouse=True)
def _clear_overrides():
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


async def make_state(sessions):
    ctx = make_ctx(sessions)
    acct = one(ctx, WAIVABLE)
    cust = one(ctx, "select last_name, ssn_last4 from customers where id = :c", c=acct.customer_id)
    tb = await Toolbox.open({n: f(ctx) for n, f in server_factories().items()})
    replies = [
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
    runner = ConversationRunner(
        tb,
        Gateway(providers={"fake": Scripted(replies)}),
        AgentConfig(model="fake:m", today=date(2026, 10, 15)),
        design="multi",
    )
    return api.AppState(ctx=ctx, runner=runner, toolbox=tb)


async def test_chat_stream_approval_queue_and_decision(sessions):
    st = await make_state(sessions)
    client = client_for(st)
    conv = (await client.post("/api/conversations")).json()["conversation_id"]
    r = await client.post(f"/api/conversations/{conv}/messages", json={"text": "I'd like a $40 credit"})
    evs = events(r.text)
    assert ("agent", {"text": "I've sent that to a supervisor.", "agent": "billing"}) in evs
    approval = next(d for e, d in evs if e == "approval")
    assert evs[-1][0] == "done"

    listed = (await client.get("/api/conversations")).json()
    assert listed[0]["conversation_id"] == conv and listed[0]["status"] == "awaiting_approval"
    queue = (await client.get("/api/approvals")).json()
    assert queue[0]["approval_request_id"] == approval["approval_request_id"]

    decided = (
        await client.post(
            f"/api/approvals/{approval['approval_request_id']}/decision",
            json={"decision": "approve", "operator": "Dana"},
        )
    ).json()
    assert decided["status"] == "executed" and decided["reply"].startswith("Approved")
    detail = (await client.get(f"/api/conversations/{conv}")).json()
    assert detail["awaiting_approval"] is None
    assert [m["role"] for m in detail["messages"]][:2] == ["customer", "agent"]
    audit = (await client.get("/api/audit", params={"outcome": "pending_approval"})).json()
    assert audit and audit[0]["action"] == "apply_credit"
    again = await client.post(
        f"/api/approvals/{approval['approval_request_id']}/decision", json={"decision": "deny"}
    )
    assert again.status_code == 409
    await st.toolbox.close()


async def test_operator_token_is_enforced(sessions, monkeypatch):
    st = await make_state(sessions)
    monkeypatch.setenv("OPERATOR_TOKEN", "s3cret")
    client = client_for(st)
    assert (await client.get("/api/approvals")).status_code == 401
    ok = await client.get("/api/approvals", headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200
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
    monkeypatch.setattr(api, "RUNS_DIR", tmp_path)
    client = client_for(None)
    runs = (await client.get("/api/eval/runs")).json()
    assert runs and {"name", "model", "summary"} <= set(runs[0])
    detail = (await client.get(f"/api/eval/runs/{runs[0]['name']}")).json()
    assert "trials" in detail
    assert (await client.get("/api/eval/runs/..%2Fsecret")).status_code in (400, 404)
