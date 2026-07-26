"""Private asides, and the asymmetry that makes them worth having.

The audience sees every whisper. The table sees only its own. Every test here
exists to pin one half of that: a leak makes the feature pointless, and hiding
them from the viewer makes it invisible.
"""

from __future__ import annotations

from app.models.agent_io import AgentDecision, ProposedOffer, Whisper
from app.models.messages import WhisperMessage

from tests.test_engine import COOP, MAXI, TIT, build_engine


def whispering(to: str, text: str, aloud: str = "Nothing to add.") -> AgentDecision:
    return AgentDecision(action="pass", thought=aloud, whisper=Whisper(to=to, text=text))


# --------------------------------------------------------------------------- #
# The audience half
# --------------------------------------------------------------------------- #


async def test_an_aside_is_recorded_and_pushed_to_viewers() -> None:
    engine, recorder, _ = build_engine(
        {COOP: [whispering(TIT, "Rex is bluffing about the deficit.")]}, rounds=1
    )

    await engine.run()

    frames = recorder.of_type(WhisperMessage)
    assert len(frames) == 1
    aside = frames[0].payload
    assert (aside.from_, aside.to, aside.round) == (COOP, TIT, 1)
    assert aside.text == "Rex is bluffing about the deficit."
    assert engine.snapshot().whispers == [aside]


async def test_whispering_does_not_use_up_the_turn() -> None:
    """The design point: you whisper *and* move, or nobody would ever whisper."""
    engine, recorder, _ = build_engine(
        {
            COOP: [
                AgentDecision(
                    action="offer",
                    offer=ProposedOffer(to=MAXI, resource="budget", amount=10.0),
                    thought="Ten to you, Rex.",
                    whisper=Whisper(to=TIT, text="I am buying him off, not agreeing."),
                )
            ]
        },
        rounds=1,
    )

    await engine.run()

    assert len(engine.offer_log) == 1, "the offer still happened"
    assert engine.offer_log[0].to == MAXI
    assert len(recorder.of_type(WhisperMessage)) == 1, "and so did the aside"


# --------------------------------------------------------------------------- #
# The privacy half
# --------------------------------------------------------------------------- #


async def test_only_the_recipient_is_ever_shown_it() -> None:
    """Structural privacy: other agents' contexts are built without it."""
    engine, _, agents = build_engine(
        {
            COOP: [whispering(TIT, "Keep this between us.")],
            MAXI: [AgentDecision(action="pass", thought="Quiet round.")],
            TIT: [AgentDecision(action="pass", thought="Also quiet.")],
        },
        rounds=2,
    )

    await engine.run()

    seen_by_recipient = [
        w.text for ctx in agents[TIT].seen_contexts for w in ctx.whispers_to_me
    ]
    assert "Keep this between us." in seen_by_recipient

    for eavesdropper in (MAXI, COOP):
        texts = [
            w.text for ctx in agents[eavesdropper].seen_contexts for w in ctx.whispers_to_me
        ]
        assert "Keep this between us." not in texts


async def test_an_aside_never_enters_the_public_transcript() -> None:
    """`thoughts` feeds every agent's recent_remarks, so a leak there is total."""
    engine, _, _ = build_engine(
        {COOP: [whispering(TIT, "Secret.", aloud="Said out loud.")]}, rounds=1
    )

    await engine.run()

    transcript = [t.text for t in engine.thoughts]
    assert "Said out loud." in transcript
    assert "Secret." not in transcript


async def test_the_closing_cannot_quote_a_whisper_as_a_position() -> None:
    """The fallback closing takes each party's last remark. Not their asides."""
    engine, _, _ = build_engine(
        {COOP: [whispering(TIT, "What I really think.", aloud="What I say publicly.")]},
        rounds=1,
    )

    await engine.run()

    assert engine.closing is not None
    assert engine.closing.positions[COOP] == "What I say publicly."


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #


async def test_whispering_to_yourself_is_dropped() -> None:
    engine, recorder, _ = build_engine({COOP: [whispering(COOP, "Note to self.")]}, rounds=1)

    await engine.run()

    assert recorder.of_type(WhisperMessage) == []
    assert engine.whispers == []


async def test_whispering_to_somebody_who_is_not_there_is_dropped() -> None:
    engine, recorder, _ = build_engine({COOP: [whispering("ghost", "Hello?")]}, rounds=1)

    await engine.run()

    assert recorder.of_type(WhisperMessage) == []


async def test_an_empty_aside_is_not_an_aside() -> None:
    engine, recorder, _ = build_engine({COOP: [whispering(TIT, "   ")]}, rounds=1)

    await engine.run()

    assert recorder.of_type(WhisperMessage) == []


async def test_the_human_can_be_whispered_to() -> None:
    """The human holds a seat, so agents can take them into their confidence."""
    from app.agents.personas import HUMAN_ID

    engine, recorder, _ = build_engine(
        {COOP: [whispering(HUMAN_ID, "Back me on this and I will owe you.")]}, rounds=1
    )

    await engine.run()

    assert [f.payload.to for f in recorder.of_type(WhisperMessage)] == [HUMAN_ID]


async def test_a_recipient_is_reminded_of_only_their_recent_asides() -> None:
    from app.engine.negotiation import RECENT_WHISPER_WINDOW

    engine, _, agents = build_engine(
        {
            COOP: [whispering(TIT, f"Aside {i}.") for i in range(8)],
            MAXI: [AgentDecision(action="pass", thought="…")] * 8,
        },
        rounds=8,
    )

    await engine.run()

    final = agents[TIT].seen_contexts[-1].whispers_to_me
    assert len(final) <= RECENT_WHISPER_WINDOW
    assert all(w.to == TIT for w in final)
