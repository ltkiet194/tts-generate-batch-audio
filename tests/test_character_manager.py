from __future__ import annotations

import json
import wave
from pathlib import Path

import pytest

from character_manager import CharacterConfigError, CharacterDisabledError, CharacterManager


def write_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\x00\x00" * 240)


def write_config(
    path: Path,
    audio_name: str = "voice.wav",
    enabled: bool = True,
    aliases: list[str] | None = None,
) -> None:
    data = {
        "characters": {
            "hero": {
                "name": "Nhân vật",
                "aliases": aliases or [],
                "ref_audio": audio_name,
                "ref_text": "Đây là giọng tham chiếu.",
                "language": "Vietnamese",
                "enabled": enabled,
                "parameters": {},
            }
        }
    }
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_load_list_and_disabled_character(tmp_path: Path) -> None:
    write_wav(tmp_path / "voice.wav")
    write_config(tmp_path / "characters.json", enabled=False)
    manager = CharacterManager(tmp_path / "characters.json", tmp_path)
    assert manager.load() == 1
    assert manager.list_public() == []
    with pytest.raises(CharacterDisabledError):
        manager.get("hero")


def test_missing_audio_is_clear_error(tmp_path: Path) -> None:
    write_config(tmp_path / "characters.json", audio_name="missing.wav")
    manager = CharacterManager(tmp_path / "characters.json", tmp_path)
    with pytest.raises(CharacterConfigError, match="không tồn tại"):
        manager.load()


def test_failed_reload_keeps_previous_snapshot(tmp_path: Path) -> None:
    write_wav(tmp_path / "voice.wav")
    config = tmp_path / "characters.json"
    write_config(config)
    manager = CharacterManager(config, tmp_path)
    manager.load()
    write_config(config, audio_name="missing.wav")
    with pytest.raises(CharacterConfigError):
        manager.load()
    assert manager.get("hero").name == "Nhân vật"


def test_reload_adds_character_without_code_change(tmp_path: Path) -> None:
    write_wav(tmp_path / "voice.wav")
    config = tmp_path / "characters.json"
    write_config(config)
    manager = CharacterManager(config, tmp_path)
    manager.load()
    data = json.loads(config.read_text(encoding="utf-8"))
    data["characters"]["new_hero"] = {**data["characters"]["hero"], "name": "Nhân vật mới"}
    config.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert manager.load() == 2
    assert manager.get("new_hero").name == "Nhân vật mới"


def test_alias_resolves_to_canonical_character_and_shared_cache_identity(tmp_path: Path) -> None:
    write_wav(tmp_path / "voice.wav")
    write_config(tmp_path / "characters.json", aliases=["npc_001"])
    manager = CharacterManager(tmp_path / "characters.json", tmp_path)
    manager.load()
    canonical = manager.get("hero")
    alias = manager.get("npc_001")
    assert alias is canonical
    assert alias.char == "hero"
    assert manager.list_public() == [
        {"char": "hero", "name": "Nhân vật", "aliases": ["npc_001"]}
    ]


@pytest.mark.parametrize(
    "first_aliases,second_id,second_aliases",
    [
        (["second"], "second", []),
        (["shared"], "second", ["shared"]),
    ],
)
def test_alias_collision_is_rejected(
    tmp_path: Path,
    first_aliases: list[str],
    second_id: str,
    second_aliases: list[str],
) -> None:
    write_wav(tmp_path / "voice.wav")
    config = tmp_path / "characters.json"
    write_config(config, aliases=first_aliases)
    data = json.loads(config.read_text(encoding="utf-8"))
    data["characters"][second_id] = {
        **data["characters"]["hero"],
        "name": "Nhân vật hai",
        "aliases": second_aliases,
    }
    config.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(CharacterConfigError, match="trùng|đã thuộc"):
        CharacterManager(config, tmp_path).load()


def test_unknown_and_disabled_character_fall_back_to_default(tmp_path: Path) -> None:
    write_wav(tmp_path / "voice.wav")
    config = tmp_path / "characters.json"
    write_config(config, enabled=False)
    data = json.loads(config.read_text(encoding="utf-8"))
    data["characters"]["default"] = {
        **data["characters"]["hero"],
        "name": "Giọng mặc định",
        "enabled": True,
        "aliases": [],
    }
    config.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    manager = CharacterManager(config, tmp_path)
    manager.load(required_default_id="default")
    unknown, unknown_fallback = manager.resolve("not_configured")
    disabled, disabled_fallback = manager.resolve("hero")
    assert unknown.char == disabled.char == "default"
    assert unknown_fallback is disabled_fallback is True


def test_required_default_must_exist_and_be_enabled(tmp_path: Path) -> None:
    write_wav(tmp_path / "voice.wav")
    config = tmp_path / "characters.json"
    write_config(config)
    manager = CharacterManager(config, tmp_path)
    with pytest.raises(CharacterConfigError, match="mặc định"):
        manager.load(required_default_id="default")
