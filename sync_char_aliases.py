"""Đồng bộ aliases trong characters.json từ char.csv.

Quy tắc:
- snake_key được thêm vào aliases của nhân vật có khóa bằng cột voice.
- voice=default bị bỏ qua, không thêm alias.
- snake_key trùng chính khóa voice không cần được thêm làm alias.
- Alias thủ công không xuất hiện trong CSV được giữ nguyên.
- Chạy lại an toàn: snake_key trong CSV được gỡ khỏi vị trí cũ trước khi gán lại.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CSV = BASE_DIR / "char.csv"
DEFAULT_JSON = BASE_DIR / "characters.json"
REQUIRED_COLUMNS = {"name", "snake_key", "voice"}


class SyncError(ValueError):
    pass


def read_assignments(csv_path: Path) -> tuple[dict[str, str], int]:
    assignments: dict[str, str] = {}
    skipped_default = 0

    try:
        csv_file = csv_path.open("r", encoding="utf-8-sig", newline="")
    except FileNotFoundError as exc:
        raise SyncError(f"Không tìm thấy CSV: {csv_path}") from exc

    with csv_file:
        reader = csv.DictReader(csv_file, skipinitialspace=True)
        columns = set(reader.fieldnames or [])
        missing = REQUIRED_COLUMNS - columns
        if missing:
            raise SyncError(f"CSV thiếu cột: {', '.join(sorted(missing))}")

        for line_number, row in enumerate(reader, start=2):
            snake_key = (row.get("snake_key") or "").strip()
            voice = (row.get("voice") or "").strip()
            if not snake_key:
                raise SyncError(f"Dòng {line_number}: snake_key rỗng.")
            if not voice:
                raise SyncError(f"Dòng {line_number}: voice rỗng.")

            previous = assignments.get(snake_key)
            if previous is not None and previous != voice:
                raise SyncError(
                    f"Dòng {line_number}: snake_key '{snake_key}' được gán cho cả "
                    f"'{previous}' và '{voice}'."
                )
            assignments[snake_key] = voice
            if voice == "default":
                skipped_default += 1

    return assignments, skipped_default


def load_characters(json_path: Path) -> dict[str, Any]:
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SyncError(f"Không tìm thấy JSON: {json_path}") from exc
    except json.JSONDecodeError as exc:
        raise SyncError(
            f"JSON lỗi tại dòng {exc.lineno}, cột {exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(data, dict) or not isinstance(data.get("characters"), dict):
        raise SyncError("characters.json phải có object gốc 'characters'.")
    return data


def sync_aliases(
    data: dict[str, Any], assignments: dict[str, str]
) -> tuple[int, int, dict[str, int]]:
    characters = data["characters"]
    canonical_ids = set(characters)
    csv_keys = set(assignments)

    unknown_voices = sorted(
        {voice for voice in assignments.values() if voice != "default" and voice not in canonical_ids}
    )
    if unknown_voices:
        raise SyncError(
            "Các voice sau không tồn tại trong characters.json: " + ", ".join(unknown_voices)
        )

    # Remove every CSV-managed key first. This lets a later CSV edit move an
    # alias between voices without leaving a stale copy behind.
    removed = 0
    preserved_aliases: dict[str, list[str]] = {}
    for character_id, config in characters.items():
        if not isinstance(config, dict):
            raise SyncError(f"characters.{character_id} phải là object.")
        aliases = config.get("aliases", [])
        if not isinstance(aliases, list) or not all(isinstance(alias, str) for alias in aliases):
            raise SyncError(f"characters.{character_id}.aliases phải là mảng chuỗi.")
        kept: list[str] = []
        for alias in aliases:
            normalized = alias.strip()
            if normalized in csv_keys:
                removed += 1
            elif normalized and normalized not in kept:
                kept.append(normalized)
        preserved_aliases[character_id] = kept

    additions: dict[str, list[str]] = defaultdict(list)
    skipped_canonical = 0
    for snake_key, voice in assignments.items():
        if voice == "default":
            continue
        if snake_key in canonical_ids:
            if snake_key != voice:
                raise SyncError(
                    f"snake_key '{snake_key}' là mã nhân vật chính nhưng CSV lại gán voice '{voice}'."
                )
            skipped_canonical += 1
            continue
        additions[voice].append(snake_key)

    # Validate collisions with preserved/manual aliases before mutating data.
    owner: dict[str, str] = {}
    for character_id, aliases in preserved_aliases.items():
        for alias in aliases:
            if alias in canonical_ids:
                raise SyncError(
                    f"Alias thủ công '{alias}' của '{character_id}' trùng mã nhân vật chính."
                )
            previous = owner.get(alias)
            if previous is not None and previous != character_id:
                raise SyncError(f"Alias '{alias}' thuộc cả '{previous}' và '{character_id}'.")
            owner[alias] = character_id

    added = 0
    counts: dict[str, int] = {}
    for character_id, config in characters.items():
        aliases = preserved_aliases[character_id]
        for alias in additions.get(character_id, []):
            previous = owner.get(alias)
            if previous is not None and previous != character_id:
                raise SyncError(f"Alias '{alias}' đã thuộc về '{previous}', không thể gán '{character_id}'.")
            if alias not in aliases:
                aliases.append(alias)
                owner[alias] = character_id
                added += 1
        if aliases:
            config["aliases"] = aliases
        else:
            config.pop("aliases", None)
        counts[character_id] = len(additions.get(character_id, []))

    return added, removed, counts


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}-", suffix=".tmp", dir=path.parent)
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        temp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Đồng bộ snake_key từ char.csv vào aliases theo cột voice."
    )
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="Đường dẫn char.csv")
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON, help="Đường dẫn characters.json")
    parser.add_argument("--dry-run", action="store_true", help="Chỉ kiểm tra, không ghi JSON")
    args = parser.parse_args()

    try:
        assignments, skipped_default = read_assignments(args.csv.resolve())
        data = load_characters(args.json.resolve())
        added, removed, counts = sync_aliases(data, assignments)
        if not args.dry_run:
            write_json_atomic(args.json.resolve(), data)
    except SyncError as exc:
        parser.error(str(exc))

    mode = "DRY RUN" if args.dry_run else "ĐÃ GHI"
    print(f"[{mode}] {args.json.resolve()}")
    print(f"CSV unique snake_key: {len(assignments)}")
    print(f"Bỏ qua voice=default: {skipped_default}")
    print(f"Gỡ alias CSV cũ: {removed}")
    print(f"Thêm alias theo CSV: {added}")
    for voice, count in sorted(counts.items()):
        if voice != "default":
            print(f"  {voice}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
