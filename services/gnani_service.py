"""
services/gnani_service.py
Gnani AI (Vachana) STT and TTS service.

Provides:
  - transcribe_audio(): Speech-to-Text via Gnani Prisma v2.5 REST
  - text_to_speech(): Text-to-Speech via Gnani Timbre v2.0 REST
  - build_silent_wav(): Fallback silent WAV generator

Production features:
  - Retry with exponential backoff (urllib3 Retry via requests.Session)
  - Configurable timeout via GNANI_TIMEOUT
  - Structured logging: request metadata, response status, latency, audio size
  - Tamil voice mapping: ta-IN → GNANI_TAMIL_VOICE (Kaveri), en-IN → GNANI_ENGLISH_VOICE (Pranav)
  - LRU cache for repeated TTS requests (avoids redundant API calls)
  - Graceful fallback: all functions return safe defaults on failure
  - WAV and WebM input support; ffmpeg conversion with ogg fallback
"""
from __future__ import annotations

import base64
import functools
import json
import logging
import os
import subprocess
import tempfile
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter, Retry

from config.settings import Config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Retry configuration
# ---------------------------------------------------------------------------
_STT_MAX_RETRIES = 3
_TTS_MAX_RETRIES = 3
_BACKOFF_FACTOR = 1.5            # 1.5s, 3s, 6s
_RETRY_STATUS_CODES = {429, 500, 502, 503, 504}

# ---------------------------------------------------------------------------
# TTS LRU cache (keyed on (text_hash, lang))
# ---------------------------------------------------------------------------
_TTS_CACHE_MAX = 50
_tts_cache: "dict[tuple, bytes]" = {}
_tts_cache_order: "list[tuple]" = []


def _tts_cache_get(key: tuple) -> bytes | None:
    return _tts_cache.get(key)


def _tts_cache_set(key: tuple, value: bytes) -> None:
    if key in _tts_cache:
        return
    if len(_tts_cache_order) >= _TTS_CACHE_MAX:
        oldest = _tts_cache_order.pop(0)
        _tts_cache.pop(oldest, None)
    _tts_cache[key] = value
    _tts_cache_order.append(key)


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------
def _api_key() -> str:
    return str(
        getattr(Config, "GNANI_API_KEY_ID", "") or
        getattr(Config, "GNANI_API_KEY", "") or ""
    ).strip()


def _base_url() -> str:
    return str(
        getattr(Config, "GNANI_BASE_URL", "https://api.vachana.ai") or "https://api.vachana.ai"
    ).rstrip("/")


def _stt_url() -> str:
    override = str(getattr(Config, "GNANI_STT_URL", "") or "").strip()
    return override or f"{_base_url()}/stt/v3"


def _tts_url() -> str:
    override = str(getattr(Config, "GNANI_TTS_URL", "") or "").strip()
    return override or f"{_base_url()}/api/v1/tts/inference"


def _timeout() -> int:
    try:
        return int(getattr(Config, "GNANI_TIMEOUT", 60))
    except (TypeError, ValueError):
        return 60


def _tamil_voice() -> str:
    return str(getattr(Config, "GNANI_TAMIL_VOICE", "") or getattr(Config, "GNANI_VOICE", "Kaveri")).strip() or "Kaveri"


def _english_voice() -> str:
    return str(getattr(Config, "GNANI_ENGLISH_VOICE", "") or getattr(Config, "GNANI_VOICE", "Pranav")).strip() or "Pranav"


def _voice_for_lang(lang: str) -> str:
    return _tamil_voice() if lang == "ta-IN" else _english_voice()


def _tts_model() -> str:
    return str(getattr(Config, "GNANI_TTS_MODEL", "vachana-voice-v3") or "vachana-voice-v3").strip()


def _normalize_language(lang: str) -> str:
    return (lang or "").strip() or "en-IN"


def _headers(content_type: str | None = None) -> dict[str, str]:
    h = {"X-API-Key-ID": _api_key()}
    if content_type:
        h["Content-Type"] = content_type
    return h


