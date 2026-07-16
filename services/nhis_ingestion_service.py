from __future__ import annotations

import hashlib
import logging
import re
from collections import deque
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set
from urllib.parse import urljoin, urldefrag, urlparse

import requests

from services.rag_service import append_chunks_to_vector_db, save_rag_chunks_to_mongo, save_rag_chunks_to_supabase, _embedding_model
from config.settings import Config

logger = logging.getLogger(__name__)

DEFAULT_START_URL = Config.NHIS_START_URL
ALLOWED_NETLOCS = Config.NHIS_ALLOWED_NETLOCS
ALLOWED_PREFIXES = Config.NHIS_ALLOWED_PREFIXES
MAX_PAGES = 120
REQUEST_TIMEOUT = 30


class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text_parts: List[str] = []
        self.links: Set[str] = set()
        self._capture_text = True
        self._current_href = None
        self._skip_stack = 0

    def handle_starttag(self, tag, attrs):
        attr_map = dict(attrs)
        if tag in {"script", "style", "noscript"}:
            self._skip_stack += 1
            return
        if tag == "a" and attr_map.get("href"):
            self.links.add(attr_map["href"])
        if tag in {"p", "br", "div", "tr", "li", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6", "table"}:
            self.text_parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"} and self._skip_stack:
            self._skip_stack -= 1
        if tag in {"p", "div", "tr", "li", "section", "article", "table"}:
            self.text_parts.append("\n")

    def handle_data(self, data):
        if self._skip_stack:
            return
        text = re.sub(r"\s+", " ", data or "").strip()
        if text:
            self.text_parts.append(text)

    def text(self) -> str:
        raw = " ".join(self.text_parts)
        raw = re.sub(r"\n\s*\n+", "\n\n", raw)
        raw = re.sub(r"[ \t]+", " ", raw)
        return raw.strip()


def _normalize_url(url: str) -> str:
    return urldefrag(url)[0]


def _is_allowed(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.netloc not in ALLOWED_NETLOCS:
        return False
    return parsed.path.startswith(ALLOWED_PREFIXES)


def _fetch(url: str) -> requests.Response:
    headers = {"User-Agent": "MediCurance-RAG-Ingest/1.0"}
    response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response


def _extract_text_and_links(html: str) -> tuple[str, Set[str]]:
    parser = _PageParser()
    parser.feed(html)
    return parser.text(), parser.links


def _chunk_text(text: str, max_chars: int = 1000, overlap: int = 160) -> Iterable[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: List[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(current) + len(sentence) + 1 <= max_chars:
            current = f"{current} {sentence}".strip()
            continue
        if current:
            chunks.append(current)
        current = sentence
    if current:
        chunks.append(current)

    output: List[str] = []
    for chunk in chunks:
        if len(chunk) <= max_chars:
            output.append(chunk)
            continue
        start = 0
        while start < len(chunk):
            output.append(chunk[start:start + max_chars].strip())
            start += max(1, max_chars - overlap)
    return [item for item in output if item]


def _chunk_id(url: str, page_index: int, chunk_index: int, text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
    return f"nhis:{page_index}:{chunk_index}:{digest}"


def crawl_nhis_policy_pages(start_url: str = DEFAULT_START_URL, max_pages: int = MAX_PAGES) -> List[Dict[str, Any]]:
    start_url = _normalize_url(start_url)
    queue = deque([start_url])
    visited: Set[str] = set()
    pages: List[Dict[str, Any]] = []

    while queue and len(visited) < max_pages:
        url = _normalize_url(queue.popleft())
        if url in visited or not _is_allowed(url):
            continue
        visited.add(url)

        try:
            response = _fetch(url)
        except Exception as exc:
            logger.warning("[NHIS] Fetch failed for %s: %s", url, exc)
            continue

        content_type = response.headers.get("content-type", "").lower()
        if "pdf" in content_type or url.lower().endswith(".pdf"):
            try:
                import fitz

                with fitz.open(stream=response.content, filetype="pdf") as doc:
                    for page_number, page in enumerate(doc, start=1):
                        text = re.sub(r"\s+", " ", page.get_text("text") or "").strip()
                        if text:
                            pages.append({
                                "source_url": url,
                                "document": Path(urlparse(url).path).name or "TNPolInfo.pdf",
                                "page": page_number,
                                "section": "PDF",
                                "category": "NHIS Policy",
                                "text": text,
                            })
                continue
            except Exception as exc:
                logger.warning("[NHIS] PDF parse failed for %s: %s", url, exc)
                continue

        text, links = _extract_text_and_links(response.text)
        if text:
            pages.append({
                "source_url": url,
                "document": Path(urlparse(url).path).name or "TNPolInfo.aspx",
                "page": 1,
                "section": "Web Page",
                "category": "NHIS Policy",
                "text": text,
            })

        for link in links:
            absolute = _normalize_url(urljoin(url, link))
            if absolute not in visited and _is_allowed(absolute):
                queue.append(absolute)

    return pages


def build_nhis_chunks(start_url: str = DEFAULT_START_URL, max_pages: int = MAX_PAGES) -> List[Dict[str, Any]]:
    pages = crawl_nhis_policy_pages(start_url=start_url, max_pages=max_pages)
    chunks: List[Dict[str, Any]] = []
    for page in pages:
        for chunk_index, chunk_text in enumerate(_chunk_text(page["text"]), start=1):
            chunks.append({
                "chunk_id": _chunk_id(page["source_url"], page["page"], chunk_index, chunk_text),
                "text": chunk_text,
                "source_document": page["document"],
                "source_url": page["source_url"],
                "page": page["page"],
                "section": page["section"],
                "category": page["category"],
            })
    return chunks


def _embed_chunks(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not chunks:
        return chunks
    model = _embedding_model()
    vectors = model.encode([item["text"] for item in chunks], show_progress_bar=False)
    for item, vector in zip(chunks, vectors):
        item["embedding"] = [float(x) for x in vector.tolist()]
    return chunks


def ingest_nhis_policy_data(start_url: str = DEFAULT_START_URL, max_pages: int = MAX_PAGES) -> Dict[str, Any]:
    chunks = _embed_chunks(build_nhis_chunks(start_url=start_url, max_pages=max_pages))
    mongo_count = save_rag_chunks_to_mongo(chunks)
    vector_ok = append_chunks_to_vector_db(chunks)
    supabase_count = save_rag_chunks_to_supabase(chunks, file_name="TNPolInfo.aspx")
    return {
        "ok": bool(chunks) and (mongo_count > 0 or supabase_count > 0) and vector_ok,
        "pages": len({item["source_url"] for item in chunks}),
        "chunks": len(chunks),
        "mongo_saved": mongo_count,
        "supabase_saved": supabase_count,
        "vector_updated": vector_ok,
    }
