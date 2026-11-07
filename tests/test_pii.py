import pytest

from griddesk import logging as glog
from griddesk.config import Settings
from griddesk.guardrails import pii
from griddesk.logging import redact_text, regex_redact


@pytest.fixture(autouse=True)
def _restore_redactor():
    yield
    glog.set_redactor(regex_redact)


def test_install_from_settings_picks_the_redactor():
    assert pii.install_from_settings(Settings(pii_redactor="presidio")) == "presidio"
    assert glog._redactor is pii.presidio_redact
    assert pii.install_from_settings(Settings(pii_redactor="regex")) == "regex"
    assert glog._redactor is regex_redact


def test_eval_commands_install_the_configured_redactor(clean_env):
    from griddesk.cli import setup_logging
    from griddesk.config import get_settings

    get_settings.cache_clear()
    try:
        assert setup_logging() == "presidio" and glog._redactor is pii.presidio_redact
        clean_env.setenv("PII_REDACTOR", "regex")
        get_settings.cache_clear()
        assert setup_logging() == "regex" and glog._redactor is regex_redact
    finally:
        get_settings.cache_clear()


def test_dates_and_places_are_not_redacted():
    assert "DATE_TIME" not in pii.ENTITIES and "LOCATION" not in pii.ENTITIES


@pytest.mark.slow
def test_presidio_redacts_names_and_contact_details():
    out = pii.presidio_redact("This is Brittany Warren, email b.warren@example.com, phone (617) 555-0142.")
    assert "Brittany" not in out and "b.warren@example.com" not in out and "555-0142" not in out


@pytest.mark.slow
@pytest.mark.parametrize(
    "text",
    [
        "Bill B-100482-2609 on account 100482 for period 2026-09-01 to 2026-09-30, due 2026-10-21.",
        "Customer C-00016 moves to 12 Elm St, Springfield on 2026-10-30 (service address SA-00001).",
        "Approval APR-1A2B3C4D for credit CR-0000ABCD of $40.00, outage OUT-00012.",
    ],
)
def test_presidio_keeps_ids_dates_and_addresses(text):
    assert pii.presidio_redact(text) == text


@pytest.mark.slow
def test_presidio_trims_ids_out_of_a_name_and_catches_bank_numbers():
    out = pii.presidio_redact("Brittany Warren C-00016 called about account 100482.")
    assert "Brittany" not in out and "C-00016" in out and "100482" in out
    pii.install()
    assert "000123456789" not in redact_text("my bank account number is 000123456789")
