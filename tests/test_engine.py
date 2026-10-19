import numpy as np

from tts_engine import TTSEngine


def test_resampling_changes_sample_count() -> None:
    audio = np.linspace(-1, 1, 24000, dtype=np.float32)
    converted = TTSEngine._resample(audio, 24000, 16000)
    assert converted.dtype == np.float32
    assert converted.shape == (16000,)
