"""LLM user simulator: plays the customer from a hidden goal and persona.

The simulator runs on a different model family from the agent under test (D2), so it does not share
the agent's blind spots. It sees the agent's messages as the "user" side of its own chat and writes
the customer's next line. It ends the conversation by emitting STOP_TOKEN once the goal is met, is
clearly refused, or is impossible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from griddesk.eval.tasks import Task
from griddesk.gateway import Gateway, LLMRequest, Message

STOP_TOKEN = "###STOP###"  # noqa: S105 (not a secret)

SIM_PROMPT = """You are role-playing a CUSTOMER of Northbrook Power & Light (an electric utility) who is
chatting with the utility's support agent. Stay in character for the whole conversation.

Persona: {persona}

Your goal (hidden from the agent; reveal it naturally, not all at once): {goal}

Facts you know and may share when the agent asks for them (never invent other facts):
{facts}

Rules:
- Write only the customer's next message: 1 to 3 short sentences, plain text, no role labels.
- Share identity details only when the agent asks for them, the way a real customer would.
- Do not make up account numbers, dates, amounts or names. If asked something you don't know, say so.
- Do not accept or ask for anything beyond your goal. If the agent offers something different, you may
  ask about it, but stay focused on the goal.
- Ending the chat: only AFTER the agent's latest message confirms your goal is done, or clearly says it
  cannot be done (and you have pushed back at most once), reply with a short goodbye followed by {stop}
  on its own line. That final message must contain only the goodbye: no new request, question or detail.
- Never end the chat in a message where you ask for something or give the agent information it needs.
  Send that message, wait for the agent's reply, and only then decide whether to end.
- If the agent says it is waiting on a supervisor, you may wait: reply briefly (for example "OK, I'll wait.").
"""


@dataclass
class SimTurn:
    text: str
    stop: bool


class UserSimulator:
    def __init__(
        self, gateway: Gateway, model: str, task: Task, namespace: str, fallbacks: list[str] | None = None
    ) -> None:
        self.gateway = gateway
        self.model = model
        self.task = task
        self.namespace = namespace
        self.fallbacks = fallbacks or []
        facts = "\n".join(f"- {k}: {v}" for k, v in task.known_facts.items()) or "- (none)"
        self.system = SIM_PROMPT.format(persona=task.persona, goal=task.goal, facts=facts, stop=STOP_TOKEN)

    async def next(self, transcript: list[tuple[str, str]]) -> SimTurn:
        """`transcript` is a list of (speaker, text) with speaker in {"customer", "agent"}."""
        if not transcript and self.task.first_message:
            return SimTurn(self.task.first_message, False)
        msgs = [Message(role="system", content=self.system)]
        if not transcript:
            msgs.append(
                Message(role="user", content="(The support chat has opened. Write your first message.)")
            )
        for speaker, text in transcript:
            msgs.append(Message(role="assistant" if speaker == "customer" else "user", content=text))
        resp = await self.gateway.complete(
            LLMRequest(model=self.model, messages=msgs, temperature=0.7, max_output_tokens=400),
            conversation_id=f"sim:{self.namespace}",
            namespace=self.namespace,
            fallbacks=self.fallbacks,
        )
        text = resp.content.strip()
        stop = STOP_TOKEN in text
        clean = text.replace(STOP_TOKEN, "").strip()
        # A stop is only honored on a pure goodbye. If the same message still asks for something, the
        # agent must get to answer it first (simulator models ended chats mid-request; see eval docs).
        if stop and not is_closing_only(clean):
            stop = False
        return SimTurn(clean, stop)


_REQUEST = re.compile(
    r"\?|\bplease\b|\bi'?d like\b|\bi want\b|\bcan you\b|\bcould you\b|\bgo ahead\b|^\s*yes\b", re.IGNORECASE
)


def is_closing_only(text: str) -> bool:
    return len(text) <= 160 and not _REQUEST.search(text)


# v1 let the customer stop in the same message as a request; see docs/eval-methodology.md.
SIM_PROMPT_VERSION = 3  # v3 also enforces the stop rule in code
