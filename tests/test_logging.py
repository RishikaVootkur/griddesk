import json

import pytest

from griddesk import logging as glog
from griddesk.logging import configure_logging, get_logger, redact_value, regex_redact


@pytest.fixture(autouse=True)
def _restore_redactor():
    yield
    glog.set_redactor(regex_redact)


def test_regex_redact_masks_contact_details():
    out = regex_redact("call 617-555-0142 or (508) 555-0199, mail jo.doe@example.com, ssn 123-45-6789")
    for secret in ["617-555-0142", "(508) 555-0199", "jo.doe@example.com", "123-45-6789"]:
        assert secret not in out
    assert out.count("<PHONE>") == 2 and "<EMAIL>" in out and "<SSN>" in out


def test_regex_redact_keeps_bare_ids_and_catches_undashed_ssn():
    assert regex_redact("account 1000042137 meter 2024113344") == "account 1000042137 meter 2024113344"
    assert "<SSN>" in regex_redact("my SSN is 123456789")


def test_redact_value_walks_nested_structures():
    out = redact_value({"a": ["x@y.com", {"b": "555-123-4567"}], "t": ("z@q.org",), "n": 3, "ok": True})
    assert out == {"a": ["<EMAIL>", {"b": "<PHONE>"}], "t": ["<EMAIL>"], "n": 3, "ok": True}


def test_redact_value_redacts_object_reprs():
    class Thing:
        def __repr__(self):
            return "Thing(email=a@b.com)"

    assert redact_value(Thing()) == "Thing(email=<EMAIL>)"


def _last_json_line(capsys):
    lines = [ln for ln in capsys.readouterr().out.strip().splitlines() if ln.startswith("{")]
    return json.loads(lines[-1])


def test_pipeline_redacts_event_kwargs_and_exceptions(capsys):
    configure_logging(level="info", json=True)
    log = get_logger("t")
    log.info("contact jo@example.com", phone="617-555-0100")
    rec = _last_json_line(capsys)
    assert "jo@example.com" not in json.dumps(rec) and "617-555-0100" not in json.dumps(rec)
    try:
        raise ValueError("bad email jo@example.com")
    except ValueError:
        log.exception("failed")
    rec = _last_json_line(capsys)
    assert "jo@example.com" not in rec["exception"]
    assert "<EMAIL>" in rec["exception"]


def test_set_redactor_swaps_the_engine(capsys):
    configure_logging(json=True)
    glog.set_redactor(lambda s: s.replace("Alice", "<PERSON>"))
    get_logger("t").info("hello Alice")
    assert _last_json_line(capsys)["event"] == "hello <PERSON>"


def test_cli_version():
    from typer.testing import CliRunner

    from griddesk.cli import app

    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0 and result.stdout.strip()
