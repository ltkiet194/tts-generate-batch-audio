"""Single-load OmniVoice inference engine."""

from __future__ import annotations

import logging
import threading
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import numpy as np

from character_manager import Character

logger = logging.getLogger(__name__)


class TTSEngine:
    def __init__(
        self,
        model_id: str,
        device: str = "auto",
        model_sample_rate: int = 24000,
        output_sample_rate: int = 24000,
        model_cache_dir: str | None = None,
        model_revision: str = "main",
    ) -> None:
        self.model_id = model_id
        self.requested_device = device
        self.model_sample_rate = model_sample_rate
        self.output_sample_rate = output_sample_rate
        self.model_cache_dir = model_cache_dir
        self.model_revision = model_revision
        self.device = "not-loaded"
        self._model: Any = None
        self._inference_lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    @property
    def model_identity(self) -> str:
        try:
            library_version = version("omnivoice")
        except PackageNotFoundError:
            library_version = "unknown"
        return f"{self.model_id}@{self.model_revision}|omnivoice={library_version}"

    def load(self) -> None:
        if self._model is not None:
            return
        import torch
        from omnivoice import OmniVoice

        if self.requested_device == "auto":
            self.device = "cuda:0" if torch.cuda.is_available() else "cpu"
        else:
            self.device = self.requested_device
        if self.device.startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError(f"Đã yêu cầu {self.device}, nhưng PyTorch không phát hiện CUDA.")

        dtype = torch.float16 if self.device.startswith("cuda") else torch.float32
        logger.info("Loading model %s on %s (%s)", self.model_id, self.device, dtype)
        load_kwargs: dict[str, Any] = {
            "device_map": self.device,
            "dtype": dtype,
            "revision": self.model_revision,
        }
        if self.model_cache_dir:
            load_kwargs["cache_dir"] = self.model_cache_dir
        self._model = OmniVoice.from_pretrained(self.model_id, **load_kwargs)
        self._model.eval()
        logger.info("TTS model loaded")

    def generate(self, text: str, character: Character) -> np.ndarray:
        if self._model is None:
            raise RuntimeError("Model TTS chưa được tải.")
        kwargs = dict(character.parameters)
        kwargs.update(
            text=text,
            language=character.language,
            ref_audio=str(character.ref_audio),
            ref_text=character.ref_text,
        )
        # Most consumer GPUs cannot safely run concurrent generations on one model.
        with self._inference_lock:
            result = self._model.generate(**kwargs)
        return self._normalize_audio_result(result)

    def generate_voice_design(
        self,
        text: str,
        instruct: str,
        language: str = "Vietnamese",
    ) -> np.ndarray:
        """Generate a speaker from descriptive attributes without reference audio."""
        if self._model is None:
            raise RuntimeError("Model TTS chưa được tải.")
        with self._inference_lock:
            result = self._model.generate(
                text=text,
                instruct=instruct,
                language=language,
            )
        return self._normalize_audio_result(result)

    def _normalize_audio_result(self, result: Any) -> np.ndarray:
        if not isinstance(result, (list, tuple)) or not result:
            raise RuntimeError("Model trả về kết quả audio không hợp lệ.")
        audio = np.asarray(result[0], dtype=np.float32).squeeze()
        if audio.ndim != 1 or audio.size == 0:
            raise RuntimeError(f"Model trả về mảng audio có shape không hợp lệ: {audio.shape}")
        if self.model_sample_rate != self.output_sample_rate:
            audio = self._resample(audio, self.model_sample_rate, self.output_sample_rate)
        return audio

    @staticmethod
    def _resample(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
        """Dependency-light linear resampling; never relabel samples as another rate."""
        if source_rate <= 0 or target_rate <= 0:
            raise ValueError("Sample rate phải lớn hơn 0.")
        target_length = max(1, round(audio.size * target_rate / source_rate))
        old_positions = np.linspace(0.0, 1.0, num=audio.size, endpoint=True)
        new_positions = np.linspace(0.0, 1.0, num=target_length, endpoint=True)
        return np.interp(new_positions, old_positions, audio).astype(np.float32)

    def close(self) -> None:
        model, self._model = self._model, None
        if model is not None:
            del model
        try:
            import torch

            if self.device.startswith("cuda") and torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
