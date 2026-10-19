"""Application configuration. All filesystem paths are anchored to this file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    base_dir: Path = BASE_DIR
    characters_file: Path = BASE_DIR / "characters.json"
    output_dir: Path = BASE_DIR / "output"
    cache_dir: Path = BASE_DIR / "cache"
    model_id: str = os.getenv("TTS_MODEL_ID", "kjanh/KhanhTTS-OmniVoice")
    model_revision: str = os.getenv("TTS_MODEL_REVISION", "main")
    device: str = os.getenv("TTS_DEVICE", "auto")
    # OmniVoice 0.2.x documents a native 24 kHz output.
    model_sample_rate: int = int(os.getenv("TTS_MODEL_SAMPLE_RATE", "24000"))
    output_sample_rate: int = int(os.getenv("TTS_OUTPUT_SAMPLE_RATE", "24000"))
    max_text_length: int = int(os.getenv("TTS_MAX_TEXT_LENGTH", "2000"))
    cache_enabled: bool = _env_bool("TTS_CACHE_ENABLED", True)
    cache_max_mb: int = int(os.getenv("TTS_CACHE_MAX_MB", "1024"))
    request_timeout_seconds: float = float(os.getenv("TTS_REQUEST_TIMEOUT_SECONDS", "120"))
    model_cache_dir: str | None = os.getenv("TTS_MODEL_CACHE_DIR") or None
    default_character: str = os.getenv("TTS_DEFAULT_CHARACTER", "default")


settings = Settings()
