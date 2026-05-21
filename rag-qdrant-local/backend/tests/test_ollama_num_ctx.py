"""OllamaClient — num_ctx auto-detection from /api/show.

We don't hit a real Ollama; we stub the ``_request`` method to return
canned ``/api/show`` payloads and assert that the highest available
context_length wins, the cache is honoured, and we fall back gracefully
when the model file pins ``num_ctx`` via the parameters block instead.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from app import ollama_client as ollama_client_module
from app.ollama_client import OllamaClient


class _FakeResponse:
    def __init__(self, payload: Dict[str, Any]) -> None:
        self._payload = payload

    def json(self) -> Dict[str, Any]:
        return self._payload


def _make_client_with_stub(payload: Dict[str, Any], *, calls: list | None = None) -> OllamaClient:
    """Return a client whose `_request` returns ``payload`` and records calls."""
    client = OllamaClient(base_url="http://stub")

    async def fake_request(method: str, path: str, *, json=None):  # noqa: ANN001
        if calls is not None:
            calls.append((method, path, json))
        return _FakeResponse(payload)

    client._request = fake_request  # type: ignore[assignment]
    return client


@pytest.fixture(autouse=True)
def _clear_ctx_cache():
    """Each test starts with an empty cache so they don't leak into each other."""
    ollama_client_module._CTX_CACHE.clear()
    yield
    ollama_client_module._CTX_CACHE.clear()


# ---------------------------------------------------------------------------
# Happy path: model_info exposes <arch>.context_length
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reads_max_context_length_from_model_info():
    client = _make_client_with_stub({
        "model_info": {
            "general.architecture": "qwen2",
            "qwen2.context_length": 32768,
            "qwen2.embedding_length": 5120,
        },
    })
    assert await client.get_context_length("qwen2.5:14b") == 32768


@pytest.mark.asyncio
async def test_picks_max_when_multiple_context_length_keys_present():
    """Some quantised models expose more than one context_length key."""
    client = _make_client_with_stub({
        "model_info": {
            "qwen2.context_length": 32768,
            "general.context_length": 8192,
        },
    })
    assert await client.get_context_length("qwen2.5:14b") == 32768


# ---------------------------------------------------------------------------
# Fallback: parameters block carries ``num_ctx``
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_falls_back_to_parameters_num_ctx_when_model_info_missing():
    client = _make_client_with_stub({
        "model_info": {},
        "parameters": "stop                           \"<|endoftext|>\"\nnum_ctx                        8192\n",
    })
    assert await client.get_context_length("custom-model") == 8192


@pytest.mark.asyncio
async def test_uses_ollama_default_when_nothing_is_declared():
    client = _make_client_with_stub({"model_info": {}, "parameters": ""})
    assert (
        await client.get_context_length("orphan-model")
        == ollama_client_module._OLLAMA_DEFAULT_NUM_CTX
    )


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cache_avoids_repeated_api_show_calls_for_same_model():
    calls: list = []
    client = _make_client_with_stub(
        {"model_info": {"qwen2.context_length": 16384}}, calls=calls,
    )
    a = await client.get_context_length("qwen2.5:14b")
    b = await client.get_context_length("qwen2.5:14b")
    assert a == b == 16384
    # Only the first call hits the wire.
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_cache_is_per_model_name():
    payloads = {
        "model-a": {"model_info": {"a.context_length": 8192}},
        "model-b": {"model_info": {"b.context_length": 32768}},
    }
    client = OllamaClient(base_url="http://stub")

    async def fake_request(method: str, path: str, *, json=None):  # noqa: ANN001
        model = json["name"]  # type: ignore[index]
        return _FakeResponse(payloads[model])

    client._request = fake_request  # type: ignore[assignment]

    assert await client.get_context_length("model-a") == 8192
    assert await client.get_context_length("model-b") == 32768
    # Cached.
    assert await client.get_context_length("model-a") == 8192


# ---------------------------------------------------------------------------
# Failure mode: /api/show is unreachable
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_swallows_ollama_error_and_returns_default():
    """If the model isn't present (or Ollama is down), get_context_length
    must not crash the chat path — it falls back to the conservative
    default and lets the chat call surface the real error itself."""
    from app.ollama_client import OllamaError

    client = OllamaClient(base_url="http://stub")

    async def boom(method: str, path: str, *, json=None):  # noqa: ANN001
        raise OllamaError("404 model not found")

    client._request = boom  # type: ignore[assignment]
    assert (
        await client.get_context_length("missing")
        == ollama_client_module._OLLAMA_DEFAULT_NUM_CTX
    )
