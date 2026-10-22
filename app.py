"""FastAPI entrypoint for the local character-aware TTS service."""

from __future__ import annotations

import logging
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
import tempfile
import threading
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.background import BackgroundTask
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator
import soundfile as sf

from cache import AudioCache
from character_manager import (
    Character,
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
voice_design_preview_dir = settings.output_dir / "voice_design_previews"
voice_design_previews: dict[str, "VoiceDesignPreview"] = {}
voice_design_preview_lock = threading.RLock()
character_create_preview_dir = settings.output_dir / "character_create_previews"
character_create_previews: dict[str, "CharacterCreatePreview"] = {}
MAX_REFERENCE_AUDIO_BYTES = 50 * 1024 * 1024
DEFAULT_CHARACTER_PREVIEW_TEXT = "Thiên địa hữu tình, giang hồ hữu mộng. Mong quân bảo trọng trên đường phía trước."


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


class VoiceDesignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, Field(min_length=1, max_length=settings.max_text_length)]
    instruct: Annotated[str, Field(min_length=1, max_length=1000)]
    language: Annotated[str, Field(min_length=1, max_length=64)] = "Vietnamese"

    @field_validator("text", "instruct", "language")
    @classmethod
    def strip_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("không được để trống")
        return value


class VoiceDesignSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_id: UUID
    primary_alias: Annotated[str, Field(min_length=1, max_length=128)]
    aliases: Annotated[list[str], Field(max_length=256)] = Field(default_factory=list)
    name: Annotated[str, Field(min_length=1, max_length=160)]

    @field_validator("primary_alias", "name")
    @classmethod
    def strip_nonempty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("không được để trống")
        return value

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("alias không được rỗng")
        if len(set(normalized)) != len(normalized):
            raise ValueError("danh sách alias bị trùng")
        return normalized


class AliasMoveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aliases: Annotated[list[str], Field(min_length=1, max_length=1000)]
    source_char: Annotated[str, Field(min_length=1, max_length=128)]
    target_char: Annotated[str, Field(min_length=1, max_length=128)]

    @field_validator("source_char", "target_char")
    @classmethod
    def strip_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("khÃ´ng Ä‘Æ°á»£c Ä‘á»ƒ trá»‘ng")
        return value

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("alias khÃ´ng Ä‘Æ°á»£c rá»—ng")
        if len(set(normalized)) != len(normalized):
            raise ValueError("danh sÃ¡ch alias bá»‹ trÃ¹ng")
        return normalized


class CharacterCreateSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_id: UUID
    name: Annotated[str, Field(min_length=1, max_length=160)]
    aliases: Annotated[list[str], Field(max_length=1000)] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def strip_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("không được để trống")
        return value

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("alias không được rỗng")
        if len(set(normalized)) != len(normalized):
            raise ValueError("danh sách alias bị trùng")
        return normalized


@dataclass(frozen=True, slots=True)
class VoiceDesignPreview:
    path: Path
    text: str
    instruct: str
    language: str
    created_at: float


@dataclass(frozen=True, slots=True)
class CharacterCreatePreview:
    char_id: str
    reference_path: Path
    preview_path: Path
    ref_text: str
    preview_text: str
    speed: float
    created_at: float


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    voice_design_preview_dir.mkdir(parents=True, exist_ok=True)
    character_create_preview_dir.mkdir(parents=True, exist_ok=True)
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


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def test_ui() -> HTMLResponse:
    ui_path = settings.base_dir / "ui" / "index.html"
    try:
        html = ui_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.exception("Could not read local TTS test UI")
        raise HTTPException(status_code=500, detail="Không đọc được giao diện test.") from exc
    return HTMLResponse(html)


@app.exception_handler(RequestValidationError)
async def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": "Dữ liệu đầu vào không hợp lệ.", "errors": exc.errors()})


@app.get("/api/health")
def health() -> dict[str, object]:
    return {
        "status": "ok" if engine.loaded else "starting",
        "model_loaded": engine.loaded,
        "device": engine.device,
        "cache_enabled": audio_cache.enabled,
    }


@app.get("/api/characters")
def list_characters() -> dict[str, object]:
    return {"characters": characters.list_public()}


