"""Compare characters.json with its timestamped backups without modifying files."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class Snapshot:
    path: Path
    canonical: frozenset[str]
    alias_owner: dict[str, str]

    @property
    def identifiers(self) -> frozenset[str]:
        return self.canonical | frozenset(self.alias_owner)


def load_snapshot(path: Path) -> Snapshot:
    data = json.loads(path.read_text(encoding="utf-8"))
    characters = data.get("characters") if isinstance(data, dict) else None
    if not isinstance(characters, dict):
        raise ValueError("thiếu object characters")
    aliases: dict[str, str] = {}
    for char_id, entry in characters.items():
        if not isinstance(char_id, str) or not isinstance(entry, dict):
            raise ValueError("char không hợp lệ")
        raw_aliases = entry.get("aliases", [])
        if not isinstance(raw_aliases, list):
            raise ValueError(f"characters.{char_id}.aliases không phải array")
        for alias in raw_aliases:
            if not isinstance(alias, str) or not alias:
                raise ValueError(f"characters.{char_id} có alias không hợp lệ")
            if alias in aliases:
                raise ValueError(f"alias trùng '{alias}'")
            aliases[alias] = char_id
    collisions = set(characters).intersection(aliases)
    if collisions:
        raise ValueError(f"alias trùng char chính: {', '.join(sorted(collisions)[:10])}")
    return Snapshot(path, frozenset(characters), aliases)


def check_integrity(current_path: Path) -> dict[str, Any]:
    current_path = current_path.resolve()
    current = load_snapshot(current_path)
    candidates = sorted(
        current_path.parent.glob("characters.backup*.json"),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
    )
    backups: list[Snapshot] = []
    invalid_backups: list[dict[str, str]] = []
    for path in candidates:
        try:
            backups.append(load_snapshot(path))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            invalid_backups.append({"file": path.name, "error": str(exc)})

    if not backups:
        return {
            "status": "no_backups",
            "current_file": current_path.name,
            "backup_count": 0,
            "current": _counts(current),
            "invalid_backups": invalid_backups,
        }

    latest = backups[-1]
    historical_ids = frozenset().union(*(backup.identifiers for backup in backups))
    missing_from_latest = sorted(latest.identifiers - current.identifiers)
    new_since_latest = sorted(current.identifiers - latest.identifiers)
    missing_historical = sorted(historical_ids - current.identifiers)
    moved_aliases = [
        {"alias": alias, "from": latest.alias_owner[alias], "to": current.alias_owner[alias]}
        for alias in sorted(set(latest.alias_owner).intersection(current.alias_owner))
        if latest.alias_owner[alias] != current.alias_owner[alias]
    ]
    canonical_to_alias = [
        {"id": char_id, "owner": current.alias_owner[char_id]}
        for char_id in sorted(latest.canonical - current.canonical)
        if char_id in current.alias_owner
    ]
    alias_to_canonical = [
        {"id": alias, "previous_owner": latest.alias_owner[alias]}
        for alias in sorted(set(latest.alias_owner).intersection(current.canonical))
    ]
    return {
        "status": "warning" if missing_from_latest or invalid_backups else "ok",
        "current_file": current_path.name,
        "latest_backup": latest.path.name,
        "backup_count": len(backups),
        "current": _counts(current),
        "latest": _counts(latest),
        "missing_from_latest": missing_from_latest,
        "new_since_latest": new_since_latest,
        "missing_historical": missing_historical,
        "moved_aliases": moved_aliases,
        "canonical_to_alias": canonical_to_alias,
        "alias_to_canonical": alias_to_canonical,
        "invalid_backups": invalid_backups,
    }


def _counts(snapshot: Snapshot) -> dict[str, int]:
    return {
        "characters": len(snapshot.canonical),
        "aliases": len(snapshot.alias_owner),
        "identifiers": len(snapshot.identifiers),
    }
