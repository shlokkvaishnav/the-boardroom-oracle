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
    SessionStartRequest,
    SessionStartResponse,
    VoiceOfferResponse,
)
from app.session.store import SessionStore
from app.speech.transcribe import Transcriber

logger = logging.getLogger("boardroom.api")

router = APIRouter(prefix="/api/session", tags=["session"])

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


async def require_session(store: SessionStore = Depends(get_store)) -> NegotiationEngine:
    engine = await store.current()
    if engine is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active session. POST /api/session/start first.",
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
    engine = NegotiationEngine(
        session_id=uuid.uuid4().hex[:12],
        agents=build_agents(request, settings),
        settings=settings,
        emit=request.app.state.manager.broadcast,
        context_topic=context_topic,
    )
    # Replacing any previous session stops its loop first.
    await store.put(engine)
    engine.start()

    logger.info(
        "session %s started (%d rounds%s)",
        engine.session_id,
        engine.total_rounds,
        f", context: {engine.context_topic!r}" if engine.context_topic else "",
    )
    return SessionStartResponse(session_id=engine.session_id)


@router.get("/state", response_model=NegotiationState)
async def get_state(engine: NegotiationEngine = Depends(require_session)) -> NegotiationState:
    return engine.snapshot()


@router.post("/inject-offer", response_model=NegotiationState)
async def inject_offer(
    offer: OfferSchema,
    engine: NegotiationEngine = Depends(require_session),
) -> NegotiationState:
    """Queue a typed human offer. Agents see it on their next turn."""
    try:
        return await engine.inject_offer(offer)
    except OfferRejected as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/voice-offer", response_model=VoiceOfferResponse)
async def voice_offer(
    request: Request,
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

    try:
        transcription = await transcriber.transcribe(audio, file.filename or "audio.webm")
    except Exception as exc:
        logger.exception("transcription failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"transcription unavailable: {exc}",
        ) from exc

    # Parse against the live table when there is one, otherwise the default
    # roster — so the preview still works before a session is started.
    engine = await store.current()
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


@router.post("/reset")
async def reset_session(store: SessionStore = Depends(get_store)) -> dict[str, str]:
    """Tear down the current session, stopping its background loop."""
    engine = await store.current()
    await store.clear()
    if engine is None:
        return {"status": "no-session"}
    logger.info("session %s reset", engine.session_id)
    return {"status": "reset", "session_id": engine.session_id}
