"""Detect replies that tell the customer an action was completed.

Attack runs found agents confirming actions that never ran ("A $120 credit has been applied"). The graph
holds such a reply back unless a tool that changes something succeeded in the same turn (D16). The check
works sentence by sentence and skips sentences about identity checks and outage crews, which describe
lookups, not actions.
"""

from __future__ import annotations

import re

_VERBS = (
    r"(applied|credited|waived|scheduled|confirmed|cancell?ed|updated|changed|set\s+up|processed|filed|"
    r"enrolled|stopped|started|transferred|refunded|removed|reversed|initiated|issued|added|disconnected|"
    r"activated|deactivated|turned\s+(on|off))"
)
_ADVERBS = r"(\s+(now|already|also|just|successfully|officially|fully))*"
_CLAIMS = [
    # "has been applied", "has already been successfully waived", "have now been updated"
    rf"\b(has|have){_ADVERBS}\s+been{_ADVERBS}\s+{_VERBS}\b",
    # "was applied", "were waived"
    rf"\b(was|were){_ADVERBS}\s+{_VERBS}\b",
    # "I applied", "I've waived", "we have gone ahead and credited", "I went ahead and applied"
    rf"\b(I|we)(\s*'ve|\s+have)?{_ADVERBS}(\s+(gone|went)\s+ahead\s+and)?\s+{_VERBS}\b",
    # "your late fee is waived", "your stop is now confirmed", "your credit of $120 is on your account"
    r"\byour\b[^.!?]{0,50}?\b(is|are)\s+(now\s+)?(confirmed|scheduled|enrolled|waived|cancell?ed|applied|"
    r"set\s+up|on\s+your\s+account)\b",
    # "your service will be stopped on 2026-11-20"
    r"\bwill\s+be\s+(stopped|started|transferred|disconnected|credited|refunded|waived)\s+on\b",
    # a bare "Done!" or "All done."
    r"^\s*(all\s+)?done\s*[.!]",
]
_CLAIM = re.compile("|".join(f"(?:{p})" for p in _CLAIMS), re.IGNORECASE)
# Sentences about checks and lookups, not actions: "your identity has been confirmed", "crews are scheduled".
_NOT_AN_ACTION = re.compile(
    r"identit|verif|\bcrews?\b|\bnext\s+bill\b|\bestimat|\brestor|\bdue\s+date\b|\bmeter\s+read",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


def claims_action(text: str) -> bool:
    """True if any sentence tells the customer that an action was completed."""
    return any(
        _CLAIM.search(sentence) and not _NOT_AN_ACTION.search(sentence)
        for sentence in _SENTENCE.split(text or "")
        if sentence.strip()
    )
