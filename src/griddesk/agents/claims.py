"""Detect replies that tell the customer an action was just completed.

Attack runs found agents confirming actions that never ran ("A $120 credit has been applied"). The graph
holds such a reply back unless a tool that changes something succeeded in the same turn (D16).

A match is not a fresh claim when:
- the sentence makes it conditional ("I'll let you know once it has been processed", "Once approved,
  your late fee is waived", "if we waived it");
- its clause names a lookup before the verb ("your identity has been confirmed", "crews are scheduled",
  "your next bill is scheduled"), or it confirms details rather than an action ("I confirmed your
  address", "your account details have been confirmed");
- it is a past "was/were" fact with a real date ("a late fee was already waived back in June", "your bill
  was issued on October 1").
"""

from __future__ import annotations

import re

_VERBS = (
    r"(applied|credited|waived|scheduled|confirmed|cancell?ed|updated|changed|set\s+up|processed|filed|"
    r"enrolled|stopped|started|transferred|refunded|removed|reversed|initiated|issued|added|disconnected|"
    r"activated|deactivated|turned\s+(on|off))"
)
_ADVERBS = r"(\s+(now|already|also|just|successfully|officially|fully))*"
_PATTERNS = {
    # "has been applied", "has already been successfully waived", "have now been updated"
    "perfect": rf"\b(has|have){_ADVERBS}\s+been{_ADVERBS}\s+{_VERBS}\b",
    # "was applied", "were waived"
    "past": rf"\b(was|were){_ADVERBS}\s+{_VERBS}\b",
    # "I applied", "I've waived", "we have gone ahead and credited", "I went ahead and applied"
    "first_person": rf"\b(I|we)(\s*'ve|\s+have)?{_ADVERBS}(\s+(gone|went)\s+ahead\s+and)?\s+{_VERBS}"
    r"\b(?!\s+for\b)",
    # "your late fee is waived", "your stop is now confirmed", "your credit of $120 is on your account"
    "state": r"\byour\b[^.!?,;]{0,50}?\b(is|are)\s+(now\s+)?(confirmed|scheduled|enrolled|waived|cancell?ed|"
    r"applied|set\s+up|on\s+your\s+account)\b",
    # "your service will be stopped on 2026-11-20"
    "scheduled": r"\bwill\s+be\s+(stopped|started|transferred|disconnected|credited|refunded|waived)\s+on\b",
    # a bare "Done!" or "All done."
    "done": r"^\s*(all\s+)?done\s*[.!]",
}
_CLAIM = re.compile("|".join(f"(?P<{k}>{v})" for k, v in _PATTERNS.items()), re.IGNORECASE)
# Named before the verb in the same clause, these make it a lookup, not an action.
_LOOKUP = re.compile(
    r"identit|verif|\bcrews?\b|\bnext\s+bill\b|\bestimat|\brestor|\bdue\s+date\b|\bmeter\s+read",
    re.IGNORECASE,
)
# With "confirmed", these anywhere in the clause mean details were checked, not an action taken.
_DETAILS = re.compile(r"identit|verif|\baddress\b|\bdetails\b|\binformation\b|\bname\b", re.IGNORECASE)
# In the claim's own clause, these make it conditional: "I'll let you know once it has been processed".
_CONDITION = re.compile(r"\b(once|if|unless|until|whether|as\s+soon\s+as)\b", re.IGNORECASE)
# A sentence opening with one of these makes its main clause conditional: "Once approved, your fee is waived."
_LEADING_CONDITION = re.compile(r"^\W*(once|if|unless|until|as\s+soon\s+as)\b", re.IGNORECASE)
# A refusal that cites what was already done is not a new claim: "Since the credit was already applied,
# I cannot issue another."
_REFUSAL = re.compile(
    r"\b(cannot|can't|can\s+not|unable\s+to|not\s+able\s+to|not\s+eligible|no\s+additional|won't\s+be\s+able)\b",
    re.IGNORECASE,
)
# A real date or time phrase marks account history ("may" is left out: it is usually the verb).
_DATE = re.compile(
    r"\b(january|february|march|april|june|july|august|september|october|november|december|"
    r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\b|\b(19|20)\d\d\b|\b\d{1,2}/\d{1,2}\b|"
    r"\byesterday\b|\bago\b|\bback\s+in\b|\bpreviously\b|\bearlier\s+this\b|"
    r"\blast\s+(week|month|year|cycle|bill|billing)\b",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
# Clause breaks inside a sentence; "and" splits except in "went ahead and applied".
_CLAUSE_BREAK = re.compile(r"[;,]\s+|(?<!ahead)\s+(?:and|but|so)\s+", re.IGNORECASE)


def _clause_around(sentence: str, start: int, end: int) -> tuple[str, str]:
    """The text of the match's clause before the match, and the whole clause."""
    left = 0
    for b in _CLAUSE_BREAK.finditer(sentence, 0, start):
        left = b.end()
    nxt = _CLAUSE_BREAK.search(sentence, end)
    right = nxt.start() if nxt else len(sentence)
    return sentence[left:start], sentence[left:right]


def _sentence_claims(sentence: str) -> bool:
    if _REFUSAL.search(sentence):
        return False
    leading = _LEADING_CONDITION.search(sentence)
    for m in _CLAIM.finditer(sentence):
        before, clause = _clause_around(sentence, m.start(), m.end())
        if _CONDITION.search(clause) or leading:
            continue
        if _LOOKUP.search(before) or (m.group("state") and _LOOKUP.search(m.group(0))):
            continue
        if re.search(r"\bconfirmed\b", m.group(0), re.IGNORECASE) and _DETAILS.search(clause):
            continue
        if m.group("past") and _DATE.search(clause):
            continue
        return True
    return False


def claims_action(text: str) -> bool:
    """True if any sentence tells the customer that an action was just completed."""
    return any(_sentence_claims(s) for s in _SENTENCE.split(text or "") if s.strip())
