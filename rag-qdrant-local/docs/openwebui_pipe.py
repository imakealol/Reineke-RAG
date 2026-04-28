"""
title: Reineke RAG Pipe
author: Reineke
version: 0.2.0

Calls the local rag-qdrant-local backend at POST /chat and renders the
sources block into the assistant message. Uses httpx.AsyncClient so the
event loop stays free while the LLM is generating.

Install in OpenWebUI:
  Workspace → Functions → +
  paste this whole file, Save, then enable the toggle.

Configuration (Function → Settings ⚙):
  rag_url   — http://host.docker.internal:8000/chat   (Docker-on-Mac)
              http://localhost:8000/chat              (host network)
  tenant    — reineke
  project   — watch

Usage in chat:
  Pick "Reineke RAG" from the model picker. The user's last message is sent
  to /chat as the `question`; OpenWebUI's chat_id is reused as our
  session_id so the backend's chat_messages table groups conversations.

Notes:
  - stream=true is NOT supported by the backend MVP — keep streaming OFF.
  - Sources are appended to the assistant message body and also returned
    structurally by /chat. The pipe falls back to its own renderer if the
    backend's textual "Quellen:" block isn't already present.
"""

from typing import Optional, Callable, Awaitable
from pydantic import BaseModel, Field
import httpx


def extract_event_info(event_emitter) -> tuple[Optional[str], Optional[str]]:
    if not event_emitter or not event_emitter.__closure__:
        return None, None
    for cell in event_emitter.__closure__:
        if isinstance(request_info := cell.cell_contents, dict):
            chat_id = request_info.get("chat_id")
            message_id = request_info.get("message_id")
            return chat_id, message_id
    return None, None


class Pipe:
    class Valves(BaseModel):
        rag_url: str = Field(
            default="http://host.docker.internal:8000/chat",
            description="Full URL of the rag-qdrant-local /chat endpoint.",
        )
        tenant: str = Field(default="reineke")
        project: str = Field(default="watch")
        timeout_seconds: int = Field(default=240)
        enable_status_indicator: bool = Field(default=True)

    def __init__(self):
        self.type = "pipe"
        self.id = "reineke_rag_pipe"
        self.name = "Reineke RAG"
        self.valves = self.Valves()

    async def emit_status(
        self,
        __event_emitter__: Callable[[dict], Awaitable[None]],
        level: str,
        message: str,
        done: bool,
    ):
        if not (__event_emitter__ and self.valves.enable_status_indicator):
            return
        await __event_emitter__(
            {
                "type": "status",
                "data": {
                    "status": "complete" if done else "in_progress",
                    "level": level,
                    "description": message,
                    "done": done,
                    "hidden": done,  # hide the indicator on completion
                },
            }
        )

    @staticmethod
    def _format_sources(sources: list[dict]) -> str:
        if not sources:
            return ""
        lines = ["", "**Quellen:**"]
        seen = set()
        for s in sources:
            key = (
                s.get("file_name"),
                s.get("page"),
                s.get("sheet"),
                s.get("row_start"),
                s.get("row_end"),
                s.get("chunk_index"),
            )
            if key in seen:
                continue
            seen.add(key)
            parts = [str(s.get("file_name", "?"))]
            if s.get("page") is not None:
                parts.append(f"Seite {s['page']}")
            if s.get("sheet"):
                parts.append(f'Sheet "{s["sheet"]}"')
            if s.get("row_start") is not None and s.get("row_end") is not None:
                parts.append(f"Zeilen {s['row_start']}-{s['row_end']}")
            score = s.get("score")
            if isinstance(score, (int, float)):
                parts.append(f"score={score:.2f}")
            lines.append("- " + ", ".join(parts))
        return "\n".join(lines)

    async def pipe(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
        __event_call__: Callable[[dict], Awaitable[dict]] = None,
    ) -> str:
        await self.emit_status(
            __event_emitter__, "info", "Frage Reineke-RAG…", False
        )

        chat_id, _ = extract_event_info(__event_emitter__)
        messages = body.get("messages", [])
        if not messages:
            await self.emit_status(
                __event_emitter__, "error", "No messages found", True
            )
            return "No messages found in the request body"

        question = ""
        for m in reversed(messages):
            if m.get("role") == "user" and (m.get("content") or "").strip():
                question = m["content"].strip()
                break
        if not question:
            await self.emit_status(
                __event_emitter__, "error", "No user question found", True
            )
            return "No user question found"

        payload = {
            "tenant": self.valves.tenant,
            "project": self.valves.project,
            "question": question,
            "session_id": str(chat_id) if chat_id else None,
        }

        try:
            async with httpx.AsyncClient(timeout=self.valves.timeout_seconds) as client:
                response = await client.post(
                    self.valves.rag_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
        except httpx.HTTPError as exc:
            await self.emit_status(
                __event_emitter__, "error", f"RAG request failed: {exc}", True
            )
            return f"Error contacting RAG backend: {exc}"

        if response.status_code != 200:
            err = f"RAG backend returned {response.status_code}: {response.text}"
            await self.emit_status(__event_emitter__, "error", err, True)
            return err

        data = response.json()
        answer = (data.get("answer") or "").strip()
        sources = data.get("sources") or []

        if "Quellen:" not in answer:
            answer = f"{answer}\n{self._format_sources(sources)}".rstrip()

        await self.emit_status(__event_emitter__, "info", "Fertig", True)
        return answer
