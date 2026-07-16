from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import tempfile
from typing import Any

import requests

from config.settings import Config

logger = logging.getLogger(__name__)


def _api_key() -> str:
    return str(getattr(Config, "GNANI_API_KEY_ID", "") or getattr(Config, "GNANI_API_KEY", "") or "").strip()


def _base_url() -> str:
    return str(getattr(Config, "GNANI_BASE_URL", "https://api.vachana.ai") or "https://api.vachana.ai").rstrip("/")


def _stt_url() -> str:
    override = str(getattr(Config, "GNANI_STT_URL", "") or "").strip()
    if override:
        return override
    return f"{_base_url()}/stt/v3"


def _tts_url() -> str:
    override = str(getattr(Config, "GNANI_TTS_URL", "") or "").strip()
    if override:
        return override
    return f"{_base_url()}/api/v1/tts/inference"


def _headers(content_type: str | None = None) -> dict[str, str]:
    headers = {"X-API-Key-ID": _api_key()}
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _normalize_language(lang: str) -> str:
    lang = (lang or "").strip()
    return lang or "en-IN"


def _normalize_audio(audio_bytes: bytes) -> tuple[bytes, str, str]:
    if not audio_bytes:
        return b"", "recording.wav", "audio/wav"
    if audio_bytes.startswith(b"RIFF"):
        return audio_bytes, "recording.wav", "audio/wav"

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".webm") as temp_in:
            temp_in.write(audio_bytes)
            temp_in_path = temp_in.name

        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_out:
            temp_out_path = temp_out.name

        # Convert to PCM 16-bit 16kHz WAV
        subprocess.run(
            ["ffmpeg", "-y", "-i", temp_in_path, "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", temp_out_path],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        with open(temp_out_path, "rb") as f:
            wav_bytes = f.read()

        os.remove(temp_in_path)
        os.remove(temp_out_path)

        return wav_bytes, "recording.wav", "audio/wav"
    except Exception as exc:
        logger.warning(f"Audio conversion failed: {exc}. Spoofing as ogg.")
        # Bypass ffmpeg conversion to avoid FileNotFoundError on Windows if ffmpeg missing.
        # Vachana's backend ffmpeg natively decodes the magic bytes correctly,
        # but their API gateway strictly validates the filename/mimetype.
        return audio_bytes, "recording.ogg", "audio/ogg"


def transcribe_audio(audio_bytes: bytes, lang: str = "ta-IN") -> str:
    """
    Transcribe audio bytes using Gnani Prisma v2.5 REST STT.
    """
    language_code = _normalize_language(lang)
    audio_payload, filename, content_type = _normalize_audio(audio_bytes)
    data = {
        "language_code": language_code,
        "preferred_language": language_code,
        "format": "transcribe",
        "itn_native_numerals": "true"
    }

    try:
        response = requests.post(
            _stt_url(),
            headers=_headers(),
            files={"audio_file": (filename, audio_payload, content_type)},
            data=data,
            timeout=90,
        )
        if response.ok:
            payload = response.json()
            return str(payload.get("transcript", "") or "").strip()

        logger.warning("Gnani STT failed: %s %s", response.status_code, response.text[:1000])
    except Exception as exc:
        logger.error("Error calling Gnani STT: %s", exc)
    return ""


def _extract_audio_bytes(response: requests.Response) -> bytes:
    content_type = response.headers.get("Content-Type", "").lower()
    if "audio/" in content_type or response.content[:4] == b"RIFF":
        return response.content

    try:
        body: dict[str, Any] = response.json()
        audio_b64 = body.get("audio") or body.get("data", {}).get("audio")
        if audio_b64:
            return base64.b64decode(audio_b64)
    except Exception:
        return response.content
    return b""


def text_to_speech(text: str, lang: str = "ta-IN") -> bytes:
    """
    Convert text to speech using Gnani Timbre v2.0 REST TTS.
    """
    text = (text or "").strip()
    if not text:
        logger.warning("Gnani TTS aborted: Text is empty.")
        return b""
        
    language_code = _normalize_language(lang)
    if language_code not in ["en-IN", "hi-IN"]:
        language_code = "en-IN"
        
    payload = {
        "text": text,
        "voice": str(getattr(Config, "GNANI_VOICE", "Pranav")).strip(),
        "model": str(getattr(Config, "GNANI_TTS_MODEL", "vachana-voice-v3")).strip(),
        "audio_config": {
            "sample_rate": 44100,
            "num_channels": 1,
            "sample_width": 2,
            "encoding": "linear_pcm",
            "container": "wav"
        }
    }

    try:
        response = requests.post(
            _tts_url(),
            headers=_headers("application/json"),
            json=payload,
            timeout=90,
        )
        if response.ok and response.content:
            audio = _extract_audio_bytes(response)
            if audio:
                return audio
        logger.warning("Gnani TTS failed: %s %s", response.status_code, response.text[:1000])
    except Exception as exc:
        logger.error("Error calling Gnani TTS: %s", exc)
    return b""


def build_silent_wav(duration_seconds: float = 0.3, sample_rate: int = 44100) -> bytes:
    """Fallback WAV so the browser still receives a valid audio payload."""
    import struct

    num_channels = 1
    sample_width = 2
    total_frames = max(1, int(duration_seconds * sample_rate))
    pcm = b"\x00\x00" * total_frames
    data_length = len(pcm)
    byte_rate = sample_rate * num_channels * sample_width
    block_align = num_channels * sample_width
    wav_header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_length,
        b"WAVE",
        b"fmt ",
        16,
        1,
        num_channels,
        sample_rate,
        byte_rate,
        block_align,
        sample_width * 8,
        b"data",
        data_length,
    )
    return wav_header + pcm
