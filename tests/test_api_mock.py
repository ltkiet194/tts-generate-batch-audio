from __future__ import annotations

import threading
import time
import wave
from pathlib import Path
from types import MappingProxyType

import numpy as np
import pytest
from fastapi.testclient import TestClient

import app as app_module
from cache import AudioCache
from character_manager import Character, CharacterDisabledError, CharacterNotFoundError


class FakeCharacters:
    def __init__(self, character: Character) -> None:
        self.character = character

    def load(self, required_default_id: str | None = None) -> int:
        return 1

    def resolve(self, char_id: str, default_id: str = "default"):
        try:
            return self.get(char_id), False
        except (CharacterNotFoundError, CharacterDisabledError):
            return self.character, True

    def list_public(self):
        return [{"char": self.character.char, "name": self.character.name}]

    def get(self, char_id: str) -> Character:
        if char_id == "missing":
            raise CharacterNotFoundError(char_id)
        if char_id == "disabled":
            raise CharacterDisabledError(char_id)
        return self.character


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    root = tmp_path_factory.mktemp("api")
    ref = root / "ref.wav"
    with wave.open(str(ref), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\x00\x00" * 240)
    character = Character("hero", "Hero", ref, "reference", "Vietnamese", True, MappingProxyType({}))
    app_module.characters = FakeCharacters(character)
    app_module.audio_cache = AudioCache(root / "cache", max_bytes=1_000_000)
    calls = {"count": 0, "active": 0, "max_active": 0}
    guard = threading.Lock()

    def fake_load():
        app_module.engine._model = object()
        app_module.engine.device = "cpu"

    def fake_generate(text, selected):
        with guard:
            calls["count"] += 1
            calls["active"] += 1
            calls["max_active"] = max(calls["max_active"], calls["active"])
        time.sleep(0.02)
        with guard:
            calls["active"] -= 1
        return np.zeros(480, dtype=np.float32)

    app_module.engine.load = fake_load
    app_module.engine.generate = fake_generate
    app_module.engine.close = lambda: None
    with TestClient(app_module.app) as test_client:
        yield test_client, calls


def test_health_and_characters(client) -> None:
    api, _ = client
    assert api.get("/api/health").json() == {"status": "ok", "model_loaded": True, "device": "cpu"}
    assert api.get("/api/characters").json() == {"characters": [{"char": "hero", "name": "Hero"}]}


def test_real_decodable_wav_and_cache(client) -> None:
    api, calls = client
    payload = {"text": "Xin chào", "char": "hero"}
    first = api.post("/api/tts", json=payload)
    second = api.post("/api/tts", json=payload)
    assert first.status_code == second.status_code == 200
    assert first.headers["content-type"].startswith("audio/wav")
    assert first.content[:4] == b"RIFF"
    assert first.content == second.content
    assert calls["count"] == 1


def test_character_is_primary_request_field(client) -> None:
    api, _ = client
    response = api.post("/api/tts", json={"text": "Trường character mới", "character": "hero"})
    assert response.status_code == 200
    assert response.content[:4] == b"RIFF"


def test_bad_requests(client) -> None:
    api, _ = client
    assert api.post("/api/tts", json={"text": "ok", "char": "missing"}).status_code == 200
    assert api.post("/api/tts", json={"text": "", "char": "hero"}).status_code == 400
    assert api.post("/api/tts", json={"text": "ok", "char": "disabled"}).status_code == 200
    assert api.post("/api/tts", json={"text": "ok", "character": "missing"}).status_code == 200


def test_queue_serializes_generation(client) -> None:
    api, calls = client
    responses = []

    def request(index: int) -> None:
        responses.append(api.post("/api/tts", json={"text": f"line {index}", "char": "hero"}))

    threads = [threading.Thread(target=request, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert all(response.status_code == 200 for response in responses)
    assert calls["max_active"] == 1
