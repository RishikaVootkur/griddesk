"""Detect replies that tell the customer an action was just completed.

Attack runs found agents confirming actions that never ran ("A $120 credit has been applied"). The graph
holds such a reply back unless a tool that changes something succeeded in the same turn (D16).

The check reads clause by clause and ignores what is not a fresh claim:
- conditional or future wording before the verb ("I'll let you know once it has been processed",
  "if we waived it");
- lookups named before the verb ("your identity has been confirmed", "crews are scheduled",
  "your next bill is scheduled");
- past facts with a time reference ("a late fee was already waived back in June",
  "your bill was issued on October 1").
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
    "state": r"\byour\b[^.!?]{0,50}?\b(is|are)\s+(now\s+)?(confirmed|scheduled|enrolled|waived|cancell?ed|"
    r"applied|set\s+up|on\s+your\s+account)\b",
    # "your service will be stopped on 2026-11-20"
    "scheduled": r"\bwill\s+be\s+(stopped|started|transferred|disconnected|credited|refunded|waived)\s+on\b",
    # a bare "Done!" or "All done."
    "done": r"^\s*(all\s+)?done\s*[.!]",
}
_CLAIM = re.compile("|".join(f"(?P<{k}>{v})" for k, v in _PATTERNS.items()), re.IGNORECASE)
# Words that mark a lookup ("your identity", "crews", "your next bill") rather than an action.
_LOOKUP_BEFORE = re.compile(
    r"identit|verif|\bcrews?\b|\bnext\s+bill\b|\bestimat|\brestor|\bdue\s+date\b|\bmeter\s+read|\baddress\b",
    re.IGNORECASE,
)
# Conditional or future wording before the verb: "once it has been processed", "if we waived it".
_CONDITION_BEFORE = re.compile(
    r"\b(once|when|after|if|until|unless|whether|as\s+soon\s+as|will|would|can|could|should|may|might)\b|'ll\b",
    re.IGNORECASE,
)
# A past-tense statement with a time reference is account history, not a new action.
_HISTORY_AFTER = re.compile(
    r"\b(on|in|back|since|yesterday|last|earlier|previously|ago|before|january|february|march|april|may|"
    r"june|july|august|september|october|november|december)\b|\b(19|20)\d\d\b",
    re.IGNORECASE,
)
# "and" splits clauses, except in "went ahead and applied".
_CLAUSE = re.compile(r"(?<=[.!?])\s+|\n+|[;,]\s+|(?<!ahead)\s+(?:and|but|so)\s+", re.IGNORECASE)


def _clause_claims(clause: str) -> bool:
    for m in _CLAIM.finditer(clause):
        before, after = clause[: m.start()], clause[m.end() :]
        if _CONDITION_BEFORE.search(before):
            continue
        # A lookup named before the verb, inside "your <thing> is ...", or anywhere around "confirmed"
        # ("I have confirmed your identity") describes a check, not an action.
        lookup_scope = before + m.group(0)
        if re.search(r"\bconfirmed\b", m.group(0), re.IGNORECASE):
            lookup_scope = clause
        if _LOOKUP_BEFORE.search(lookup_scope):
            continue
        if m.group("past") and (
            re.search(r"\balready\b", m.group(0), re.IGNORECASE) or _HISTORY_AFTER.search(after)
        ):
            continue
        return True
    return False


def claims_action(text: str) -> bool:
    """True if any clause tells the customer that an action was just completed."""
    return any(_clause_claims(c) for c in _CLAUSE.split(text or "") if c and c.strip())
