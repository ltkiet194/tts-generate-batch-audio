"""Phân tích giọng và cân bằng alias chỉ từ characters.json + ref_audio.

Chạy thử:
    .venv\Scripts\python.exe rebalance_aliases.py

Áp dụng (tự tạo thêm một bản backup):
    .venv\Scripts\python.exe rebalance_aliases.py --apply
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf


BASE_DIR = Path(__file__).resolve().parent
CHARACTERS_PATH = BASE_DIR / "characters.json"

FEMALE_HINTS = {
    "nu", "nương", "nương_nương", "phu_nhan", "tieu_thu", "co_nuong",
    "su_muoi", "su_thai", "a_hoan", "thieu_nu", "nu_hiep", "ba_ba",
    "ma_ma", "cung_nu", "hoang_hau", "cong_chua", "dao_co",
}
MALE_HINTS = {
    "nam", "ong", "lao", "dai_han", "cong_tu", "thieu_gia", "hoa_thuong",
    "dao_truong", "tuong_quan", "dai_nhan", "cong_cong", "tieu_nhi",
    "bo_khoai", "tieu_dau", "ngu_phu", "thuyen_phu", "son_tac", "hai_tac",
    "bao_cong",
}
ELDER_HINTS = {
    "lao", "truong_lao", "su_thai", "dao_truong", "truong_boi", "ba_ba",
    "ong", "lao_ba", "lao_thai", "cong_cong",
}
YOUNG_HINTS = {
    "nho", "thieu_nu", "thieu_nien", "tieu_hai", "dua_tre", "be",
    "a_hoan", "su_muoi", "tieu_muoi",
}

ROLE_PATTERNS = {
    "religious": ("hoa_thuong", "dao_truong", "su_thai", "ni_co", "tang", "thien_su"),
    "official": ("dai_nhan", "quan_", "bo_khoai", "sai_dich", "cong_cong", "cung_nu", "ve_binh"),
    "military": ("binh_linh", "tuong_quan", "quan_doi", "thu_linh", "dau_muc", "linh_"),
    "outlaw": ("son_tac", "hai_tac", "thuy_phi", "thao_khau", "mao_nhan"),
    "merchant": ("thuong_nhan", "nguoi_ban", "tap_hoa", "chu_quan", "tieu_nhi"),
    "worker": ("tho_", "thuyen_phu", "ngu_phu", "nguoi_lam", "phu_vac"),
    "martial": ("de_tu", "cao_thu", "kiem", "quyen", "bang_", "giao_", "mon_"),
    "noble": ("trang_chu", "dai_nhan", "nương_nương", "cong_chua", "phu_nhan"),
}


def ascii_key(value: str) -> str:
    text = unicodedata.normalize("NFD", value.lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("đ", "d")
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def contains_hint(key: str, hints: set[str]) -> bool:
    padded = f"_{key}_"
    return any(f"_{ascii_key(hint)}_" in padded for hint in hints)


def lexical_gender(key: str, name: str = "") -> tuple[str, float]:
    combined = f"{ascii_key(key)}_{ascii_key(name)}"
    female = contains_hint(combined, FEMALE_HINTS)
    male = contains_hint(combined, MALE_HINTS)
    if female and not male:
        return "female", 0.96
    if male and not female:
        return "male", 0.92
    return "unknown", 0.0


def infer_age(key: str, name: str = "") -> tuple[str, float]:
    combined = f"{ascii_key(key)}_{ascii_key(name)}"
    if contains_hint(combined, ELDER_HINTS):
        return "elder", 0.85
    if contains_hint(combined, YOUNG_HINTS):
        return "young", 0.82
    return "adult", 0.55


def infer_roles(key: str, name: str = "") -> list[str]:
    combined = f"{ascii_key(key)}_{ascii_key(name)}"
    roles = [role for role, patterns in ROLE_PATTERNS.items() if any(p in combined for p in patterns)]
    return roles or ["general"]


def estimate_pitch(samples: np.ndarray, sample_rate: int) -> tuple[float | None, float]:
    """Ước lượng F0 trung vị bằng tự tương quan; đủ để phân nhóm, không phải đo lâm sàng."""
    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    samples = samples.astype(np.float32)
    if sample_rate != 16_000:
        target_len = max(1, round(len(samples) * 16_000 / sample_rate))
        samples = np.interp(
            np.linspace(0, len(samples) - 1, target_len),
            np.arange(len(samples)),
            samples,
        ).astype(np.float32)
        sample_rate = 16_000

    frame_len = int(0.05 * sample_rate)
    hop = int(0.025 * sample_rate)
    min_lag = sample_rate // 350
    max_lag = sample_rate // 65
    pitches: list[float] = []
    strengths: list[float] = []
    global_rms = float(np.sqrt(np.mean(samples * samples))) if len(samples) else 0.0
    threshold = max(global_rms * 0.35, 0.004)

    for start in range(0, max(0, len(samples) - frame_len), hop):
        frame = samples[start:start + frame_len]
        frame = frame - float(frame.mean())
        rms = float(np.sqrt(np.mean(frame * frame)))
        if rms < threshold:
            continue
        frame *= np.hanning(len(frame)).astype(np.float32)
        corr = np.correlate(frame, frame, mode="full")[len(frame) - 1:]
        if corr[0] <= 1e-9:
            continue
        region = corr[min_lag:max_lag + 1]
        lag = int(np.argmax(region)) + min_lag
        strength = float(corr[lag] / corr[0])
        if strength >= 0.30:
            pitches.append(sample_rate / lag)
            strengths.append(strength)

    if not pitches:
        return None, 0.0
    return round(float(np.median(pitches)), 1), round(float(np.median(strengths)), 3)


def analyze_audio(path: Path, ref_text: str) -> dict[str, Any]:
    audio, sample_rate = sf.read(path, always_2d=True, dtype="float32")
    mono = audio.mean(axis=1)
    duration = len(mono) / sample_rate
    rms = float(np.sqrt(np.mean(mono * mono))) if len(mono) else 0.0
    pitch, pitch_strength = estimate_pitch(mono, sample_rate)
    words = len(re.findall(r"\w+", ref_text, flags=re.UNICODE))
    pace = words / duration if duration else 0.0
    return {
        "duration_seconds": round(duration, 2),
        "sample_rate": int(sample_rate),
        "pitch_hz": pitch,
        "pitch_confidence": pitch_strength,
        "tone": "unknown" if pitch is None else ("low" if pitch < 145 else "high" if pitch >= 195 else "medium"),
        "energy_rms": round(rms, 4),
        "pace_words_per_second": round(pace, 2),
        "pace": "slow" if pace < 1.7 else "fast" if pace > 2.8 else "medium",
    }


def build_profile(char_id: str, item: dict[str, Any]) -> dict[str, Any]:
    audio = analyze_audio((BASE_DIR / item["ref_audio"]).resolve(), item.get("ref_text", ""))
    gender, confidence = lexical_gender(char_id, item.get("name", ""))
    alias_votes = Counter(
        vote
        for alias in item.get("aliases", [])
        for vote, vote_confidence in [lexical_gender(alias)]
        if vote != "unknown" and vote_confidence >= 0.8
    )
    vote_total = sum(alias_votes.values())
    if gender == "unknown" and vote_total >= 2:
        winner, winner_count = alias_votes.most_common(1)[0]
        if winner_count / vote_total >= 0.75:
            gender = winner
            confidence = min(0.88, 0.62 + winner_count / max(vote_total, 1) * 0.25)
    audio_gender = "unknown"
    pitch = audio["pitch_hz"]
    if pitch is not None and audio["pitch_confidence"] >= 0.34:
        if pitch <= 175:
            audio_gender = "male"
        elif pitch >= 185:
            audio_gender = "female"
    if gender == "unknown" and audio_gender != "unknown":
        gender, confidence = audio_gender, 0.68
    elif audio_gender == gender:
        confidence = min(0.99, confidence + 0.03)
    elif audio_gender != "unknown" and audio_gender != gender:
        # Tên/vai trò đáng tin cậy hơn F0 đơn giản; hạ độ tin cậy để thể hiện xung đột.
        confidence = max(0.60, confidence - 0.18)
    age, age_confidence = infer_age(char_id, item.get("name", ""))
    return {
        "gender": gender,
        "gender_confidence": round(confidence, 2),
        "gender_alias_votes": dict(alias_votes),
        "age_group": age,
        "age_confidence": round(age_confidence, 2),
        "role_tags": infer_roles(char_id, item.get("name", "")),
        **audio,
        "analysis_method": "name_role_and_reference_audio_v1",
    }


def alias_traits(alias: str, source_profile: dict[str, Any]) -> dict[str, Any]:
    gender, confidence = lexical_gender(alias)
    if gender == "unknown":
        gender = source_profile["gender"]
        confidence = 0.45 if gender != "unknown" else 0.0
    age, age_confidence = infer_age(alias)
    if age_confidence < 0.8:
        age = source_profile["age_group"]
    return {
        "gender": gender,
        "gender_confidence": confidence,
        "age_group": age,
        "roles": infer_roles(alias),
        "source_pitch": source_profile.get("pitch_hz"),
    }


def compatibility_cost(
    traits: dict[str, Any],
    profile: dict[str, Any],
    current_load: int,
    source_id: str,
    target_id: str,
) -> float:
    cost = current_load * 3.5
    if traits["gender"] != "unknown" and profile["gender"] != "unknown":
        if traits["gender"] != profile["gender"]:
            cost += 1_000.0
    if traits["age_group"] != profile["age_group"]:
        cost += 11.0
    alias_roles = set(traits["roles"]) - {"general"}
    target_roles = set(profile["role_tags"]) - {"general"}
    if alias_roles:
        cost += 0.0 if alias_roles & target_roles else 8.0
    source_pitch = traits.get("source_pitch")
    target_pitch = profile.get("pitch_hz")
    if source_pitch and target_pitch:
        cost += min(abs(source_pitch - target_pitch) / 18.0, 8.0)
    if source_id == target_id:
        cost -= 1.0
    return cost


def rebalance(data: dict[str, Any]) -> dict[str, Any]:
    chars: dict[str, dict[str, Any]] = data["characters"]
    profiles: dict[str, dict[str, Any]] = {}
    for char_id, item in chars.items():
        profile = build_profile(char_id, item)
        item["voice_profile"] = profile
        profiles[char_id] = profile

    candidates = [
        char_id for char_id, item in chars.items()
        if char_id != "default" and item.get("enabled", True)
    ]
    original_owner: dict[str, str] = {}
    aliases: list[str] = []
    for char_id, item in chars.items():
        for alias in item.get("aliases", []):
            if alias in original_owner:
                raise ValueError(f"Alias trùng lặp: {alias}")
            original_owner[alias] = char_id
            aliases.append(alias)
        item["aliases"] = []

    assigned: dict[str, list[str]] = {char_id: [] for char_id in candidates}
    pinned: dict[str, str] = {}
    for alias in aliases:
        normalized = alias[4:] if alias.startswith("npc_") else alias
        if normalized in candidates:
            pinned[alias] = normalized
            assigned[normalized].append(alias)

    # Alias rõ giới tính/vai trò được xếp trước, rồi mới đến tên riêng khó nhận diện.
    unpinned = [a for a in aliases if a not in pinned]
    unpinned.sort(
        key=lambda a: (
            -alias_traits(a, profiles[original_owner[a]])["gender_confidence"],
            a,
        )
    )
    for alias in unpinned:
        source_id = original_owner[alias]
        traits = alias_traits(alias, profiles[source_id])
        ranked = sorted(
            candidates,
            key=lambda target: (
                compatibility_cost(
                    traits, profiles[target], len(assigned[target]), source_id, target
                ),
                len(assigned[target]),
                target,
            ),
        )
        assigned[ranked[0]].append(alias)

    for char_id in candidates:
        chars[char_id]["aliases"] = sorted(assigned[char_id])
    chars["default"]["aliases"] = []

    moved = sum(1 for alias, old in original_owner.items() if alias not in pinned and alias not in assigned.get(old, []))
    return {
        "total_aliases": len(aliases),
        "moved_aliases": moved,
        "pinned_aliases": len(pinned),
        "before": dict(Counter(original_owner.values())),
        "after": {char_id: len(chars[char_id].get("aliases", [])) for char_id in chars},
        "gender_counts": dict(Counter(p["gender"] for p in profiles.values() if p)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Ghi kết quả vào characters.json")
    args = parser.parse_args()

    data = json.loads(CHARACTERS_PATH.read_text(encoding="utf-8"))
    report = rebalance(data)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not args.apply:
        print("\nChỉ chạy thử; dùng --apply để lưu.")
        return 0

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = CHARACTERS_PATH.with_name(f"characters.backup-{timestamp}.json")
    shutil.copy2(CHARACTERS_PATH, backup)
    CHARACTERS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report_path = BASE_DIR / "alias_rebalance_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"\nĐã lưu: {CHARACTERS_PATH}")
    print(f"Backup: {backup}")
    print(f"Báo cáo: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