@app.get("/api/characters/manage")
def list_characters_for_management() -> dict[str, object]:
    return {"characters": characters.list_management()}


@app.post("/api/characters/aliases/move")
def move_character_alias(request: AliasMoveRequest) -> dict[str, object]:
    try:
        target = characters.move_aliases(
            aliases=request.aliases,
            source_char=request.source_char,
            target_char=request.target_char,
            required_default_id=settings.default_character,
        )
    except CharacterConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    logger.info(
        "Moved aliases=%d source=%s target=%s",
        len(request.aliases),
        request.source_char,
        request.target_char,
    )
    return {
        "status": "ok",
        "aliases": request.aliases,
        "moved_count": len(request.aliases),
        "source_char": request.source_char,
        "target_char": target.char,
        "target_name": target.name,
        "target_aliases": list(target.aliases),
    }


@app.get("/api/voice-design/aliases")
def list_voice_design_aliases() -> dict[str, object]:
    """List aliases eligible for promotion; canonical IDs are omitted."""
    return {"aliases": characters.list_voice_design_aliases()}


def _generate_character_create_preview_queued(
    reference_path: Path,
    ref_text: str,
    preview_text: str,
    speed: float,
    preview_id: str,
) -> Path:
    character = Character(
        char=f"preview_{preview_id}",
        name="Character preview",
        ref_audio=reference_path,
        ref_text=ref_text,
        language="Vietnamese",
        enabled=True,
        parameters={},
        speed=speed,
    )
    audio = engine.generate(preview_text, character)
    target = character_create_preview_dir / f"{preview_id}.preview.wav"
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{preview_id}-", suffix=".wav", dir=character_create_preview_dir
    )
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        sf.write(temp_path, audio, engine.output_sample_rate, format="WAV", subtype="PCM_16")
        os.replace(temp_path, target)
    finally:
        temp_path.unlink(missing_ok=True)
    return target


@app.post("/api/characters/create/preview")
def preview_new_character(
    audio: UploadFile = File(...),
    ref_text: Annotated[str, Form(min_length=1, max_length=settings.max_text_length)] = "",
    preview_text: Annotated[str, Form(max_length=settings.max_text_length)] = DEFAULT_CHARACTER_PREVIEW_TEXT,
    speed: str = Form(default="1.0"),
) -> dict[str, object]:
    if not engine.loaded:
        raise HTTPException(status_code=503, detail="Model TTS chưa sẵn sàng.")
    ref_text = ref_text.strip()
    preview_text = preview_text.strip() or DEFAULT_CHARACTER_PREVIEW_TEXT
    if not ref_text:
        raise HTTPException(status_code=400, detail="ref_text không được để trống.")
    try:
        speed_value = float(speed.strip() or "1.0")
    except (AttributeError, TypeError, ValueError, OverflowError) as exc:
        raise HTTPException(status_code=400, detail="speed phải là số hữu hạn lớn hơn 0.") from exc
    if not math.isfinite(speed_value) or speed_value <= 0:
        raise HTTPException(status_code=400, detail="speed phải là số hữu hạn lớn hơn 0.")

    uploaded_name = (audio.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if not uploaded_name.lower().endswith(".wav"):
        raise HTTPException(status_code=400, detail="Tên file upload phải có phần mở rộng .wav.")
    char_id = uploaded_name[:-4]
    if not char_id or len(char_id) > 128 or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,127}", char_id):
        raise HTTPException(
            status_code=400,
            detail="Tên file trước phần mở rộng .wav phải là mã char hợp lệ (chữ thường, số, '_' hoặc '-').",
        )

    audio_bytes = audio.file.read(MAX_REFERENCE_AUDIO_BYTES + 1)
    if len(audio_bytes) > MAX_REFERENCE_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="File WAV tham chiếu vượt quá giới hạn 50 MiB.")
    preview_id = uuid4().hex
    reference_path = character_create_preview_dir / f"{preview_id}.ref.wav"
    generated_path = character_create_preview_dir / f"{preview_id}.preview.wav"
    reference_path.write_bytes(audio_bytes)
    try:
        info = sf.info(reference_path)
        if info.format != "WAV" or info.frames <= 0 or info.samplerate <= 0:
            raise ValueError("Chỉ chấp nhận WAV hợp lệ có audio.")
    except Exception as exc:
        reference_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"File tham chiếu không phải WAV hợp lệ: {exc}") from exc

    future = inference_queue.submit(
        _generate_character_create_preview_queued,
        reference_path,
        ref_text,
        preview_text,
        speed_value,
        preview_id,
    )
    try:
        generated_path = future.result(timeout=settings.request_timeout_seconds)
    except FutureTimeoutError as exc:
        cancelled = future.cancel()
        if cancelled:
            reference_path.unlink(missing_ok=True)
            generated_path.unlink(missing_ok=True)
        else:
            future.add_done_callback(
                lambda _: (reference_path.unlink(missing_ok=True), generated_path.unlink(missing_ok=True))
            )
        logger.warning("Character preview timeout id=%s", preview_id)
        raise HTTPException(status_code=503, detail="Preview quá thời gian chờ hoặc hàng đợi inference đang bận.") from exc
    except Exception as exc:
        reference_path.unlink(missing_ok=True)
        generated_path.unlink(missing_ok=True)
        logger.exception("Character preview generation failed")
        raise HTTPException(status_code=500, detail="Tạo preview thất bại. Xem log server để biết chi tiết.") from exc

    preview = CharacterCreatePreview(
        char_id=char_id,
        reference_path=reference_path,
        preview_path=generated_path,
        ref_text=ref_text,
        preview_text=preview_text,
        speed=speed_value,
        created_at=time.time(),
    )
    with voice_design_preview_lock:
        character_create_previews[preview_id] = preview
        while len(character_create_previews) > 30:
            oldest_id = min(character_create_previews, key=lambda key: character_create_previews[key].created_at)
            expired = character_create_previews.pop(oldest_id)
            expired.reference_path.unlink(missing_ok=True)
            expired.preview_path.unlink(missing_ok=True)
    logger.info("New character preview generated id=%s", preview_id)
    return {
        "preview_id": preview_id,
        "char_id": char_id,
        "audio_url": f"/api/characters/create/previews/{preview_id}",
        "sample_rate": engine.output_sample_rate,
    }