# ---------------------------------------------------------------------------
# Session factories with retry
# ---------------------------------------------------------------------------
def _make_stt_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=_STT_MAX_RETRIES,
        backoff_factor=_BACKOFF_FACTOR,
        status_forcelist=list(_RETRY_STATUS_CODES),
        allowed_methods=["POST"],
        raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    return session


def _make_tts_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=_TTS_MAX_RETRIES,
        backoff_factor=_BACKOFF_FACTOR,
        status_forcelist=list(_RETRY_STATUS_CODES),
        allowed_methods=["POST"],
        raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    return session


# ---------------------------------------------------------------------------
# Audio normalization
# ---------------------------------------------------------------------------
def _normalize_audio(audio_bytes: bytes) -> tuple[bytes, str, str]:
    """
    Normalize input audio to WAV/PCM 16kHz 16-bit mono for Gnani STT.
    Detects WAV by RIFF header. Falls back to ogg if ffmpeg is unavailable.

    Returns:
        (audio_bytes, filename, mime_type)
    """
    if not audio_bytes:
        logger.warning("[Gnani STT] Received empty audio bytes.")
        return b"", "recording.wav", "audio/wav"

    if audio_bytes.startswith(b"RIFF"):
        logger.debug("[Gnani STT] Audio detected as WAV (RIFF header).")
        return audio_bytes, "recording.wav", "audio/wav"

    logger.info("[Gnani STT] Non-WAV audio received (%d bytes). Attempting ffmpeg conversion.", len(audio_bytes))
    temp_in_path = ""
    temp_out_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".webm") as temp_in:
            temp_in.write(audio_bytes)
            temp_in_path = temp_in.name

        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_out:
            temp_out_path = temp_out.name

        subprocess.run(
            [
                "ffmpeg", "-y", "-i", temp_in_path,
                "-ac", "1", "-ar", "16000", "-sample_fmt", "s16",
                temp_out_path,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        with open(temp_out_path, "rb") as f:
            wav_bytes = f.read()

        logger.info("[Gnani STT] ffmpeg conversion successful: %d bytes WAV.", len(wav_bytes))
        return wav_bytes, "recording.wav", "audio/wav"

    except FileNotFoundError:
        logger.warning("[Gnani STT] ffmpeg not found — sending as OGG (Gnani backend will decode).")
    except subprocess.CalledProcessError as exc:
        logger.warning("[Gnani STT] ffmpeg conversion failed: %s — sending as OGG.", exc)
    except Exception as exc:
        logger.warning("[Gnani STT] Audio conversion error: %s — sending as OGG.", exc)
    finally:
        for path in (temp_in_path, temp_out_path):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    return audio_bytes, "recording.ogg", "audio/ogg"


# ---------------------------------------------------------------------------
# STT
# ---------------------------------------------------------------------------
def transcribe_audio(audio_bytes: bytes, lang: str = "ta-IN") -> str:
    """
    Transcribe audio bytes using Gnani Prisma v2.5 REST STT.

    Supports WAV and WebM input (WebM is converted via ffmpeg; falls back to OGG).
    Retries on 429/5xx errors with exponential backoff.
    Returns empty string on any failure — never raises.

    Args:
        audio_bytes: Raw audio bytes from the frontend microphone.
        lang:        Language code (e.g. "ta-IN", "en-IN").

    Returns:
        Transcribed text string, or empty string on failure.
    """
    if not audio_bytes:
        logger.warning("[Gnani STT] Empty audio bytes provided — skipping transcription.")
        return ""

    if not _api_key():
        logger.error("[Gnani STT] Missing GNANI_API_KEY. Aborting transcription.")
        return ""

    language_code = _normalize_language(lang)
    audio_payload, filename, content_type = _normalize_audio(audio_bytes)

    if not audio_payload:
        logger.warning("[Gnani STT] Audio normalization produced empty bytes.")
        return ""

    form_data = {
        "language_code": language_code,
        "preferred_language": language_code,
        "format": "transcribe",
        "itn_native_numerals": "true",
    }

    logger.info(
        "[Gnani STT] Request | lang=%s | audio_size=%d | filename=%s | url=%s",
        language_code, len(audio_payload), filename, _stt_url(),
    )

    session = _make_stt_session()
    start_ts = time.monotonic()
    response = None

    try:
        response = session.post(
            _stt_url(),
            headers=_headers(),
            files={"audio_file": (filename, audio_payload, content_type)},
            data=form_data,
            timeout=_timeout(),
        )
        latency_ms = (time.monotonic() - start_ts) * 1000

        logger.info(
            "[Gnani STT] Response | status=%d | latency_ms=%.1f",
            response.status_code, latency_ms,
        )

        if response.ok:
            payload = response.json()
            transcript = str(payload.get("transcript", "") or "").strip()
            logger.info("[Gnani STT] Transcript: %s", transcript[:200] if transcript else "(empty)")
            return transcript

        logger.warning(
            "[Gnani STT] Non-OK response | status=%d | body=%s",
            response.status_code, response.text[:500],
        )
        return ""

    except requests.exceptions.Timeout:
        latency_ms = (time.monotonic() - start_ts) * 1000
        logger.error("[Gnani STT] Timeout after %.1fms.", latency_ms)
        return ""
    except requests.exceptions.ConnectionError as exc:
        logger.error("[Gnani STT] Connection error: %s", exc)
        return ""
    except Exception as exc:
        logger.error("[Gnani STT] Unexpected error: %s", exc, exc_info=True)
        return ""
    finally:
        session.close()


# ---------------------------------------------------------------------------
# TTS audio extraction helper
# ---------------------------------------------------------------------------
def _extract_audio_bytes(response: requests.Response) -> bytes:
    """Extract audio bytes from a Gnani TTS response (handles binary + base64 JSON)."""
    content_type = response.headers.get("Content-Type", "").lower()
    content = response.content

    # Detect raw binary audio: by Content-Type or by magic bytes
    _AUDIO_MAGIC = (
        b"RIFF",    # WAV
        b"OggS",    # OGG
        b"\xff\xfb",  # MP3
        b"\xff\xf3",  # MP3
        b"\xff\xf2",  # MP3
        b"fLaC",    # FLAC
    )
    is_audio_content_type = "audio/" in content_type
    is_audio_magic = any(content[:4].startswith(magic) for magic in _AUDIO_MAGIC)

    if is_audio_content_type or is_audio_magic:
        logger.debug(
            "[Gnani TTS] Binary audio response detected: content_type=%s, size=%d bytes",
            content_type, len(content),
        )
        return content

    # Try JSON-wrapped base64 audio (some API versions return this)
    try:
        body: dict[str, Any] = response.json()
        audio_b64 = body.get("audio") or body.get("data", {}).get("audio")
        if audio_b64:
            decoded = base64.b64decode(audio_b64)
            logger.debug("[Gnani TTS] Base64-decoded audio from JSON response: %d bytes", len(decoded))
            return decoded
        # Log the full JSON error body so it appears in server logs
        logger.warning("[Gnani TTS] JSON response but no audio field. Body: %s", str(body)[:500])
    except Exception as exc:
        logger.warning("[Gnani TTS] Base64 decoding or JSON parse failed: %s", exc)

    # Last resort — return raw content (may be empty)
    return content or b""


# ---------------------------------------------------------------------------
# TTS
# ---------------------------------------------------------------------------
def text_to_speech(text: str, lang: str = "en-IN") -> bytes:
    """
    Convert text to speech using Gnani Timbre v2.0 REST TTS.

    Supports English (en-IN) and Tamil (ta-IN).
    Caches results for identical (text, lang) pairs to avoid redundant API calls.
    Retries on 429/5xx with exponential backoff.
    Returns empty bytes on failure (caller should use build_silent_wav() as fallback).

    Args:
        text: Text to synthesize. Must be non-empty.
        lang: Language code — "en-IN" or "ta-IN".

    Returns:
        WAV audio bytes, or empty bytes on failure.
    """
    text = (text or "").strip()
    if not text:
        logger.warning("[Gnani TTS] Aborted: empty text.")
        return b""

    if not _api_key():
        logger.error("[Gnani TTS] Missing GNANI_API_KEY. Aborting TTS.")
        return b""

    language_code = _normalize_language(lang)

    # Tamil TTS: attempt ta-IN; if Gnani doesn't support it, fall back to en-IN
    # (detected at runtime from non-OK response)
    tts_lang = language_code

    voice = _voice_for_lang(language_code)

    # Check cache
    cache_key = (hash(text[:500]), tts_lang)
    cached = _tts_cache_get(cache_key)
    if cached:
        logger.debug("[Gnani TTS] Cache hit for lang=%s, text_hash=%d", tts_lang, cache_key[0])
        return cached

    payload = {
        "model": _tts_model(),          # must be first per Vachana spec
        "text": text,
        "voice": voice,
        "audio_config": {
            "container": "wav",          # output container format
            "encoding": "linear_pcm",   # PCM encoding
            "sample_rate": 44100,
            "num_channels": 1,
            # NOTE: sample_width is NOT a Vachana API field — omitted
        },
    }

    logger.info(
        "[Gnani TTS] Request | lang=%s | voice=%s | model=%s | text_len=%d | url=%s",
        tts_lang, voice, _tts_model(), len(text), _tts_url(),
    )

    session = _make_tts_session()
    start_ts = time.monotonic()
    response = None

    try:
        response = session.post(
            _tts_url(),
            headers=_headers("application/json"),
            json=payload,
            timeout=_timeout(),
        )
        latency_ms = (time.monotonic() - start_ts) * 1000

        logger.info(
            "[Gnani TTS] Response | status=%d | latency_ms=%.1f | content_size=%d",
            response.status_code, latency_ms, len(response.content),
        )

        if response.ok and response.content:
            audio = _extract_audio_bytes(response)
            if audio:
                logger.info("[Gnani TTS] Audio generated: %d bytes | lang=%s.", len(audio), tts_lang)
                _tts_cache_set(cache_key, audio)
                return audio

            logger.warning("[Gnani TTS] Response OK but audio extraction returned empty bytes.")

        # Non-OK: log the raw error body for diagnostics (per debugging guide)
        if not response.ok:
            err_ct = response.headers.get("Content-Type", "")
            if "application/json" in err_ct:
                try:
                    logger.error(
                        "[Gnani TTS] API JSON error | status=%d | body=%s",
                        response.status_code, response.json(),
                    )
                except Exception:
                    logger.error(
                        "[Gnani TTS] API error | status=%d | body=%s",
                        response.status_code, response.text[:500],
                    )
            else:
                logger.error(
                    "[Gnani TTS] API error | status=%d | body=%s",
                    response.status_code, response.text[:500],
                )

            # If Tamil TTS fails, retry with en-IN
            if tts_lang == "ta-IN":
                logger.warning(
                    "[Gnani TTS] Tamil TTS failed (status=%d) — retrying with en-IN.",
                    response.status_code,
                )
                return text_to_speech(text, lang="en-IN")

            return b""

    except requests.exceptions.Timeout:
        latency_ms = (time.monotonic() - start_ts) * 1000
        logger.error("[Gnani TTS] Timeout after %.1fms.", latency_ms)
        return b""
    except requests.exceptions.ConnectionError as exc:
        logger.error("[Gnani TTS] Connection error: %s", exc)
        return b""
    except Exception as exc:
        logger.error("[Gnani TTS] Unexpected error: %s", exc, exc_info=True)
        return b""
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Silent WAV fallback
# ---------------------------------------------------------------------------
def build_silent_wav(duration_seconds: float = 0.3, sample_rate: int = 44100) -> bytes:
    """
    Generate a valid silent WAV file as a browser-safe fallback when TTS fails.

    Args:
        duration_seconds: Duration of silence (default 0.3s).
        sample_rate:      Sample rate in Hz (default 44100).

    Returns:
        WAV bytes with a valid RIFF/WAVE header and silent PCM data.
    """
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
