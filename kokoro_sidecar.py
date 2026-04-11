"""
Kokoro TTS Sidecar — single-process FastAPI service.
Loads Kokoro + spaCy + ONNX once. Max 2 concurrent inferences via semaphore.
XelaSphere workers call this over HTTP instead of loading models in-process.
"""
import asyncio
import io
import logging
import wave
import time

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from kokoro import KPipeline

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("kokoro-sidecar")

app = FastAPI()
semaphore = asyncio.Semaphore(2)

# ── Lazy-loaded pipelines (one per lang_code) ──
_pipelines = {}

LANG_CODES = {'a', 'b', 'e', 'f', 'h', 'i', 'j', 'z'}


def _get_pipeline(lang_code: str) -> KPipeline:
    """Get or create a KPipeline for the given language code."""
    if lang_code not in _pipelines:
        if lang_code not in LANG_CODES:
            lang_code = 'a'
        logger.info(f"Loading KPipeline for lang={lang_code}...")
        _pipelines[lang_code] = KPipeline(lang_code=lang_code, repo_id='hexgrad/Kokoro-82M')
        logger.info(f"KPipeline lang={lang_code} ready.")
    return _pipelines[lang_code]


class TTSRequest(BaseModel):
    text: str
    voice: str = "af_heart"
    speed: float = 1.0
    lang: str = "a"


def _run_tts(text: str, voice: str, speed: float, lang: str) -> bytes:
    """Run Kokoro inference synchronously. Called via run_in_executor."""
    pipeline = _get_pipeline(lang)

    if len(text) > 8000:
        text = text[:8000]

    start = time.time()
    audio_chunks = []
    for _, _, audio in pipeline(text, voice=voice, speed=speed):
        if audio is not None:
            audio_chunks.append(audio)

    if not audio_chunks:
        raise ValueError("No audio generated")

    combined = np.concatenate(audio_chunks)
    elapsed = time.time() - start
    duration = len(combined) / 24000

    logger.info(
        f"TTS lang={lang} voice={voice} speed={speed:.2f} "
        f"{len(text)}ch -> {duration:.1f}s audio in {elapsed:.1f}s "
        f"(RTF {elapsed / max(duration, 0.1):.2f}x)"
    )

    # Convert float32 -> 16-bit PCM WAV
    pcm_16 = (np.clip(combined, -1, 1) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(pcm_16.tobytes())

    return buf.getvalue()


@app.post("/synthesize")
async def synthesize(req: TTSRequest):
    async with semaphore:
        loop = asyncio.get_event_loop()
        try:
            audio_bytes = await loop.run_in_executor(
                None, _run_tts, req.text, req.voice, req.speed, req.lang
            )
            return Response(content=audio_bytes, media_type="audio/wav")
        except Exception as e:
            logger.error(f"TTS failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    return {"status": "ok"}


# Pre-load English pipeline at startup
logger.info("Pre-loading English pipeline...")
_get_pipeline('a')
logger.info("Sidecar ready.")
