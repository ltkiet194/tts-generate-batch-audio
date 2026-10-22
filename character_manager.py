"""Strict, thread-safe character configuration loading."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import soundfile as sf


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
    speed: float = 1.0

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
        speed = item.get("speed", 1.0)
        try:
            speed_value = float(speed)
        except (TypeError, ValueError, OverflowError):
            speed_value = math.nan
        if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not math.isfinite(speed_value) or speed_value <= 0:
            raise CharacterConfigError(f"{prefix}.speed must be a finite number greater than 0 (default 1.0).")
        speed = speed_value
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

        reserved = {"text", "language", "ref_audio", "ref_text", "speed"}.intersection(item["parameters"])
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
            speed=float(speed),
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

    def list_voice_design_aliases(self) -> list[dict[str, str]]:
        """Return selectable aliases, never canonical character IDs."""
        with self._lock:
            result = [
                {"alias": alias, "char": character.char, "name": character.name}
                for alias, character in self._aliases.items()
                if character.enabled
            ]
        return sorted(result, key=lambda item: item["alias"])

    def list_management(self) -> list[dict[str, Any]]:
        """Return only fields needed by the local alias-management UI."""
        with self._lock:
            snapshot = tuple(self._characters.values())
        return [
            {
                "char": item.char,
                "name": item.name,
                "enabled": item.enabled,
                "aliases": list(item.aliases),
                "speed": item.speed,
            }
            for item in snapshot
        ]

    def move_aliases(
        self,
        *,
        aliases: list[str],
        source_char: str,
        target_char: str,
        required_default_id: str,
    ) -> Character:
        """Move a batch of aliases and atomically persist the configuration."""
        aliases = [item.strip() for item in aliases]
        source_char, target_char = source_char.strip(), target_char.strip()
        if not aliases or any(not item for item in aliases) or not source_char or not target_char:
            raise CharacterConfigError("Pháº£i chá»n alias, char nguá»“n vÃ  char Ä‘Ã­ch.")
        if len(set(aliases)) != len(aliases):
            raise CharacterConfigError("Danh sÃ¡ch alias cÃ³ mÃ£ trÃ¹ng láº·p.")
        if source_char == target_char:
            raise CharacterConfigError("Char nguá»“n vÃ  Ä‘Ã­ch pháº£i khÃ¡c nhau.")

        with self._lock:
            source = self._characters.get(source_char)
            target = self._characters.get(target_char)
            if source is None:
                raise CharacterConfigError(f"KhÃ´ng tÃ¬m tháº¥y char nguá»“n '{source_char}'.")
            if target is None:
                raise CharacterConfigError(f"KhÃ´ng tÃ¬m tháº¥y char Ä‘Ã­ch '{target_char}'.")
            if not target.enabled:
                raise CharacterConfigError(f"Char Ä‘Ã­ch '{target_char}' Ä‘ang bá»‹ táº¯t.")
            for alias in aliases:
                if alias in self._characters:
                    raise CharacterConfigError(f"'{alias}' lÃ  char chÃ­nh, khÃ´ng thá»ƒ chuyá»ƒn nhÆ° alias.")
                owner = self._aliases.get(alias)
                if owner is None or owner.char != source_char:
                    raise CharacterConfigError(f"Alias '{alias}' khÃ´ng thuá»™c char nguá»“n '{source_char}'.")

            try:
                data = json.loads(self._config_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise CharacterConfigError(f"KhÃ´ng Ä‘á»c Ä‘Æ°á»£c characters.json: {exc}") from exc
            raw_characters = data.get("characters") if isinstance(data, dict) else None
            if not isinstance(raw_characters, dict):
                raise CharacterConfigError("TrÆ°á»ng gá»‘c 'characters' pháº£i lÃ  má»™t object JSON.")
            source_entry = raw_characters.get(source_char)
            target_entry = raw_characters.get(target_char)
            if not isinstance(source_entry, dict) or not isinstance(target_entry, dict):
                raise CharacterConfigError("Cáº¥u hÃ¬nh char nguá»“n/Ä‘Ã­ch khÃ´ng há»£p lá»‡.")

            source_aliases = list(source_entry.get("aliases", []))
            target_aliases = list(target_entry.get("aliases", []))
            missing = [alias for alias in aliases if alias not in source_aliases]
            if missing:
                raise CharacterConfigError(
                    f"characters.json Ä‘Ã£ thay Ä‘á»•i; alias khÃ´ng cÃ²n thuá»™c '{source_char}': {', '.join(missing)}. HÃ£y táº£i láº¡i trang."
                )
            conflicts = [alias for alias in aliases if alias in target_aliases]
            if conflicts:
                raise CharacterConfigError(f"Alias Ä‘Ã£ cÃ³ trong char Ä‘Ã­ch: {', '.join(conflicts)}.")
            source_entry["aliases"] = [value for value in source_aliases if value not in set(aliases)]
            target_entry["aliases"] = [*target_aliases, *aliases]
            if not source_entry["aliases"]:
                source_entry.pop("aliases")

            fd, staged_name = tempfile.mkstemp(
                prefix=f".{self._config_path.name}-", suffix=".tmp", dir=self._config_path.parent
            )
            os.close(fd)
            staged = Path(staged_name)
            original = self._config_path.read_bytes()
            replaced = False
            try:
                staged.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                candidate = CharacterManager(staged, self._base_dir)
                candidate.load(required_default_id=required_default_id)
                os.replace(staged, self._config_path)
                replaced = True
                self.load(required_default_id=required_default_id)
            except Exception:
                if replaced:
                    restore_fd, restore_name = tempfile.mkstemp(
                        prefix=f".{self._config_path.name}-restore-", suffix=".tmp", dir=self._config_path.parent
                    )
                    os.close(restore_fd)
                    restore = Path(restore_name)
                    try:
                        restore.write_bytes(original)
                        os.replace(restore, self._config_path)
                        self.load(required_default_id=required_default_id)
                    finally:
                        restore.unlink(missing_ok=True)
                raise
            finally:
                staged.unlink(missing_ok=True)
            return self._characters[target_char]

    def create_character_from_audio(
        self,
        *,
        char_id: str,
        name: str,
        audio_source: Path,
        ref_text: str,
        aliases: list[str],
        speed: float = 1.0,
        language: str = "Vietnamese",
        required_default_id: str,
    ) -> Character:
        """Create a character from an uploaded reference WAV and optionally claim aliases."""
        char_id, name, ref_text, language = char_id.strip(), name.strip(), ref_text.strip(), language.strip()
        aliases = [alias.strip() for alias in aliases]
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,127}", char_id):
            raise CharacterConfigError("Mã char chỉ được gồm chữ thường, số, '_' và '-'.")
        if not name or not ref_text or not language:
            raise CharacterConfigError("Tên, ref_text và language không được để trống.")
        try:
            speed_value = float(speed)
        except (TypeError, ValueError, OverflowError):
            speed_value = math.nan
        if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not math.isfinite(speed_value) or speed_value <= 0:
            raise CharacterConfigError("speed phải là số hữu hạn lớn hơn 0.")
        if len(set(aliases)) != len(aliases) or any(not alias for alias in aliases):
            raise CharacterConfigError("Danh sách alias có giá trị rỗng hoặc trùng lặp.")
        if not audio_source.is_file():
            raise CharacterConfigError("Không tìm thấy file audio tham chiếu đã upload.")
        try:
            audio_info = sf.info(audio_source)
        except Exception as exc:
            raise CharacterConfigError(f"Audio tham chiếu không đọc được: {exc}") from exc
        if audio_info.frames <= 0 or audio_info.samplerate <= 0:
            raise CharacterConfigError("Audio tham chiếu rỗng hoặc không hợp lệ.")

        with self._lock:
            if char_id in self._characters:
                raise CharacterConfigError(f"Char chính '{char_id}' đã tồn tại; không ghi đè.")
            if char_id in self._aliases:
                raise CharacterConfigError(f"'{char_id}' hiện là alias; không thể dùng làm mã char chính.")
            owners: dict[str, Character] = {}
            for alias in aliases:
                owner = self._aliases.get(alias)
                if owner is None or not owner.enabled:
                    raise CharacterConfigError(f"Alias '{alias}' không còn khả dụng để chuyển.")
                owners[alias] = owner

            try:
                data = json.loads(self._config_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise CharacterConfigError(f"Không đọc được characters.json: {exc}") from exc
            raw_characters = data.get("characters") if isinstance(data, dict) else None
            if not isinstance(raw_characters, dict):
                raise CharacterConfigError("Trường gốc 'characters' phải là một object JSON.")

            for owner_id in {owner.char for owner in owners.values()}:
                raw_owner = raw_characters.get(owner_id)
                if not isinstance(raw_owner, dict):
                    raise CharacterConfigError(f"characters.{owner_id} không hợp lệ.")
                owner_aliases = raw_owner.get("aliases", [])
                if not isinstance(owner_aliases, list) or any(not isinstance(value, str) for value in owner_aliases):
                    raise CharacterConfigError(f"characters.{owner_id}.aliases không hợp lệ; hãy sửa JSON trước.")
                selected_for_owner = [alias for alias in aliases if owners[alias].char == owner_id]
                missing = [alias for alias in selected_for_owner if alias not in owner_aliases]
                if missing:
                    raise CharacterConfigError(
                        f"characters.json đã thay đổi; alias không còn thuộc '{owner_id}': {', '.join(missing)}. Tải lại danh sách."
                    )
                remaining = [alias for alias in owner_aliases if alias not in selected_for_owner]
                if remaining:
                    raw_owner["aliases"] = remaining
                else:
                    raw_owner.pop("aliases", None)

            audio_dir = self._base_dir / "audio"
            audio_dir.mkdir(parents=True, exist_ok=True)
            target_audio = audio_dir / f"{char_id}.wav"
            if target_audio.exists():
                raise CharacterConfigError(f"File audio đã tồn tại, không ghi đè: {target_audio.name}")
            entry: dict[str, Any] = {
                "name": name,
                "ref_audio": target_audio.relative_to(self._base_dir).as_posix(),
                "ref_text": ref_text,
                "language": language,
                "enabled": True,
                "speed": speed_value,
                "parameters": {},
            }
            if aliases:
                entry["aliases"] = aliases
            raw_characters[char_id] = entry

            audio_fd, audio_name = tempfile.mkstemp(prefix=f".{char_id}-", suffix=".wav", dir=audio_dir)
            os.close(audio_fd)
            staged_audio = Path(audio_name)
            json_fd, json_name = tempfile.mkstemp(
                prefix=f".{self._config_path.name}-", suffix=".tmp", dir=self._config_path.parent
            )
            os.close(json_fd)
            staged_json = Path(json_name)
            audio_installed = False
            json_installed = False
            original_json = self._config_path.read_bytes()
            try:
                shutil.copyfile(audio_source, staged_audio)
                staged_json.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                os.replace(staged_audio, target_audio)
                audio_installed = True
                candidate = CharacterManager(staged_json, self._base_dir)
                candidate.load(required_default_id=required_default_id)
                os.replace(staged_json, self._config_path)
                json_installed = True
                self.load(required_default_id=required_default_id)
            except Exception:
                if json_installed:
                    restore_fd, restore_name = tempfile.mkstemp(
                        prefix=f".{self._config_path.name}-restore-", suffix=".tmp", dir=self._config_path.parent
                    )
                    os.close(restore_fd)
                    restore = Path(restore_name)
                    try:
                        restore.write_bytes(original_json)
                        os.replace(restore, self._config_path)
                        self.load(required_default_id=required_default_id)
                    finally:
                        restore.unlink(missing_ok=True)
                if audio_installed:
                    target_audio.unlink(missing_ok=True)
                raise
            finally:
                staged_audio.unlink(missing_ok=True)
                staged_json.unlink(missing_ok=True)
            return self._characters[char_id]

    def promote_aliases_to_character(
        self,
        *,
        primary_alias: str,
        secondary_aliases: list[str],
        name: str,
        audio_source: Path,
        ref_text: str,
        language: str,
        required_default_id: str,
    ) -> Character:
        """Create a new character from an existing alias and reassign selected aliases.

        The WAV and JSON are prepared and validated before the JSON snapshot is
        switched. Canonical IDs can never be promoted or overwritten.
        """
        primary_alias = primary_alias.strip()
        name = name.strip()
        ref_text = ref_text.strip()
        language = language.strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,127}", primary_alias):
            raise CharacterConfigError("Mã alias chính chỉ được gồm chữ thường, số, '_' và '-'.")
        if not name or not ref_text or not language:
            raise CharacterConfigError("Tên, ref_text và language không được để trống.")
        if not audio_source.is_file():
            raise CharacterConfigError("Không tìm thấy audio preview để lưu.")
        try:
            audio_info = sf.info(audio_source)
        except Exception as exc:
            raise CharacterConfigError(f"Audio preview không đọc được: {exc}") from exc
        if audio_info.frames <= 0 or audio_info.samplerate <= 0:
            raise CharacterConfigError("Audio preview WAV rỗng hoặc không hợp lệ.")

        secondary = [item.strip() for item in secondary_aliases]
        if len(set(secondary)) != len(secondary):
            raise CharacterConfigError("Danh sách alias phụ có mã trùng nhau.")
        selected = [primary_alias, *secondary]
        if primary_alias in secondary:
            raise CharacterConfigError("Alias chính không được lặp trong danh sách alias phụ.")

        # The lock covers both owner verification and file replacement so two
        # simultaneous saves cannot claim or remove the same aliases.
        with self._lock:
            if primary_alias in self._characters:
                raise CharacterConfigError(
                    f"'{primary_alias}' đã là char chính; char chính hiện có không thể ghi đè."
                )
            owners: dict[str, Character] = {}
            for alias in selected:
                owner = self._aliases.get(alias)
                if owner is None:
                    raise CharacterConfigError(f"'{alias}' không còn là alias có thể chuyển.")
                if not owner.enabled:
                    raise CharacterConfigError(f"Nhân vật sở hữu alias '{alias}' đang bị tắt.")
                owners[alias] = owner

            try:
                data = json.loads(self._config_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise CharacterConfigError(f"Không đọc được characters.json: {exc}") from exc
            raw_characters = data.get("characters") if isinstance(data, dict) else None
            if not isinstance(raw_characters, dict):
                raise CharacterConfigError("Trường gốc 'characters' phải là một object JSON.")

            for owner_id in {owner.char for owner in owners.values()}:
                raw_entry = raw_characters.get(owner_id)
                if not isinstance(raw_entry, dict):
                    raise CharacterConfigError(f"characters.{owner_id} không hợp lệ.")
                old_aliases = raw_entry.get("aliases", [])
                raw_entry["aliases"] = [alias for alias in old_aliases if alias not in selected]
                if not raw_entry["aliases"]:
                    raw_entry.pop("aliases")

            audio_dir = self._base_dir / "audio"
            audio_dir.mkdir(parents=True, exist_ok=True)
            target_audio = audio_dir / f"{primary_alias}.wav"
            if target_audio.exists():
                raise CharacterConfigError(f"File audio đã tồn tại, không ghi đè: {target_audio.name}")
            relative_audio = target_audio.relative_to(self._base_dir).as_posix()
            raw_characters[primary_alias] = {
                "name": name,
                "ref_audio": relative_audio,
                "ref_text": ref_text,
                "language": language,
                "enabled": True,
                "parameters": {},
            }
            if secondary:
                raw_characters[primary_alias]["aliases"] = secondary

            audio_fd, staged_audio_name = tempfile.mkstemp(
                prefix=f".{primary_alias}-", suffix=".wav", dir=audio_dir
            )
            os.close(audio_fd)
            staged_audio = Path(staged_audio_name)
            json_fd, staged_json_name = tempfile.mkstemp(
                prefix=f".{self._config_path.name}-", suffix=".tmp", dir=self._config_path.parent
            )
            os.close(json_fd)
            staged_json = Path(staged_json_name)
            audio_installed = False
            json_installed = False
            original_json = self._config_path.read_bytes()
            try:
                shutil.copyfile(audio_source, staged_audio)
                staged_json.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                os.replace(staged_audio, target_audio)
                audio_installed = True

                candidate = CharacterManager(staged_json, self._base_dir)
                candidate.load(required_default_id=required_default_id)
                os.replace(staged_json, self._config_path)
                json_installed = True
                self.load(required_default_id=required_default_id)
            except Exception:
                if json_installed:
                    restore_fd, restore_name = tempfile.mkstemp(
                        prefix=f".{self._config_path.name}-restore-",
                        suffix=".tmp",
                        dir=self._config_path.parent,
                    )
                    os.close(restore_fd)
                    restore_path = Path(restore_name)
                    try:
                        restore_path.write_bytes(original_json)
                        os.replace(restore_path, self._config_path)
                        self.load(required_default_id=required_default_id)
                    finally:
                        restore_path.unlink(missing_ok=True)
                if audio_installed:
                    target_audio.unlink(missing_ok=True)
                raise
            finally:
                staged_audio.unlink(missing_ok=True)
                staged_json.unlink(missing_ok=True)

            saved = self._characters[primary_alias]
            return saved
