"""Opt-in smoke test for a separately running real server."""

from __future__ import annotations

import io
import os
import wave

import httpx
import pytest


@pytest.mark.skipif(not os.getenv("TTS_REAL_TEST_CHAR"), reason="Set TTS_REAL_TEST_CHAR to run real inference")
def test_real_server_and_inference() -> None:
    base_url = os.getenv("TTS_REAL_TEST_URL", "http://127.0.0.1:8011")
    char = os.environ["TTS_REAL_TEST_CHAR"]
    with httpx.Client(base_url=base_url, timeout=180) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["model_loaded"] is True
        assert client.get("/api/characters").status_code == 200
        response = client.post(
            "/api/tts",
            json={"text": "Xin chào, đây là kiểm tra giọng nói.", "character": char},
        )
        response.raise_for_status()
        with wave.open(io.BytesIO(response.content), "rb") as wav:
            assert wav.getnchannels() >= 1
            assert wav.getframerate() > 0
            assert wav.getnframes() > 0
