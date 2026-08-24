from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, UploadFile

from transcriber import Transcriber

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("dictation")

transcriber = Transcriber(model_size=os.environ.get("DICT_MODEL", "medium"),
                          device=os.environ.get("DICT_DEVICE", "auto"),
                          language=os.environ.get("DICT_LANG", "ru") or None)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # прогрев: грузим модель в GPU на старте, чтобы первый запрос был быстрым
    await asyncio.to_thread(transcriber._ensure_model)
    log.info("warm: device=%s", transcriber.device)
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "device": transcriber.device}


@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...)) -> dict:
    data = await file.read()
    suffix = os.path.splitext(file.filename or "a.wav")[1] or ".wav"
    fd, path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        t0 = time.time()
        text = await transcriber.transcribe(path)
        ms = int((time.time() - t0) * 1000)
        log.info("transcribe: %d ms, %d chars", ms, len(text))
        return {"text": text, "ms": ms}
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
