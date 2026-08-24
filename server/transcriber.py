from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


def _preload_cuda_libs() -> None:
    """ctranslate2 ищет cuBLAS/cuDNN через dlopen, а в WSL их нет в системных путях
    (libcublas.so.12 not found). Зато они есть в pip-пакетах nvidia-*. Подгружаем их
    заранее с RTLD_GLOBAL, чтобы ct2 нашёл символы — без правки systemd/LD_LIBRARY_PATH.
    Best-effort: пакетов нет → молча уходим (Transcriber всё равно сработает на CPU)."""
    import ctypes
    import glob
    import os
    for mod in ("nvidia.cublas.lib", "nvidia.cudnn.lib"):  # cublas раньше cudnn
        try:
            pkg = __import__(mod, fromlist=["*"])
            libdir = list(pkg.__path__)[0]  # namespace-пакет: путь в __path__, не __file__
        except Exception:  # noqa: BLE001
            continue
        for so in sorted(glob.glob(os.path.join(libdir, "*.so*"))):
            try:
                ctypes.CDLL(so, mode=ctypes.RTLD_GLOBAL)
            except OSError:
                pass


_preload_cuda_libs()

try:  # faster-whisper нужен только на сервере; тесты мокают WhisperModel
    from faster_whisper import WhisperModel
except ImportError:  # pragma: no cover
    WhisperModel = None  # type: ignore[assignment, misc]


class Transcriber:
    """Локальное распознавание речи (faster-whisper). Ленивая загрузка модели,
    GPU с авто-фолбэком на CPU. Любая ошибка распознавания → пустая строка:
    вызывающая сторона получает "ничего не распознал", а не исключение."""

    def __init__(self, model_size: str = "medium", device: str = "auto",
                 language: str | None = "ru") -> None:
        self.model_size = model_size
        self.language = language  # None → whisper определит язык сам
        self._requested_device = device
        self._model: WhisperModel | None = None
        self.device: str | None = None

    def _load_cpu(self) -> WhisperModel:
        self._model = WhisperModel(self.model_size, device="cpu",
                                   compute_type="int8")
        self.device = "cpu"
        logger.info("STT: модель %s загружена на cpu", self.model_size)
        return self._model

    def _ensure_model(self) -> WhisperModel:
        if self._model is not None:
            return self._model
        if self._requested_device in ("auto", "cuda"):
            try:
                # int8_float16 — ~1.5 ГБ VRAM, точность почти как у float16.
                # Так модель умещается рядом с другой моделью на карте 12 ГБ.
                self._model = WhisperModel(self.model_size, device="cuda",
                                           compute_type="int8_float16")
                self.device = "cuda"
                logger.info("STT: модель %s загружена на cuda", self.model_size)
                return self._model
            except Exception as exc:  # noqa: BLE001
                logger.warning("STT: CUDA недоступна (%s), переключаюсь на CPU", exc)
        return self._load_cpu()

    def _run(self, model: WhisperModel, audio_path: str) -> str:
        segments, _ = model.transcribe(audio_path, language=self.language,
                                       beam_size=5)
        return " ".join(s.text.strip() for s in segments).strip()

    def _transcribe_sync(self, audio_path: str) -> str:
        try:
            return self._run(self._ensure_model(), audio_path)
        except Exception as exc:  # noqa: BLE001
            # модель создалась на cuda, но инференс упал (нет cuBLAS/cuDNN в WSL) —
            # один раз пересобираем на CPU и повторяем; дальше работаем на CPU.
            if self.device == "cuda":
                logger.warning("STT: инференс на cuda не удался (%s), пересобираю "
                               "на CPU", exc)
                try:
                    return self._run(self._load_cpu(), audio_path)
                except Exception as exc2:  # noqa: BLE001
                    logger.warning("STT: распознавание не удалось: %s", exc2)
                    return ""
            logger.warning("STT: распознавание не удалось: %s", exc)
            return ""

    async def transcribe(self, audio_path: str) -> str:
        return await asyncio.to_thread(self._transcribe_sync, audio_path)
