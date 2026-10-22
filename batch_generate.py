"""Generate a complete offline WAV collection from CSV or JSONL.

Required input fields: id, char, text. The model is loaded exactly once.
Unknown/disabled character IDs resolve to TTS_DEFAULT_CHARACTER.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import re
import signal
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import soundfile as sf

from character_manager import CharacterManager
from config import BASE_DIR, settings
from tts_engine import TTSEngine


logger = logging.getLogger("batch_generate")
STOP_REQUESTED = False


@dataclass(frozen=True, slots=True)
class Dialogue:
    dialogue_id: str
    character: str
    text: str
    line_number: int


def env_path(name: str, default: str) -> Path:
    raw = os.getenv(name, default)
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (BASE_DIR / path).resolve()


def env_int(name: str, default: int, minimum: int = 1) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def read_dialogues(path: Path) -> Iterator[Dialogue]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {"id", "char", "text"}
            missing = required - set(reader.fieldnames or ())
            if missing:
                raise ValueError(f"CSV thiếu cột: {', '.join(sorted(missing))}")
            for line_number, row in enumerate(reader, start=2):
                yield parse_row(row, line_number)
        return
    if suffix in {".jsonl", ".ndjson"}:
        with path.open("r", encoding="utf-8-sig") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"Dòng {line_number}: phải là JSON object")
                yield parse_row(value, line_number)
        return
    raise ValueError("Input phải có đuôi .csv, .jsonl hoặc .ndjson")


def parse_row(row: dict[str, Any], line_number: int) -> Dialogue:
    dialogue_id = str(row.get("id", "")).strip()
    character = str(row.get("char", "")).strip()
    text = str(row.get("text", "")).strip()
    if not dialogue_id or not character or not text:
        raise ValueError(f"Dòng {line_number}: id, char và text không được rỗng")
    if len(text) > settings.max_text_length:
        raise ValueError(
            f"Dòng {line_number}: text dài {len(text)} ký tự, giới hạn {settings.max_text_length}"
        )
    return Dialogue(dialogue_id, character, text, line_number)


def safe_stem(dialogue_id: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", dialogue_id).strip("._")
    if not stem:
        stem = "line"
    if stem != dialogue_id:
        digest = hashlib.sha256(dialogue_id.encode("utf-8")).hexdigest()[:10]
        stem = f"{stem[:100]}-{digest}"
    return stem[:120]


def valid_wav(path: Path, sample_rate: int) -> bool:
    try:
        info = sf.info(path)
        return info.frames > 0 and info.samplerate == sample_rate and info.channels == 1
    except (OSError, RuntimeError):
        return False


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", suffix=".tmp", dir=path.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_wav(path: Path, audio: Any, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=".wav", dir=path.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        sf.write(temporary, audio, sample_rate, format="WAV", subtype="PCM_16")
        if not valid_wav(temporary, sample_rate):
            raise RuntimeError("WAV vừa tạo không thể xác minh")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_existing_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"version": 1, "items": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("items"), dict):
        raise ValueError(f"Manifest không hợp lệ: {path}")
    return value


def configure_signals() -> None:
    def request_stop(_signum: int, _frame: Any) -> None:
        global STOP_REQUESTED
        STOP_REQUESTED = True
        logger.warning("Đã nhận tín hiệu dừng; sẽ checkpoint sau dòng hiện tại.")

    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_stop)


def main() -> int:
    if os.name == "nt":
        for stream in (sys.stdout, sys.stderr):
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure is not None:
                reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=env_path("TTS_BATCH_INPUT", "dialogues.csv"))
    parser.add_argument("--output", type=Path, default=env_path("TTS_BATCH_OUTPUT_DIR", "generated_audio"))
    parser.add_argument("--checkpoint-every", type=int, default=env_int("TTS_BATCH_CHECKPOINT_EVERY", 10))
    parser.add_argument("--retry-failed", action="store_true", help="Thử lại các dòng từng lỗi")
    parser.add_argument("--limit", type=int, default=None, help="Chỉ xử lý N dòng đầu để test")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    configure_signals()
    input_path = args.input.expanduser().resolve()
    output_dir = args.output.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"
    failed_path = output_dir / "failed.json"

    dialogues = list(read_dialogues(input_path))
    if args.limit is not None:
        dialogues = dialogues[: max(args.limit, 0)]
    duplicate_ids = [key for key, count in Counter(d.dialogue_id for d in dialogues).items() if count > 1]
    if duplicate_ids:
        raise ValueError(f"ID trùng lặp: {', '.join(duplicate_ids[:10])}")

    manager = CharacterManager(settings.characters_file, settings.base_dir)
    character_count = manager.load(required_default_id=settings.default_character)
    engine = TTSEngine(
        model_id=settings.model_id,
        device=settings.device,
        model_sample_rate=settings.model_sample_rate,
        output_sample_rate=settings.output_sample_rate,
        model_cache_dir=settings.model_cache_dir,
        model_revision=settings.model_revision,
    )
    manifest = load_existing_manifest(manifest_path)
    items: dict[str, Any] = manifest["items"]
    failures: dict[str, Any] = {}
    if failed_path.is_file():
        old_failures = json.loads(failed_path.read_text(encoding="utf-8"))
        if isinstance(old_failures, dict):
            failures = old_failures

    logger.info("Input: %s (%d dòng), %d nhân vật", input_path, len(dialogues), character_count)
    logger.info("Đang nạp model một lần...")
    engine.load()
    logger.info("Model sẵn sàng trên %s", engine.device)
    generated = skipped = failed = fallback_count = 0
    started_all = time.perf_counter()

    def checkpoint() -> None:
        manifest.update(
            model=engine.model_identity,
            sample_rate=settings.output_sample_rate,
            updated_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        )
        atomic_write_json(manifest_path, manifest)
        atomic_write_json(failed_path, failures)

    try:
        for index, dialogue in enumerate(dialogues, start=1):
            if STOP_REQUESTED:
                break
            filename = f"{safe_stem(dialogue.dialogue_id)}.wav"
            target = output_dir / filename
            previous = items.get(dialogue.dialogue_id, {})
            unchanged = (
                previous.get("text") == dialogue.text
                and previous.get("char_requested") == dialogue.character
                and previous.get("model") == engine.model_identity
                and valid_wav(target, settings.output_sample_rate)
            )
            if unchanged:
                skipped += 1
                continue
            if dialogue.dialogue_id in failures and not args.retry_failed:
                failed += 1
                continue

            line_started = time.perf_counter()
            try:
                character, used_fallback = manager.resolve(dialogue.character, settings.default_character)
                audio = engine.generate(dialogue.text, character)
                atomic_write_wav(target, audio, settings.output_sample_rate)
                elapsed = time.perf_counter() - line_started
                items[dialogue.dialogue_id] = {
                    "file": filename,
                    "char_requested": dialogue.character,
                    "char_resolved": character.char,
                    "used_fallback": used_fallback,
                    "text": dialogue.text,
                    "model": engine.model_identity,
                    "sample_rate": settings.output_sample_rate,
                    "seconds_to_generate": round(elapsed, 3),
                }
                failures.pop(dialogue.dialogue_id, None)
                generated += 1
                fallback_count += int(used_fallback)
                logger.info("[%d/%d] %s -> %s (%.1fs)%s", index, len(dialogues), dialogue.dialogue_id, character.char, elapsed, " [default]" if used_fallback else "")
            except Exception as exc:  # Continue the batch; error details stay local.
                failed += 1
                failures[dialogue.dialogue_id] = {
                    "line": dialogue.line_number,
                    "char": dialogue.character,
                    "error": f"{type(exc).__name__}: {exc}",
                }
                logger.exception("[%d/%d] Lỗi tại id=%s", index, len(dialogues), dialogue.dialogue_id)
            if (generated + failed) % args.checkpoint_every == 0:
                checkpoint()
    finally:
        checkpoint()
        engine.close()

    summary = {
        "total_input": len(dialogues),
        "generated": generated,
        "skipped_valid": skipped,
        "failed": failed,
        "fallback_to_default": fallback_count,
        "stopped_early": STOP_REQUESTED,
        "elapsed_seconds": round(time.perf_counter() - started_all, 2),
        "output_dir": str(output_dir),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Lỗi cấu hình/input: {exc}", file=sys.stderr)
        raise SystemExit(2)
