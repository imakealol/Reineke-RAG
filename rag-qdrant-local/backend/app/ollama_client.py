"""Thin client for the Ollama HTTP API (`/api/embeddings`, `/api/chat`,
`/api/tags`).

We deliberately do *not* depend on the ollama Python SDK — the REST API is
small, stable, and one less dependency to keep aligned.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from .config import settings
from .utils import get_logger

log = get_logger(__name__)


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: float = 600.0,
    ) -> None:
        self.base_url = (base_url or settings.OLLAMA_BASE_URL).rstrip("/")
        # Generous read timeout: a cold-loaded 32B model + long generation can
        # easily exceed 2 minutes. Connect timeout stays short so a dead
        # Ollama is reported quickly.
        self.timeout = httpx.Timeout(
            connect=10.0,
            read=timeout,
            write=30.0,
            pool=10.0,
        )

    # ---- low-level --------------------------------------------------------

    # Exceptions worth retrying once: dropped connections, mid-request
    # protocol errors, transient "remote closed" surprises that show up
    # under parallel load.
    _TRANSIENT_ERRORS = (
        httpx.ConnectError,
        httpx.ReadError,
        httpx.RemoteProtocolError,
    )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[Dict[str, Any]] = None,
    ) -> httpx.Response:
        url = f"{self.base_url}{path}"
        last_exc: Optional[Exception] = None
        # First attempt + one retry for transient drops.
        for attempt in (1, 2):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.request(method, url, json=json)
                break
            except self._TRANSIENT_ERRORS as exc:
                last_exc = exc
                if attempt == 2:
                    detail = str(exc) or exc.__class__.__name__
                    raise OllamaError(
                        f"Ollama request failed ({type(exc).__name__}) after retry: {detail}"
                    ) from exc
                log.warning(
                    "Transient Ollama error on %s %s (attempt %d/2): %s — retrying",
                    method, path, attempt, exc.__class__.__name__,
                )
                await asyncio.sleep(0.75)
            except httpx.HTTPError as exc:
                # Non-retryable httpx error (e.g. timeout, invalid URL, etc.)
                detail = str(exc) or exc.__class__.__name__
                raise OllamaError(
                    f"Ollama request failed ({type(exc).__name__}): {detail}"
                ) from exc

        if resp.status_code >= 400:
            raise OllamaError(
                f"Ollama error {resp.status_code} on {method} {path}: {resp.text}"
            )
        return resp

    # ---- public -----------------------------------------------------------

    async def list_models(self) -> List[str]:
        resp = await self._request("GET", "/api/tags")
        data = resp.json()
        models = data.get("models", []) or []
        return [m.get("name", "") for m in models if m.get("name")]

    async def has_model(self, name: str) -> bool:
        try:
            models = await self.list_models()
        except OllamaError:
            return False
        # Ollama tags often look like "model:tag"; allow prefix match.
        return any(m == name or m.startswith(name + ":") or m.split(":")[0] == name for m in models)

    async def embed(self, text: str, *, model: Optional[str] = None) -> List[float]:
        body = {
            "model": model or settings.EMBEDDING_MODEL,
            "prompt": text,
        }
        resp = await self._request("POST", "/api/embeddings", json=body)
        data = resp.json()
        emb = data.get("embedding")
        if not emb or not isinstance(emb, list):
            raise OllamaError(f"Embedding response had no 'embedding': {data}")
        return [float(x) for x in emb]

    async def embed_many(self, texts: List[str], *, model: Optional[str] = None) -> List[List[float]]:
        # /api/embeddings is single-prompt — call sequentially (Ollama batches
        # internally on the GPU).
        out: List[List[float]] = []
        for t in texts:
            out.append(await self.embed(t, model=model))
        return out

    async def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        body: Dict[str, Any] = {
            "model": model or settings.CHAT_MODEL,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": (
                    temperature if temperature is not None else settings.CHAT_TEMPERATURE
                ),
                "num_predict": (
                    max_tokens if max_tokens is not None else settings.CHAT_MAX_TOKENS
                ),
            },
        }
        resp = await self._request("POST", "/api/chat", json=body)
        data = resp.json()
        msg = (data.get("message") or {}).get("content")
        if not msg:
            raise OllamaError(f"Chat response had no message content: {data}")
        return msg

    async def ping(self) -> bool:
        try:
            await self._request("GET", "/api/tags")
            return True
        except OllamaError:
            return False
