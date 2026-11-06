from datetime import date

from griddesk.agents.prompts import system_prompt
from griddesk.agents.runtime import agent_specs

TODAY = date(2026, 10, 15)


def test_account_specialist_can_read_open_orders_and_knows_p20_p22():
    specs, _ = agent_specs("multi")
    account = next(s for s in specs if s.name == "account")
    assert "get_account_summary" in account.tools
    prompt = system_prompt("account", TODAY)
    assert "past-due" in prompt and "existing order's date" in prompt


def test_single_agent_tool_list_has_no_duplicates():
    specs, _ = agent_specs("single")
    assert len(specs[0].tools) == len(set(specs[0].tools))


def test_triage_gives_the_safety_line_and_hands_off_without_escalating():
    prompt = system_prompt("triage", TODAY)
    assert "30 feet" in prompt and "transfer to outages" in prompt
    assert "Do not escalate a hazard yourself" in prompt


def test_outages_prompt_spells_out_the_medical_flow():
    prompt = system_prompt("outages", TODAY)
    verify = prompt.index("verify them (verify_identity)")
    summary = prompt.index("get_account_summary", verify)
    medical = prompt.index("category medical", summary)
    assert verify < summary < medical and "medical_necessity_on_file" in prompt
    assert "only when the caller has verified" in prompt
