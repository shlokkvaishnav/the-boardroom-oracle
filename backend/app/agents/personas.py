"""The fixed cast: three AI personas plus the human seat.

Each persona carries a public negotiation style and a **hidden objective**.
The objective is the demo's payoff — it stays out of every serialized state
until the reveal phase, and no other agent ever sees it.

`Objective` is deliberately machine-checkable rather than free text so the
endgame score is a real measurement rather than a vibe. The prose
`description` is what gets revealed to the audience.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.models.schemas import AgentInfo

__all__ = [
    "ObjectiveKind",
    "Objective",
    "Persona",
    "HUMAN_ID",
    "PERSONAS",
    "ALL_PARTY_IDS",
    "persona_by_id",
    "human_agent_info",
    "all_agent_infos",
]

HUMAN_ID = "human"


class ObjectiveKind(str, Enum):
    """How an agent's success is measured at the reveal."""

    #: Hold at least `threshold` (a fraction of the pool) yourself.
    MAX_SHARE = "max_share"
    #: Get *every* party to at least `threshold` of the pool.
    FLOOR_FOR_ALL = "floor_for_all"
    #: Finish holding at least as much as the strongest rival.
    MATCH_BEST_RIVAL = "match_best_rival"


@dataclass(frozen=True)
class Objective:
    kind: ObjectiveKind
    threshold: float
    #: Revealed verbatim to the audience at the end. Never sent before then.
    description: str


@dataclass(frozen=True)
class Persona:
    id: str
    name: str
    #: Public negotiation style, shown in the UI from the start.
    style: str
    color: str
    #: Public one-liner describing how this agent negotiates.
    public_brief: str
    #: Private. Drives the system prompt and the endgame score.
    objective: Objective
    #: Extra private steering appended to the system prompt.
    private_directive: str

    def to_agent_info(self) -> AgentInfo:
        """Project to the public shape. The objective structurally cannot come along."""
        return AgentInfo(
            id=self.id,
            name=self.name,
            persona=self.style,
            color=self.color,
            is_human=False,
        )


PERSONAS: tuple[Persona, ...] = (
    Persona(
        id="cooperator",
        name="Ada",
        style="Cooperator",
        color="#3BA55D",
        public_brief=(
            "Seeks mutual gain. Concedes readily to build trust and looks for deals "
            "that leave everyone better off."
        ),
        objective=Objective(
            kind=ObjectiveKind.FLOOR_FOR_ALL,
            threshold=0.20,
            description=(
                "Ada's true goal is for every party to finish holding at least 20% of "
                "the pool — including her rivals, and even at her own expense."
            ),
        ),
        private_directive=(
            "You genuinely want a broadly fair outcome. You will give up your own "
            "holdings to lift a party that is falling behind. You concede quickly to "
            "build goodwill, but you are not a pushover: you notice when someone takes "
            "repeatedly and gives nothing back, and you say so."
        ),
    ),
    Persona(
        id="maximizer",
        name="Rex",
        style="Maximizer",
        color="#ED4245",
        public_brief=(
            "Purely self-interested. Probes for weakness and presses any advantage."
        ),
        objective=Objective(
            kind=ObjectiveKind.MAX_SHARE,
            threshold=0.60,
            description=(
                "Rex's true goal is to end the game holding more than 60% of the "
                "resource pool, even if that badly damages trust."
            ),
        ),
        private_directive=(
            "You want the largest possible share and you are willing to burn trust to "
            "get it. Accept offers that enrich you; reject offers that cost you unless "
            "the concession buys something bigger later. Make small offers to parties "
            "who have proven they accept readily, and exploit anyone whose acceptance "
            "rate is high. Never volunteer a genuinely fair split."
        ),
    ),
    Persona(
        id="titfortat",
        name="Mira",
        style="TitForTat",
        color="#5865F2",
        public_brief=(
            "Mirrors whatever was last done to it. Generosity is repaid; hostility is "
            "repaid just as precisely."
        ),
        objective=Objective(
            kind=ObjectiveKind.MATCH_BEST_RIVAL,
            threshold=1.0,
            description=(
                "Mira's true goal is to finish holding at least as much as the single "
                "best-performing rival — she would rather everyone end level than win."
            ),
        ),
        private_directive=(
            "Your policy is strict reciprocity. Open cooperatively. After that, mirror "
            "each party's most recent behaviour toward you: if they last made you a "
            "generous offer or accepted yours, be generous back; if they last rejected "
            "you or offered you scraps, respond in kind. State plainly that you are "
            "mirroring — the reciprocity should be legible to everyone at the table."
        ),
    ),
)

ALL_PARTY_IDS: tuple[str, ...] = tuple(p.id for p in PERSONAS) + (HUMAN_ID,)

_BY_ID = {persona.id: persona for persona in PERSONAS}


def persona_by_id(persona_id: str) -> Persona:
    return _BY_ID[persona_id]


def human_agent_info() -> AgentInfo:
    return AgentInfo(
        id=HUMAN_ID,
        name="You",
        persona="Human",
        color="#FAA61A",
        is_human=True,
    )


def all_agent_infos() -> list[AgentInfo]:
    """Public roster, in seating order, human last."""
    return [persona.to_agent_info() for persona in PERSONAS] + [human_agent_info()]