@app.get("/api/characters/create/previews/{preview_id}", response_class=FileResponse)
def get_new_character_preview(preview_id: UUID) -> FileResponse:
    with voice_design_preview_lock:
        preview = character_create_previews.get(preview_id.hex)
    if preview is None or not preview.preview_path.is_file():
        raise HTTPException(status_code=404, detail="Preview không còn tồn tại; hãy tạo lại.")
    return FileResponse(preview.preview_path, media_type="audio/wav", filename=f"character-preview-{preview_id.hex[:12]}.wav")


@app.post("/api/characters/create/save")
def save_new_character(request: CharacterCreateSaveRequest) -> dict[str, object]:
    preview_id = request.preview_id.hex
    with voice_design_preview_lock:
        preview = character_create_previews.get(preview_id)
        if preview is None or not preview.reference_path.is_file():
            raise HTTPException(status_code=404, detail="Audio upload/preview đã hết hạn; hãy upload và nghe thử lại.")
        try:
            created = characters.create_character_from_audio(
                char_id=preview.char_id,
                name=request.name,
                audio_source=preview.reference_path,
                ref_text=preview.ref_text,
                aliases=request.aliases,
                speed=preview.speed,
                required_default_id=settings.default_character,
            )
        except CharacterConfigError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        character_create_previews.pop(preview_id, None)
        preview.reference_path.unlink(missing_ok=True)
        preview.preview_path.unlink(missing_ok=True)

    logger.info("Created character char=%s moved_aliases=%d", created.char, len(request.aliases))
    return {
        "status": "ok",
        "char": created.char,
        "name": created.name,
        "aliases": list(created.aliases),
        "ref_audio": created.ref_audio.relative_to(settings.base_dir).as_posix(),
        "speed": created.speed,
    }


@app.post("/api/characters/reload")
def reload_characters() -> dict[str, object]:
    try:
        count = characters.load(required_default_id=settings.default_character)
    except CharacterConfigError as exc:
        # The previous valid snapshot remains active.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "ok", "count": count}


