"""Endgame scoring: how much of its hidden objective did each agent achieve?

Each score is a fraction in [0, 1], computed from final holdings alone. Like
the trust rule, this is written to be narrated: one small function per
objective kind, each stating what it measures in a sentence.
"""

from __future__ import annotations

from app.agents.personas import PERSONAS, Objective, ObjectiveKind, Persona

__all__ = ["shares", "score_objective", "score_all", "revealed_objectives"]


def shares(holdings: dict[str, float], pool_total: float) -> dict[str, float]:
    """Convert absolute holdings into fractions of the pool."""
    if pool_total <= 0:
        return {party: 0.0 for party in holdings}
    return {party: amount / pool_total for party, amount in holdings.items()}


def _score_max_share(agent_id: str, share_by_party: dict[str, float], target: float) -> float:
    """How close the agent got to hoarding `target` of the pool."""
    if target <= 0:
        return 1.0
    return min(1.0, share_by_party.get(agent_id, 0.0) / target)


def _score_floor_for_all(share_by_party: dict[str, float], floor: float) -> float:
    """What fraction of parties cleared the floor the agent was trying to hold up."""
    if not share_by_party:
        return 0.0
    cleared = sum(1 for share in share_by_party.values() if share >= floor)
    return cleared / len(share_by_party)


def _score_match_best_rival(agent_id: str, share_by_party: dict[str, float]) -> float:
    """How the agent's share compares with the strongest rival's."""
    rivals = [share for party, share in share_by_party.items() if party != agent_id]
    best_rival = max(rivals, default=0.0)
    if best_rival <= 0:
        # Nobody else holds anything, so the agent is trivially level or ahead.
        return 1.0
    return min(1.0, share_by_party.get(agent_id, 0.0) / best_rival)


def score_objective(
    agent_id: str,
    objective: Objective,
    holdings: dict[str, float],
    pool_total: float,
) -> float:
    """Fraction of `objective` achieved, in [0, 1]."""
    share_by_party = shares(holdings, pool_total)

    if objective.kind is ObjectiveKind.MAX_SHARE:
        score = _score_max_share(agent_id, share_by_party, objective.threshold)
    elif objective.kind is ObjectiveKind.FLOOR_FOR_ALL:
        score = _score_floor_for_all(share_by_party, objective.threshold)
    elif objective.kind is ObjectiveKind.MATCH_BEST_RIVAL:
        score = _score_match_best_rival(agent_id, share_by_party)
    else:  # pragma: no cover - exhaustive over the enum
        raise ValueError(f"unscoreable objective kind: {objective.kind}")

    return round(max(0.0, min(1.0, score)), 4)


def score_all(
    holdings: dict[str, float],
    pool_total: float,
    personas: tuple[Persona, ...] = PERSONAS,
) -> dict[str, float]:
    """Score every AI agent. The human has no hidden objective, so isn't scored."""
    return {
        persona.id: score_objective(persona.id, persona.objective, holdings, pool_total)
        for persona in personas
    }


def revealed_objectives(personas: tuple[Persona, ...] = PERSONAS) -> dict[str, str]:
    """The reveal payload: agent id -> the prose description of its hidden goal."""
    return {persona.id: persona.objective.description for persona in personas}
