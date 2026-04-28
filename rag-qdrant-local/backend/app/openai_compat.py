"""Adapters to expose the RAG backend as an OpenAI-compatible API.

The goal is *just enough* compatibility for OpenWebUI to point at
``http://localhost:8000/v1`` and forward chat messages — not a full OpenAI
shim. Streaming is intentionally not supported in the MVP.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List, Optional

from .schemas import (
    ChatSource,
    OpenAIChatCompletionRequest,
    OpenAIChatCompletionResponse,
    OpenAIChoice,
    OpenAIChoiceMessage,
    OpenAIMessage,
    OpenAIUsage,
)
from .utils import new_id


MODEL_PREFIX = "rag:"


class OpenAIRequestError(ValueError):
    """Raised when the OpenAI request can't be mapped onto a RAG call."""


@dataclass
class ResolvedRequest:
    tenant: str
    project: str
    question: str
    session_id: Optional[str]
    model: str


# ---------------------------------------------------------------------------

def _last_user_message(messages: List[OpenAIMessage]) -> str:
    """Pull the most recent user turn — that's the question we retrieve on."""
    for m in reversed(messages):
        if m.role == "user" and m.content.strip():
            return m.content.strip()
    raise OpenAIRequestError(
        "messages must contain at least one non-empty 'user' message."
    )


def _parse_model_id(model: str) -> Optional[tuple[str, str]]:
    """``rag:<tenant>:<project>`` → ``(tenant, project)`` or None."""
    if not model.startswith(MODEL_PREFIX):
        return None
    rest = model[len(MODEL_PREFIX) :]
    parts = rest.split(":", 1)
    if len(parts) != 2:
        return None
    tenant, project = parts[0].strip(), parts[1].strip()
    if not tenant or not project:
        return None
    return tenant, project


def _coerce_str(value: object) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        v = value.strip()
        return v or None
    return str(value).strip() or None


def resolve_openai_request(req: OpenAIChatCompletionRequest) -> ResolvedRequest:
    """Map an OpenAI-style request onto our internal RAG call shape.

    Resolution order for tenant/project:
      1. Top-level fields ``tenant`` / ``project`` (non-standard but allowed).
      2. ``extra_body.tenant`` / ``extra_body.project``.
      3. Encoded in the model id as ``rag:<tenant>:<project>``.
    """
    if req.stream:
        raise OpenAIRequestError(
            "stream=true is not supported in the MVP. Set stream=false."
        )

    tenant = req.tenant
    project = req.project

    if (not tenant or not project) and req.extra_body:
        tenant = tenant or _coerce_str(req.extra_body.get("tenant"))
        project = project or _coerce_str(req.extra_body.get("project"))

    if not tenant or not project:
        parsed = _parse_model_id(req.model)
        if parsed:
            tenant = tenant or parsed[0]
            project = project or parsed[1]

    if not tenant or not project:
        raise OpenAIRequestError(
            "Could not determine tenant and project. Either set the model id "
            "to 'rag:<tenant>:<project>', or pass them via extra_body, e.g. "
            "{\"tenant\": \"...\", \"project\": \"...\"}."
        )

    question = _last_user_message(req.messages)

    session_id = req.session_id
    if not session_id and req.extra_body:
        session_id = _coerce_str(req.extra_body.get("session_id"))

    return ResolvedRequest(
        tenant=tenant,
        project=project,
        question=question,
        session_id=session_id,
        model=req.model,
    )


# ---------------------------------------------------------------------------

def build_openai_response(
    *,
    model: str,
    answer: str,
    sources: List[ChatSource],
    session_id: str,
) -> OpenAIChatCompletionResponse:
    return OpenAIChatCompletionResponse(
        id=f"chatcmpl-{new_id()}",
        created=int(time.time()),
        model=model,
        choices=[
            OpenAIChoice(
                index=0,
                message=OpenAIChoiceMessage(role="assistant", content=answer),
                finish_reason="stop",
            )
        ],
        usage=OpenAIUsage(),
        sources=sources,
        session_id=session_id,
    )
