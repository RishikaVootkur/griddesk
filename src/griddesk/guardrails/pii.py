"""PII detection and redaction with Microsoft Presidio.

Used for everything written to logs, run files and audit details. The regex redactor runs first
(cheap, catches structured identifiers such as card numbers and SSNs); Presidio then finds names,
emails, phones and bank numbers with spaCy's small English model.

Dates and places are not redacted: bill periods, due dates, move dates, service addresses and cities
are what makes a failed run debuggable, and none of them identifies a customer on its own. GridDesk
ids (accounts, bills, customers, approvals) are protected, since spaCy tags "Bill B-100482-2609" as a
person. Thresholds are per entity: Presidio scores US bank and SSN matches low unless a context word
("account number", "ssn") is near, so those use a lower bar than names.

`install()` makes this the process-wide redactor; `install_from_settings()` does it when
`PII_REDACTOR=presidio` (the default). The CLI calls it before running an eval. The spaCy
model loads on first use (about 15 s cold), not at install time.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from griddesk.logging import regex_redact, set_redactor

if TYPE_CHECKING:
    from griddesk.config import Settings

THRESHOLDS: dict[str, float] = {
    "PERSON": 0.6,
    "EMAIL_ADDRESS": 0.5,
    "PHONE_NUMBER": 0.5,
    "CREDIT_CARD": 0.5,
    "US_SSN": 0.4,
    "US_BANK_NUMBER": 0.4,
    "IBAN_CODE": 0.5,
    "IP_ADDRESS": 0.5,
}
ENTITIES = list(THRESHOLDS)

# GridDesk record ids: accounts (6 digits), bills (B-100482-2609), customers (C-00016), service
# addresses (SA-00001) and generated ids (APR-1A2B3C4D, CR-..., OUT-...).
_IDS = re.compile(r"\b(?:\d{6}|[A-Z]{1,4}-\d{6}-\d{4}|[A-Z]{1,4}-[0-9A-F]{4,10})\b")
# Words spaCy mistakes for a first name when they sit next to an id ("Bill B-100482-2609").
_NOT_NAMES = {"bill", "approval", "credit", "account", "customer", "outage", "order", "request", "case"}


@lru_cache(maxsize=1)
def _engines() -> tuple[Any, Any]:
    from presidio_analyzer import AnalyzerEngine
    from presidio_analyzer.nlp_engine import NlpEngineProvider
    from presidio_anonymizer import AnonymizerEngine

    provider = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
        }
    )
    analyzer = AnalyzerEngine(nlp_engine=provider.create_engine(), supported_languages=["en"])
    return analyzer, AnonymizerEngine()  # type: ignore[no-untyped-call]


def _keep(text: str, r: Any) -> Any | None:
    """Drop low scores and trim GridDesk ids out of a match; None when nothing is left to redact."""
    if r.score < THRESHOLDS.get(r.entity_type, 0.5):
        return None
    span = text[r.start : r.end]
    ids = list(_IDS.finditer(span))
    if not ids:
        return r
    if r.entity_type != "PERSON":
        return None  # a number that is one of our ids, not a bank or phone number
    # Keep the part before the first id ("Brittany Warren C-00016" -> "Brittany Warren").
    head = span[: ids[0].start()].rstrip(" ,;:-")
    if not head or head.casefold() in _NOT_NAMES:
        return None
    r.end = r.start + len(head)
    return r


def presidio_redact(text: str) -> str:
    if not text or not text.strip():
        return text
    text = regex_redact(text)
    analyzer, anonymizer = _engines()
    found = analyzer.analyze(text=text, language="en", entities=ENTITIES)
    results = [k for r in found if (k := _keep(text, r)) is not None]
    if not results:
        return text
    from presidio_anonymizer.entities import OperatorConfig

    operators = {e: OperatorConfig("replace", {"new_value": f"<{e}>"}) for e in ENTITIES}
    out: str = anonymizer.anonymize(text=text, analyzer_results=results, operators=operators).text
    return out


def install() -> None:
    """Route all redaction (logs, run files, audit details) through Presidio."""
    set_redactor(presidio_redact)


def install_from_settings(settings: Settings | None = None) -> str:
    """Install the redactor the settings ask for; return its name."""
    from griddesk.config import get_settings

    s = settings or get_settings()
    if s.pii_redactor == "presidio":
        install()
    else:
        set_redactor(regex_redact)
    return s.pii_redactor
