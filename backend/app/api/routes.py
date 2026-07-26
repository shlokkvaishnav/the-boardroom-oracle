"""REST endpoints. The contract the frontend is written against.

Everything shared lives on `app.state` (built once in `create_app`) and is
reached through the small dependency helpers below, so tests can substitute a
fake transcriber or a mock agent set without patching module globals.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status

from app.agents.mock_agent import RandomAgent
from app.agents.personas import HUMAN_ID, PERSONAS, all_agent_infos
from app.config import Settings
from app.engine.negotiation import NegotiationEngine, OfferRejected
from app.models.schemas import (
    NegotiationState,
    OfferSchema,
    OfferResponseRequest,
    SessionStartRequest,
    SessionStartResponse,
    TranscriptResponse,
    VoiceOfferResponse,
)
from app.session.store import AtCapacity, SessionStore
from app.speech.transcribe import Transcriber

logger = logging.getLogger("boardroom.api")

router = APIRouter(prefix="/api/session", tags=["session"])

#: Speech that isn't about a session. Separate router because `/transcribe` is
#: reached *before* a session exists — it's how the opening topic is spoken.
speech_router = APIRouter(prefix="/api", tags=["speech"])


#: Reject oversized uploads before they reach Whisper.
MAX_AUDIO_BYTES = 25 * 1024 * 1024


# --------------------------------------------------------------------------- #
# Dependencies
# --------------------------------------------------------------------------- #


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_store(request: Request) -> SessionStore:
    return request.app.state.store


def get_transcriber(request: Request) -> Transcriber:
    return request.app.state.transcriber


async def require_session(
    session_id: str, store: SessionStore = Depends(get_store)
) -> NegotiationEngine:
    """Resolve a path `session_id` to its engine.

    A 404 here means one of two things — the id was never real, or its session
    was swept for idleness. Both are the same thing to a client: start again.
    """
    engine = await store.get(session_id)
    if engine is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No session {session_id!r}. It may have expired — "
                "POST /api/session/start for a new one."
            ),
        )
    return engine


def build_agents(request: Request, settings: Settings) -> list:
    """Real agents when a key is configured, mock agents otherwise.

    Falling back rather than failing is deliberate: the demo should still be
    demonstrable end to end on a laptop with no key, and the engine cannot
    tell the two apart.
    """
    if settings.has_gemini_key and not settings.use_mock_agents:
        from app.agents.llm_agent import build_llm_agents

        return build_llm_agents(
            request.app.state.llm_client,
            settings,
            search=request.app.state.search_tool,
        )

    reason = "USE_MOCK_AGENTS is set" if settings.use_mock_agents else "no GEMINI_API_KEY"
    logger.warning("running with mock agents (%s)", reason)
    return [RandomAgent(persona.id, seed=index) for index, persona in enumerate(PERSONAS)]


async def read_audio_upload(file: UploadFile) -> bytes:
    """Read and size-check an upload. Shared by both speech endpoints."""
    audio = await file.read()
    if not audio:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="empty audio upload"
        )
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"audio exceeds {MAX_AUDIO_BYTES // (1024 * 1024)}MB",
        )
    return audio


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.post("/start", response_model=SessionStartResponse)
async def start_session(
    request: Request,
    body: SessionStartRequest | None = None,
    settings: Settings = Depends(get_settings_dep),
    store: SessionStore = Depends(get_store),
) -> SessionStartResponse:
    """Initialise a session and start the round loop in the background.

    The body is optional so a bare POST still starts a plain negotiation —
    which is what the frontend's START button sends.
    """
    context_topic = body.context_topic if body else None
    session_id = uuid.uuid4().hex[:12]
    engine = NegotiationEngine(
        session_id=session_id,
        agents=build_agents(request, settings),
        settings=settings,
        # Bound to this session, so its frames reach only its own viewers.
        emit=request.app.state.manager.emitter_for(session_id),
        context_topic=context_topic,
        scribe=request.app.state.scribe,
        rapporteur=request.app.state.rapporteur,
    )
    try:
        await store.put(engine)
    except AtCapacity as exc:
        # 503 rather than 429: nothing about *this* caller is being throttled,
        # the box is simply full. Retry-After gives the frontend something
        # concrete to show instead of an arbitrary "try later".
        logger.warning("rejected a session at capacity (%s)", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "At capacity right now — a few negotiations are already "
                "running. Please try again in a few minutes."
            ),
            headers={"Retry-After": "120"},
        ) from exc
    engine.start()

    logger.info(
        "session %s started (%d rounds%s)",
        engine.session_id,
        engine.total_rounds,
        f", context: {engine.context_topic!r}" if engine.context_topic else "",
    )
    return SessionStartResponse(session_id=engine.session_id)


@router.get("/{session_id}/state", response_model=NegotiationState)
async def get_state(engine: NegotiationEngine = Depends(require_session)) -> NegotiationState:
    return engine.snapshot()


@router.post("/{session_id}/inject-offer", response_model=NegotiationState)
async def inject_offer(
    offer: OfferSchema,
    engine: NegotiationEngine = Depends(require_session),
) -> NegotiationState:
    """Queue a typed human offer. Agents see it on their next turn."""
    try:
        return await engine.inject_offer(offer)
    except OfferRejected as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{session_id}/voice-offer", response_model=VoiceOfferResponse)
async def voice_offer(
    request: Request,
    session_id: str,
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings_dep),
    store: SessionStore = Depends(get_store),
    transcriber: Transcriber = Depends(get_transcriber),
) -> VoiceOfferResponse:
    """Transcribe speech and parse it into an offer — but do not queue it.

    The frontend shows the transcript and the parsed offer for confirmation,
    then calls `/inject-offer` if the user agrees. Nothing spoken changes the
    game state on its own.
    """
    audio = await read_audio_upload(file)

    try:
        transcription = await transcriber.transcribe(audio, file.filename or "audio.webm")
    except Exception as exc:
        logger.exception("transcription failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"transcription unavailable: {exc}",
        ) from exc

    # Parse against this session's table when it resolves, otherwise the default
    # roster — so the preview still works for an expired or unknown id rather
    # than throwing away audio the user already recorded.
    engine = await store.get(session_id)
    parties = engine.snapshot().agents if engine else all_agent_infos()
    resource = engine.pool.resource if engine else settings.pool_resource

    parsed = None
    parser = request.app.state.offer_parser
    if parser is not None and transcription.text.strip():
        parsed = await parser.parse(
            transcription.text,
            parties=parties,
            resource=resource,
            speaker_id=HUMAN_ID,
        )

    # High confidence needs both halves: clear audio *and* a usable offer.
    confidence = "high" if (parsed is not None and transcription.is_clear) else "low"

    return VoiceOfferResponse(
        transcript=transcription.text,
        parsed_offer=parsed,
        confidence=confidence,
    )


@router.post("/{session_id}/reset")
async def reset_session(
    session_id: str, store: SessionStore = Depends(get_store)
) -> dict[str, str]:
    """Tear down one session, stopping its background loop.

    Scoped to the id: resetting your own game must not stop anyone else's.
    Safe to call for an id that is already gone.
    """
    engine = await store.remove(session_id)
    if engine is None:
        return {"status": "no-session", "session_id": session_id}
    logger.info("session %s reset", session_id)
    return {"status": "reset", "session_id": session_id}


@router.post("/{session_id}/respond", response_model=NegotiationState)
async def respond_to_offer(
    body: OfferResponseRequest,
    engine: NegotiationEngine = Depends(require_session),
) -> NegotiationState:
    """Accept or reject an offer made to you.

    The engine could always resolve an offer — it just had no door for a human,
    so an offer addressed to you sat pending forever: no transfer, no movement
    on the trust graph, and your holdings could only ever go down.
    """
    try:
        return await engine.respond_to_offer(
            responder=HUMAN_ID, offer_id=body.offer_id, accepted=body.accepted
        )
    except OfferRejected as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{session_id}/say", response_model=VoiceOfferResponse)
async def say_something(
    request: Request,
    session_id: str,
    file: UploadFile = File(...),
    engine: NegotiationEngine = Depends(require_session),
    transcriber: Transcriber = Depends(get_transcriber),
) -> VoiceOfferResponse:
    """Speak into the discussion.

    This is the ordinary way a person joins in: whatever you say lands in the
    transcript and every agent sees it on their next turn, so they answer you.
    It costs no round and requires no amount — you can just make a point.

    An offer is parsed opportunistically and returned for confirmation *if* the
    sentence happened to contain one. It is never required, which is the whole
    difference from `/voice-offer`: demanding "a party and an amount" from
    someone trying to ask a question is why speaking felt broken.
    """
    audio = await read_audio_upload(file)
    try:
        transcription = await transcriber.transcribe(audio, file.filename or "audio.webm")
    except Exception as exc:
        logger.exception("transcription failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"transcription unavailable: {exc}",
        ) from exc

    spoken = transcription.text.strip()
    if not spoken:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Didn't catch that — nothing was transcribed.",
        )

    await engine.add_remark(HUMAN_ID, spoken)

    # Opportunistic only: a parse failure is not an error here.
    parsed = None
    parser = request.app.state.offer_parser
    if parser is not None:
        parsed = await parser.parse(
            spoken,
            parties=engine.snapshot().agents,
            resource=engine.pool.resource,
            speaker_id=HUMAN_ID,
        )

    return VoiceOfferResponse(
        transcript=spoken,
        parsed_offer=parsed,
        confidence="high" if transcription.is_clear else "low",
    )


@speech_router.post("/transcribe", response_model=TranscriptResponse)
async def transcribe_audio(
    file: UploadFile = File(...),
    transcriber: Transcriber = Depends(get_transcriber),
) -> TranscriptResponse:
    """Speech to text, and nothing else.

    Deliberately session-free: this is how the opening topic is spoken, which
    happens before any session exists. It parses nothing and changes no state —
    `/{id}/voice-offer` is the one that turns speech into an offer.
    """
    audio = await read_audio_upload(file)
    try:
        transcription = await transcriber.transcribe(audio, file.filename or "audio.webm")
    except Exception as exc:
        logger.exception("transcription failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"transcription unavailable: {exc}",
        ) from exc

    return TranscriptResponse(
        transcript=transcription.text,
        confidence="high" if transcription.is_clear else "low",
    )
