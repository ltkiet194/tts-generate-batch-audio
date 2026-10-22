"""Content-addressed WAV cache with atomic writes."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

import soundfile as sf

from character_manager import Character


class AudioCache:
    def __init__(self, directory: Path, enabled: bool = True, max_bytes: int = 1024 * 1024 * 1024) -> None:
        self.directory = directory
        self.enabled = enabled
        self.max_bytes = max(0, max_bytes)
        self._lock = threading.Lock()
        self.directory.mkdir(parents=True, exist_ok=True)

    def key_for(self, text: str, character: Character, model_id: str, sample_rate: int) -> str:
        reference_hash = hashlib.sha256()
        with character.ref_audio.open("rb") as reference_file:
            for chunk in iter(lambda: reference_file.read(1024 * 1024), b""):
                reference_hash.update(chunk)
        payload: dict[str, Any] = {
            "version": 2,
            "model": model_id,
            "sample_rate": sample_rate,
            "text": text,
            "char": character.char,
            "ref_text": character.ref_text,
            "ref_audio": str(character.ref_audio),
            "ref_audio_sha256": reference_hash.hexdigest(),
            "speed": character.speed,
            "parameters": dict(character.parameters),
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def get(self, key: str) -> Path | None:
        if not self.enabled:
            return None
        path = self.directory / f"{key}.wav"
        if not path.is_file():
            return None
        # Access time is unreliable/disabled on some Windows volumes; mtime is
        # deliberately used as the LRU timestamp.
        try:
            os.utime(path, None)
        except OSError:
            pass
        return path

    def write(self, key: str, audio: Any, sample_rate: int) -> Path:
        target = self.directory / f"{key}.wav"
        if not self.enabled:
            return self._write_temp(audio, sample_rate)
        fd, temp_name = tempfile.mkstemp(prefix=f".{key}-", suffix=".wav", dir=self.directory)
        os.close(fd)
        temp_path = Path(temp_name)
        try:
            sf.write(temp_path, audio, sample_rate, format="WAV", subtype="PCM_16")
            os.replace(temp_path, target)
        finally:
            temp_path.unlink(missing_ok=True)
        self.prune(protected=target)
        return target

    def prune(self, protected: Path | None = None) -> int:
        """Remove least-recently-used WAVs until the configured byte quota is met."""
        if not self.enabled or self.max_bytes <= 0:
            return 0
        removed = 0
        with self._lock:
            files = []
            total = 0
            for path in self.directory.glob("*.wav"):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                total += stat.st_size
                files.append((stat.st_mtime_ns, stat.st_size, path))
            for _, size, path in sorted(files):
                if total <= self.max_bytes:
                    break
                if protected is not None and path == protected:
                    continue
                try:
                    path.unlink()
                except OSError:
                    continue
                total -= size
                removed += 1
        return removed

    def _write_temp(self, audio: Any, sample_rate: int) -> Path:
        fd, name = tempfile.mkstemp(prefix="tts-", suffix=".wav", dir=self.directory)
        os.close(fd)
        path = Path(name)
        sf.write(path, audio, sample_rate, format="WAV", subtype="PCM_16")
        return path