def _generate_voice_design_queued(
    text: str,
    instruct: str,
    language: str,
    preview_id: str,
) -> Path:
    started = time.perf_counter()
    audio = engine.generate_voice_design(text, instruct, language)
    target = voice_design_preview_dir / f"{preview_id}.wav"
    fd, temp_name = tempfile.mkstemp(prefix=f".{preview_id}-", suffix=".wav", dir=voice_design_preview_dir)
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        sf.write(temp_path, audio, engine.output_sample_rate, format="WAV", subtype="PCM_16")
        os.replace(temp_path, target)
    finally:
        temp_path.unlink(missing_ok=True)
    logger.info("Voice design preview generated elapsed=%.3fs", time.perf_counter() - started)
    return target


@app.post("/api/voice-design/generate")
def generate_voice_design(request: VoiceDesignRequest) -> dict[str, object]:
    if not engine.loaded:
        raise HTTPException(status_code=503, detail="Model TTS chưa sẵn sàng.")

    preview_id = uuid4().hex
    future = inference_queue.submit(
        _generate_voice_design_queued,
        request.text,
        request.instruct,
        request.language,
        preview_id,
    )
    try:
        path = future.result(timeout=settings.request_timeout_seconds)
    except FutureTimeoutError as exc:
        future.cancel()
        logger.warning("Voice design timeout id=%s", preview_id)
        raise HTTPException(status_code=503, detail="Thiết kế giọng quá thời gian chờ hoặc hàng đợi đang bận.") from exc
    except Exception as exc:
        logger.exception("Voice design generation failed")
        raise HTTPException(status_code=500, detail="Thiết kế giọng thất bại. Xem log server để biết chi tiết.") from exc

    preview = VoiceDesignPreview(
        path=path,
        text=request.text,
        instruct=request.instruct,
        language=request.language,
        created_at=time.time(),
    )
    with voice_design_preview_lock:
        voice_design_previews[preview_id] = preview
        # Keep a bounded set of unsaved previews in memory and on disk.
        while len(voice_design_previews) > 40:
            oldest_id = min(voice_design_previews, key=lambda key: voice_design_previews[key].created_at)
            old = voice_design_previews.pop(oldest_id)
            old.path.unlink(missing_ok=True)

    return {
        "preview_id": preview_id,
        "audio_url": f"/api/voice-design/previews/{preview_id}",
        "sample_rate": engine.output_sample_rate,
        "text": request.text,
        "instruct": request.instruct,
    }


@app.get("/api/voice-design/previews/{preview_id}", response_class=FileResponse)
def get_voice_design_preview(preview_id: UUID) -> FileResponse:
    key = preview_id.hex
    with voice_design_preview_lock:
        preview = voice_design_previews.get(key)
    if preview is None or not preview.path.is_file():
        raise HTTPException(status_code=404, detail="Không tìm thấy preview; hãy tạo giọng thử lại.")
    return FileResponse(preview.path, media_type="audio/wav", filename=f"voice-design-{key[:12]}.wav")


@app.post("/api/voice-design/save")
def save_voice_design(request: VoiceDesignSaveRequest) -> dict[str, object]:
    preview_id = request.preview_id.hex
    with voice_design_preview_lock:
        preview = voice_design_previews.get(preview_id)
        if preview is None or not preview.path.is_file():
            raise HTTPException(status_code=404, detail="Preview không còn tồn tại; hãy tạo giọng thử lại.")
        try:
            saved = characters.promote_aliases_to_character(
                primary_alias=request.primary_alias,
                secondary_aliases=request.aliases,
                name=request.name,
                audio_source=preview.path,
                ref_text=preview.text,
                language=preview.language,
                required_default_id=settings.default_character,
            )
        except CharacterConfigError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        voice_design_previews.pop(preview_id, None)
        preview.path.unlink(missing_ok=True)

    logger.info("Saved designed voice char=%s moved_aliases=%d", saved.char, 1 + len(request.aliases))
    return {
        "status": "ok",
        "character": saved.char,
        "name": saved.name,
        "aliases": list(saved.aliases),
        "ref_audio": saved.ref_audio.relative_to(settings.base_dir).as_posix(),
        "ref_text": saved.ref_text,
    }


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
