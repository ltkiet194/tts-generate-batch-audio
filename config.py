"""Application configuration. All filesystem paths are anchored to this file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


def _load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE settings from a file beside this module.

    Values already present in the process environment take precedence.
    """
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return
    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        key, separator, value = stripped.partition("=")
        key = key.strip()
        if not separator or not key or not key.replace("_", "").isalnum():
            raise ValueError(f"Invalid .env entry at {path}:{line_number}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        os.environ.setdefault(key, value)


_load_dotenv(BASE_DIR / ".env")


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true/false, 1/0, yes/no, or on/off")


def _env_path(name: str, default: Path) -> Path:
    """Resolve relative environment paths from the project directory."""
    value = os.getenv(name)
    if not value:
        return default
    path = Path(value).expanduser()
    return path if path.is_absolute() else BASE_DIR / path


@dataclass(frozen=True, slots=True)
class Settings:
    base_dir: Path = BASE_DIR
    characters_file: Path = _env_path("TTS_CHARACTERS_FILE", BASE_DIR / "characters.json")
    output_dir: Path = _env_path("TTS_OUTPUT_DIR", BASE_DIR / "output")
    cache_dir: Path = _env_path("TTS_CACHE_DIR", BASE_DIR / "cache")
    # kjanh/KhanhTTS-OmniVoice
    # splendor1811/omnivoice-vietnamese
    # k2-fsa/OmniVoice
    model_id: str = os.getenv("TTS_MODEL_ID", "k2-fsa/OmniVoice")
    model_revision: str = os.getenv("TTS_MODEL_REVISION", "main")
    device: str = os.getenv("TTS_DEVICE", "auto")
    # OmniVoice 0.2.x documents a native 24 kHz output.
    model_sample_rate: int = int(os.getenv("TTS_MODEL_SAMPLE_RATE", "24000"))
    output_sample_rate: int = int(os.getenv("TTS_OUTPUT_SAMPLE_RATE", "24000"))
    max_text_length: int = int(os.getenv("TTS_MAX_TEXT_LENGTH", "2000"))
    # Set TTS_CACHE_ENABLED=false to synthesize every request without reusing WAVs.
    cache_enabled: bool = _env_bool("TTS_CACHE_ENABLED", False)
    cache_max_mb: int = int(os.getenv("TTS_CACHE_MAX_MB", "1024"))
    request_timeout_seconds: float = float(os.getenv("TTS_REQUEST_TIMEOUT_SECONDS", "120"))
    model_cache_dir: str | None = os.getenv("TTS_MODEL_CACHE_DIR") or None
    default_character: str = os.getenv("TTS_DEFAULT_CHARACTER", "default")


settings = Settings()
