"""Convert hoithoai.csv records into batch_generate.py's id,char,text CSV.

Expected source record:
    "1 - Vũ Văn Dật $@$(Nội dung lời thoại.)"
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
GAME_TAG = re.compile(r"<[^>]*>")
HEADER = re.compile(r"^\s*(?P<source_id>.*?)\s+-\s+(?P<speaker>.*?)\s*$")
SUPPORTED_TTS_TAG = re.compile(
    r"\[(?:laughter|sigh|confirmation-en|question-(?:en|ah|oh|ei|yi)|"
    r"surprise-(?:ah|oh|wa|yo)|dissatisfaction-hnn)\]"
)

# Every vocalization uses Unicode word boundaries on both sides. This prevents
# false positives such as the cross-word substring "hi Hi" in "Phi Hiết Sứ".
TTS_TAG_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?i)(?<!\w)(hi|hì|ha|khà|hề|hê|phì)(?!\w)"
            r"(?:\s*[,.-]?\s+\1(?!\w))+[,.…!]*"
        ),
        "[laughter] [laughter]",
    ),
    (re.compile(r"(?i)(?<!\w)(?:ha){2,}(?!\w)"), "[laughter] [laughter]"),
    (re.compile(r"(?i)(?<!\w)phì(?!\w)[,.…!]*"), "[laughter] [laughter]"),
    (
        re.compile(r"(?i)(?<!\w)(?:ha+i+z+|hầy|aiya+|ai\s+da(?:\s+ai\s+da)*)(?!\w)[,.…!]*"),
        "[sigh]",
    ),
    (re.compile(r"(?i)(?<!\w)ai(?!\w)\s*[,…!]+"), "[sigh]"),
    (
        re.compile(r"(?i)(?<!\w)(?:ừ|ừm|uhm|hử|hửm|hm|hmm)(?!\w)\s*\?+[!?]*"),
        "[question-en]",
    ),
    (re.compile(r"(?i)(?<!\w)(?:à|a)(?!\w)\s*\?+[!?]*"), "[question-ah]"),
    (re.compile(r"(?i)(?<!\w)(?:ồ|ô)(?!\w)\s*\?+[!?]*"), "[question-oh]"),
    (re.compile(r"(?i)(?<!\w)(?:ê|ể|ầy)(?!\w)\s*\?+[!?]*"), "[question-ei]"),
    (re.compile(r"(?i)(?<!\w)(?:ý|í)(?!\w)\s*\?+[!?]*"), "[question-yi]"),
    (re.compile(r"(?i)(?<!\w)(?:á|a|à)(?!\w)\s*!+"), "[surprise-ah]"),
    (re.compile(r"(?i)(?<!\w)(?:ồ|ô)(?!\w)\s*!+"), "[surprise-oh]"),
    (re.compile(r"(?i)(?<!\w)ồ(?!\w)(?:\s+ồ(?!\w))*[,.…]*"), "[surprise-oh]"),
    (re.compile(r"(?i)(?<!\w)(?:oa|wow|wa)(?!\w)\s*!+"), "[surprise-wa]"),
    (re.compile(r"(?i)(?<!\w)(?:yo|yô)(?!\w)\s*!+"), "[surprise-yo]"),
    (re.compile(r"(?i)(?<!\w)(?:hờ|xì)(?!\w)\s*[,…!]+"), "[dissatisfaction-hnn]"),
    (re.compile(r"(?i)(?<!\w)(?:hừ+|hừm+|hmph)(?!\w)[,.…!]*"), "[dissatisfaction-hnn]"),
    (re.compile(r"(?i)(?<!\w)(?:ừ+|ừm+|uhm+)(?!\w)[,.…!]*"), "[confirmation-en]"),
)


def snake_key(value: str) -> str:
    value = unicodedata.normalize("NFD", value.strip().lower())
    value = "".join(char for char in value if unicodedata.category(char) != "Mn")
    value = value.replace("đ", "d")
    return re.sub(r"[^a-z0-9]+", "_", value).strip("_")


def normalized_name(value: str) -> str:
    return snake_key(value)


def clean_original_text(value: str) -> str:
    value = html.unescape(value)
    value = GAME_TAG.sub("", value)
    value = value.replace("\ufeff", "").replace("\u200b", "")
    value = re.sub(r"[\t\r\n]+", " ", value)
    value = re.sub(r" {2,}", " ", value).strip()
    return value


def preprocess_tts_tags(value: str) -> str:
    # Keep the hesitation audible while adding the closest supported breath cue.
    value = re.sub(
        r"(?i)(?<!\w)ờm+(?!\w)",
        lambda match: f"[sigh] {match.group(0)}",
        value,
    )
    for pattern, tag in TTS_TAG_RULES:
        value = pattern.sub(f"{tag} ", value)
    # OmniVoice produces a clearer laugh when the supported token is repeated.
    # Preserve runs of two or more and expand any single pre-existing token.
    value = re.sub(
        r"(?:\[laughter\](?:\s+|$))+",
        lambda match: (
            match.group(0)
            if match.group(0).count("[laughter]") >= 2
            else "[laughter] [laughter] "
        ),
        value,
    )
    value = re.sub(r"\s+([,.;:!?…])", r"\1", value)
    value = re.sub(r" {2,}", " ", value).strip()
    if re.fullmatch(r"(?i)sao\s*\?+", value):
        value = "[question-ah] Sao?"
    elif re.fullmatch(r"\s*(?:\?!+|!\?+)\s*", value):
        value = "[question-ah] [surprise-ah]"
    return value


def clean_text(value: str) -> str:
    """Compatibility helper used by checks and callers that need final TTS text."""
    return preprocess_tts_tags(clean_original_text(value))


def load_character_indexes(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    characters = data.get("characters") if isinstance(data, dict) else None
    if not isinstance(characters, dict):
        raise ValueError(f"{path} thiếu object characters")
    by_id: dict[str, str] = {}
    by_name: dict[str, str] = {}
    for char_id, entry in characters.items():
        if not isinstance(entry, dict):
            continue
        by_id[char_id] = char_id
        for alias in entry.get("aliases", []):
            if isinstance(alias, str):
                by_id[alias] = char_id
        name = entry.get("name")
        if isinstance(name, str) and name.strip():
            by_name[normalized_name(name)] = char_id
    return by_id, by_name


def resolve_character(speaker: str, by_id: dict[str, str], by_name: dict[str, str]) -> tuple[str, bool]:
    key = snake_key(speaker)
    if key in by_id:
        # Keep the game's own key when it is an alias; the server resolves it.
        return key, True
    if key in by_name:
        return by_name[key], True
    # Preserve a useful game-like key. batch_generate/API will fall back to default.
    return key or "default", False


def convert(
    source: Path,
    output: Path,
    characters_path: Path,
    report_path: Path,
    id_prefix: str,
) -> dict[str, Any]:
    by_id, by_name = load_character_indexes(characters_path)
    unresolved = Counter()
    speaker_counts = Counter()
    tag_counts = Counter()
    empty_text_records: list[int] = []
    malformed_records: list[dict[str, Any]] = []
    written = 0

    output.parent.mkdir(parents=True, exist_ok=True)
    with source.open("r", encoding="utf-8-sig", newline="") as source_file, output.open(
        "w", encoding="utf-8-sig", newline=""
    ) as output_file:
        reader = csv.reader(source_file)
        writer = csv.DictWriter(output_file, fieldnames=["id", "char", "text", "original"])
        writer.writeheader()
        for record_number, columns in enumerate(reader, start=1):
            raw = ",".join(columns).strip()
            if "$@$" not in raw:
                malformed_records.append({"record": record_number, "value": raw[:300]})
                continue
            heading, raw_text = raw.split("$@$", 1)
            match = HEADER.match(heading)
            if not match:
                malformed_records.append({"record": record_number, "value": raw[:300]})
                continue
            speaker = match.group("speaker").strip()
            original = clean_original_text(raw_text)
            if not original:
                empty_text_records.append(record_number)
                continue
            text = preprocess_tts_tags(original)
            tag_counts.update(SUPPORTED_TTS_TAG.findall(text))
            character, resolved = resolve_character(speaker, by_id, by_name)
            speaker_counts[speaker] += 1
            if not resolved:
                unresolved[speaker] += 1
            written += 1
            writer.writerow(
                {
                    "id": f"{id_prefix}{record_number:06d}",
                    "char": character,
                    "text": text,
                    "original": original,
                }
            )

    report = {
        "source": str(source),
        "output": str(output),
        "characters_file": str(characters_path),
        "records_written": written,
        "unique_speakers": len(speaker_counts),
        "resolved_speakers": len(speaker_counts) - len(unresolved),
        "unresolved_speakers": len(unresolved),
        "unresolved_lines": sum(unresolved.values()),
        "inserted_tags": dict(sorted(tag_counts.items())),
        "inserted_tag_total": sum(tag_counts.values()),
        "unresolved": [
            {"speaker": speaker, "suggested_char": snake_key(speaker), "line_count": count}
            for speaker, count in unresolved.most_common()
        ],
        "empty_text_records": empty_text_records,
        "malformed_records": malformed_records,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=BASE_DIR / "hoithoai.csv")
    parser.add_argument("--output", type=Path, default=BASE_DIR / "dialogues.csv")
    parser.add_argument("--characters", type=Path, default=BASE_DIR / "characters.json")
    parser.add_argument("--report", type=Path, default=BASE_DIR / "dialogues.unresolved.json")
    parser.add_argument("--id-prefix", default="dialogue_", help="Prefix for stable sequential IDs")
    args = parser.parse_args()
    report = convert(
        args.input.resolve(),
        args.output.resolve(),
        args.characters.resolve(),
        args.report.resolve(),
        args.id_prefix,
    )
    print(json.dumps({key: value for key, value in report.items() if key != "unresolved"}, ensure_ascii=False, indent=2))
    print(f"Unresolved details: {args.report.resolve()}")
    return 0 if not report["malformed_records"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
