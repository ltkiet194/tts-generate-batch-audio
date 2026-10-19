from __future__ import annotations

import pytest

from sync_char_aliases import SyncError, sync_aliases


def config() -> dict:
    return {
        "characters": {
            "voice_a": {"aliases": ["manual_a", "old_csv_key"]},
            "voice_b": {"aliases": ["manual_b"]},
            "default": {},
        }
    }


def test_sync_moves_csv_alias_and_preserves_manual_aliases() -> None:
    data = config()
    assignments = {
        "old_csv_key": "voice_b",
        "new_key": "voice_a",
        "voice_a": "voice_a",
        "no_voice": "default",
    }
    added, removed, counts = sync_aliases(data, assignments)
    assert removed == 1
    assert added == 2
    assert counts["voice_a"] == 1
    assert data["characters"]["voice_a"]["aliases"] == ["manual_a", "new_key"]
    assert data["characters"]["voice_b"]["aliases"] == ["manual_b", "old_csv_key"]
    assert "aliases" not in data["characters"]["default"]


def test_unknown_voice_is_rejected() -> None:
    with pytest.raises(SyncError, match="không tồn tại"):
        sync_aliases(config(), {"npc": "missing_voice"})


def test_canonical_key_cannot_point_to_another_voice() -> None:
    with pytest.raises(SyncError, match="mã nhân vật chính"):
        sync_aliases(config(), {"voice_a": "voice_b"})
