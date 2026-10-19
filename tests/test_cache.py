from __future__ import annotations

import wave
from pathlib import Path
from types import MappingProxyType

import numpy as np

from cache import AudioCache
from character_manager import Character


def character(ref_audio: Path) -> Character:
    return Character("hero", "Hero", ref_audio, "reference", "Vietnamese", True, MappingProxyType({}))


def test_cache_hit_and_reference_fingerprint(tmp_path: Path) -> None:
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"first")
    cache = AudioCache(tmp_path / "cache", max_bytes=1_000_000)
    first_key = cache.key_for("hello", character(ref), "model", 24000)
    output = cache.write(first_key, np.zeros(240, dtype=np.float32), 24000)
    assert cache.get(first_key) == output
    with wave.open(str(output), "rb") as wav:
        assert wav.getframerate() == 24000
        assert wav.getnframes() == 240
    ref.write_bytes(b"other")
    assert cache.key_for("hello", character(ref), "model", 24000) != first_key


def test_cache_prunes_old_files(tmp_path: Path) -> None:
    cache = AudioCache(tmp_path / "cache", max_bytes=700)
    cache.write("a" * 64, np.zeros(240, dtype=np.float32), 24000)
    newest = cache.write("b" * 64, np.zeros(240, dtype=np.float32), 24000)
    assert newest.exists()
    assert len(list((tmp_path / "cache").glob("*.wav"))) == 1
