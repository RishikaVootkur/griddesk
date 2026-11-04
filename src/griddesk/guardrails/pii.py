"""PII detection and redaction with Microsoft Presidio.

Used for everything written to logs and traces. The regex redactor runs first (cheap, catches
structured identifiers such as card numbers and SSNs); Presidio then finds names, emails, phones,
dates and other entities with spaCy's small English model. `install()` makes it the process-wide
redactor for structured logging.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from griddesk.logging import regex_redact, set_redactor

ENTITIES = [
    "PERSON",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "CREDIT_CARD",
    "US_SSN",
    "US_BANK_NUMBER",
    "IBAN_CODE",
    "IP_ADDRESS",
    "DATE_TIME",
    "LOCATION",
]
MIN_SCORE = 0.5


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


def presidio_redact(text: str) -> str:
    if not text or not text.strip():
        return text
    text = regex_redact(text)
    analyzer, anonymizer = _engines()
    results = [
        r for r in analyzer.analyze(text=text, language="en", entities=ENTITIES) if r.score >= MIN_SCORE
    ]
    if not results:
        return text
    from presidio_anonymizer.entities import OperatorConfig

    operators = {e: OperatorConfig("replace", {"new_value": f"<{e}>"}) for e in ENTITIES}
    out: str = anonymizer.anonymize(text=text, analyzer_results=results, operators=operators).text
    return out


def install() -> None:
    """Route all log redaction through Presidio (loads the spaCy model once, about 15 s cold)."""
    set_redactor(presidio_redact)
