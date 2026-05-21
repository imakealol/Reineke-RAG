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


# Per-model context length, populated lazily from /api/show. Cached at module
# level because OllamaClient instances are short-lived (per request) but the
# model's context length is invariant once the model file is loaded.
_CTX_CACHE: Dict[str, int] = {}

# Ollama's compiled-in default when the model file doesn't pin one explicitly.
# Used as a last resort so we never crash on weird/unknown models.
_OLLAMA_DEFAULT_NUM_CTX = 2048


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
            "keep_alive": settings.OLLAMA_KEEP_ALIVE,
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

    async def get_context_length(self, model: Optional[str] = None) -> int:
        """Return the model's declared max context window from /api/show.

        Ollama's chat API otherwise silently truncates messages to its
        compiled-in default (2048 tokens), which quietly drops the system
        prompt + early history of any non-trivial RAG conversation. We resolve
        and cache the model's *real* max once per process so the value can
        flow into ``chat()``'s ``num_ctx`` option.

        Resolution order, biggest-fits-first:
          1. ``model_info["<arch>.context_length"]`` — exact value
          2. ``parameters`` text contains ``num_ctx`` — pinned via Modelfile
          3. ``_OLLAMA_DEFAULT_NUM_CTX`` fallback

        Cached by model name; safe to call repeatedly. Errors fall through to
        the default so a missing model doesn't break chat.
        """
        name = model or settings.CHAT_MODEL
        if name in _CTX_CACHE:
            return _CTX_CACHE[name]
        try:
            resp = await self._request("POST", "/api/show", json={"name": name})
            data = resp.json()
        except OllamaError as exc:
            log.warning(
                "Could not query /api/show for %s (%s); falling back to %d",
                name, exc, _OLLAMA_DEFAULT_NUM_CTX,
            )
            _CTX_CACHE[name] = _OLLAMA_DEFAULT_NUM_CTX
            return _OLLAMA_DEFAULT_NUM_CTX

        info = data.get("model_info") or {}
        # Architecture-prefixed keys: qwen2.context_length, llama.context_length, ...
        candidates = [
            int(v) for k, v in info.items()
            if k.endswith(".context_length") and isinstance(v, (int, float))
        ]
        if candidates:
            ctx = max(candidates)
        else:
            # Parse the parameters text — looks like "num_ctx                 8192"
            ctx = _OLLAMA_DEFAULT_NUM_CTX
            params_text = data.get("parameters", "") or ""
            for line in params_text.splitlines():
                parts = line.strip().split()
                if len(parts) >= 2 and parts[0] == "num_ctx":
                    try:
                        ctx = int(parts[1])
                        break
                    except ValueError:
                        pass

        _CTX_CACHE[name] = ctx
        log.info("Resolved num_ctx=%d for model=%s (from /api/show)", ctx, name)
        return ctx

    async def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        chat_model = model or settings.CHAT_MODEL
        # Resolve num_ctx: env override wins, else detect from /api/show.
        if settings.OLLAMA_NUM_CTX is not None:
            num_ctx = settings.OLLAMA_NUM_CTX
        else:
            num_ctx = await self.get_context_length(chat_model)
        body: Dict[str, Any] = {
            "model": chat_model,
            "messages": messages,
            "stream": False,
            "keep_alive": settings.OLLAMA_KEEP_ALIVE,
            "options": {
                "temperature": (
                    temperature if temperature is not None else settings.CHAT_TEMPERATURE
                ),
                "num_predict": (
                    max_tokens if max_tokens is not None else settings.CHAT_MAX_TOKENS
                ),
                "num_ctx": num_ctx,
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
