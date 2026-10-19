"""FastAPI entrypoint for the local character-aware TTS service."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from starlette.background import BackgroundTask
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from cache import AudioCache
from character_manager import (
    CharacterConfigError,
    CharacterDisabledError,
    CharacterManager,
    CharacterNotFoundError,
)
from config import settings
from tts_engine import TTSEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

characters = CharacterManager(settings.characters_file, settings.base_dir)
engine = TTSEngine(
    settings.model_id,
    settings.device,
    settings.model_sample_rate,
    settings.output_sample_rate,
    settings.model_cache_dir,
    settings.model_revision,
)
audio_cache = AudioCache(
    settings.cache_dir,
    settings.cache_enabled,
    settings.cache_max_mb * 1024 * 1024,
)
# A single worker is both the inference queue and the GPU concurrency limit.
inference_queue = ThreadPoolExecutor(max_workers=1, thread_name_prefix="omnivoice")


class TTSRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, Field(min_length=1, max_length=settings.max_text_length)]
    character: Annotated[
        str,
        Field(
            min_length=1,
            max_length=128,
            validation_alias=AliasChoices("character", "char"),
        ),
    ]

    @field_validator("text", "character")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("không được chỉ chứa khoảng trắng")
        return value


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    count = characters.load(required_default_id=settings.default_character)
    logger.info("Loaded %d character configurations", count)
    await run_in_threadpool(engine.load)
    try:
        yield
    finally:
        # Finish the active generation, discard work that has not started, then
        # release model/VRAM. Uvicorn stops accepting requests before this point.
        inference_queue.shutdown(wait=True, cancel_futures=True)
        engine.close()


app = FastAPI(
    title="Wandering Sword OmniVoice TTS",
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": "Dữ liệu đầu vào không hợp lệ.", "errors": exc.errors()})


@app.get("/api/health")
def health() -> dict[str, object]:
    return {
        "status": "ok" if engine.loaded else "starting",
        "model_loaded": engine.loaded,
        "device": engine.device,
    }


@app.get("/api/characters")
def list_characters() -> dict[str, object]:
    return {"characters": characters.list_public()}


@app.post("/api/characters/reload")
def reload_characters() -> dict[str, object]:
    try:
        count = characters.load(required_default_id=settings.default_character)
    except CharacterConfigError as exc:
        # The previous valid snapshot remains active.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "ok", "count": count}


def _synthesize_queued(text: str, character: object, key: str) -> tuple[Path, bool]:
    started = time.perf_counter()
    cached = audio_cache.get(key)
    if cached is not None:
        logger.info("TTS cache hit char=%s elapsed=%.3fs", character.char, time.perf_counter() - started)
        return cached, True
    audio = engine.generate(text, character)
    result = audio_cache.write(key, audio, engine.output_sample_rate)
    logger.info("TTS generated char=%s elapsed=%.3fs", character.char, time.perf_counter() - started)
    return result, False


@app.post("/api/tts", response_class=FileResponse)
def synthesize(request: TTSRequest) -> FileResponse:
    logger.info("Received TTS request character=%s text_length=%d", request.character, len(request.text))
    if not engine.loaded:
        raise HTTPException(status_code=503, detail="Model TTS chưa sẵn sàng.")
    try:
        character, used_fallback = characters.resolve(
            request.character,
            default_id=settings.default_character,
        )
    except (CharacterNotFoundError, CharacterDisabledError) as exc:
        # Startup/reload validation normally makes this unreachable. Keep a
        # safe response in case the active default becomes unavailable.
        logger.error("Default character '%s' is unavailable", settings.default_character)
        raise HTTPException(status_code=503, detail="Giọng mặc định không khả dụng.") from exc
    if used_fallback:
        logger.warning(
            "Unknown or disabled character '%s'; using default '%s'",
            request.character,
            character.char,
        )

    if not character.ref_audio.is_file():
        raise HTTPException(status_code=422, detail=f"File audio tham chiếu của '{request.character}' không tồn tại.")

    key = audio_cache.key_for(request.text, character, engine.model_identity, engine.output_sample_rate)
    cached = audio_cache.get(key)
    if cached is not None:
        return FileResponse(cached, media_type="audio/wav", filename=f"{character.char}-{key[:12]}.wav")

    future = inference_queue.submit(_synthesize_queued, request.text, character, key)
    try:
        result, was_cached = future.result(timeout=settings.request_timeout_seconds)
    except FutureTimeoutError as exc:
        future.cancel()
        logger.warning("TTS timeout character=%s timeout=%ss", request.character, settings.request_timeout_seconds)
        raise HTTPException(status_code=503, detail="Tác vụ TTS quá thời gian chờ hoặc hàng đợi đang bận.") from exc
    except Exception as exc:  # Full details go only to the local server log.
        logger.exception("TTS generation failed for character=%s", request.character)
        raise HTTPException(status_code=500, detail="Sinh giọng nói thất bại. Xem log server để biết chi tiết.") from exc
    background = None
    if not settings.cache_enabled and not was_cached:
        background = BackgroundTask(result.unlink, missing_ok=True)
    return FileResponse(
        result,
        media_type="audio/wav",
        filename=f"{character.char}-{key[:12]}.wav",
        background=background,
    )
