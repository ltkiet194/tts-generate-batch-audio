"""Strict, thread-safe character configuration loading."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


class CharacterConfigError(ValueError):
    pass


class CharacterNotFoundError(KeyError):
    pass


class CharacterDisabledError(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class Character:
    char: str
    name: str
    ref_audio: Path
    ref_text: str
    language: str
    enabled: bool
    parameters: Mapping[str, Any]
    aliases: tuple[str, ...] = ()

    def public_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"char": self.char, "name": self.name}
        if self.aliases:
            result["aliases"] = list(self.aliases)
        return result


class CharacterManager:
    REQUIRED_FIELDS = {"name", "ref_audio", "ref_text", "language", "enabled", "parameters"}

    def __init__(self, config_path: Path, base_dir: Path) -> None:
        self._config_path = config_path.resolve()
        self._base_dir = base_dir.resolve()
        self._lock = threading.RLock()
        self._characters: Mapping[str, Character] = MappingProxyType({})
        self._aliases: Mapping[str, Character] = MappingProxyType({})

    def load(self, required_default_id: str | None = None) -> int:
        """Validate a complete new snapshot, then swap it atomically."""
        try:
            raw = json.loads(self._config_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise CharacterConfigError(f"Không tìm thấy file cấu hình: {self._config_path}") from exc
        except json.JSONDecodeError as exc:
            raise CharacterConfigError(
                f"JSON không hợp lệ tại dòng {exc.lineno}, cột {exc.colno}: {exc.msg}"
            ) from exc

        if not isinstance(raw, dict) or not isinstance(raw.get("characters"), dict):
            raise CharacterConfigError("Trường gốc 'characters' phải là một object JSON.")

        loaded: dict[str, Character] = {}
        errors: list[str] = []
        for char_id, item in raw["characters"].items():
            try:
                loaded[char_id] = self._parse_character(char_id, item)
            except CharacterConfigError as exc:
                errors.append(str(exc))

        aliases: dict[str, Character] = {}
        canonical_ids = set(loaded)
        for character in loaded.values():
            for alias in character.aliases:
                if alias in canonical_ids:
                    errors.append(
                        f"characters.{character.char}.aliases: '{alias}' trùng với mã nhân vật chính."
                    )
                elif alias in aliases:
                    errors.append(
                        f"characters.{character.char}.aliases: '{alias}' đã thuộc về "
                        f"'{aliases[alias].char}'."
                    )
                else:
                    aliases[alias] = character
        if errors:
            raise CharacterConfigError("Cấu hình nhân vật không hợp lệ:\n- " + "\n- ".join(errors))

        if required_default_id is not None:
            default_character = loaded.get(required_default_id)
            if default_character is None:
                raise CharacterConfigError(
                    f"Thiếu nhân vật mặc định bắt buộc: characters.{required_default_id}."
                )
            if not default_character.enabled:
                raise CharacterConfigError(
                    f"Nhân vật mặc định '{required_default_id}' phải có enabled: true."
                )

        with self._lock:
            self._characters = MappingProxyType(loaded)
            self._aliases = MappingProxyType(aliases)
        return len(loaded)

    def _parse_character(self, char_id: Any, item: Any) -> Character:
        prefix = f"characters.{char_id}"
        if not isinstance(char_id, str) or not char_id.strip():
            raise CharacterConfigError("Mỗi char phải là một khóa chuỗi không rỗng.")
        if not isinstance(item, dict):
            raise CharacterConfigError(f"{prefix} phải là một object.")
        missing = self.REQUIRED_FIELDS - item.keys()
        if missing:
            raise CharacterConfigError(f"{prefix} thiếu trường: {', '.join(sorted(missing))}.")
        if not isinstance(item["name"], str) or not item["name"].strip():
            raise CharacterConfigError(f"{prefix}.name phải là chuỗi không rỗng.")
        if not isinstance(item["ref_text"], str) or not item["ref_text"].strip():
            raise CharacterConfigError(f"{prefix}.ref_text phải là bản chép lời không rỗng.")
        if not isinstance(item["language"], str) or not item["language"].strip():
            raise CharacterConfigError(f"{prefix}.language phải là chuỗi không rỗng.")
        if not isinstance(item["enabled"], bool):
            raise CharacterConfigError(f"{prefix}.enabled phải là true hoặc false.")
        if not isinstance(item["parameters"], dict):
            raise CharacterConfigError(f"{prefix}.parameters phải là một object.")
        if not isinstance(item["ref_audio"], str) or not item["ref_audio"].strip():
            raise CharacterConfigError(f"{prefix}.ref_audio phải là đường dẫn không rỗng.")

        aliases_raw = item.get("aliases", [])
        if not isinstance(aliases_raw, list):
            raise CharacterConfigError(f"{prefix}.aliases phải là một mảng chuỗi.")
        aliases: list[str] = []
        for index, alias in enumerate(aliases_raw):
            if not isinstance(alias, str) or not alias.strip():
                raise CharacterConfigError(f"{prefix}.aliases[{index}] phải là chuỗi không rỗng.")
            normalized_alias = alias.strip()
            if normalized_alias == char_id:
                raise CharacterConfigError(f"{prefix}.aliases không cần lặp lại mã chính '{char_id}'.")
            if normalized_alias in aliases:
                raise CharacterConfigError(f"{prefix}.aliases chứa mã trùng lặp '{normalized_alias}'.")
            aliases.append(normalized_alias)

        ref_audio = (self._base_dir / item["ref_audio"]).resolve()
        try:
            ref_audio.relative_to(self._base_dir)
        except ValueError as exc:
            raise CharacterConfigError(f"{prefix}.ref_audio không được nằm ngoài thư mục tts_bridge.") from exc
        if not ref_audio.is_file():
            raise CharacterConfigError(f"{prefix}.ref_audio không tồn tại: {ref_audio}")

        reserved = {"text", "language", "ref_audio", "ref_text"}.intersection(item["parameters"])
        if reserved:
            raise CharacterConfigError(
                f"{prefix}.parameters không được ghi đè: {', '.join(sorted(reserved))}."
            )
        return Character(
            char=char_id,
            name=item["name"].strip(),
            ref_audio=ref_audio,
            ref_text=item["ref_text"].strip(),
            language=item["language"].strip(),
            enabled=item["enabled"],
            parameters=MappingProxyType(dict(item["parameters"])),
            aliases=tuple(aliases),
        )

    def get(self, char_id: str) -> Character:
        with self._lock:
            character = self._characters.get(char_id) or self._aliases.get(char_id)
        if character is None:
            raise CharacterNotFoundError(char_id)
        if not character.enabled:
            raise CharacterDisabledError(char_id)
        return character

    def resolve(self, char_id: str, default_id: str = "default") -> tuple[Character, bool]:
        """Resolve an ID/alias, falling back for unknown or disabled characters."""
        try:
            return self.get(char_id), False
        except (CharacterNotFoundError, CharacterDisabledError):
            if char_id == default_id:
                raise
            return self.get(default_id), True

    def list_public(self) -> list[dict[str, Any]]:
        with self._lock:
            snapshot = tuple(self._characters.values())
        return [character.public_dict() for character in snapshot if character.enabled]
