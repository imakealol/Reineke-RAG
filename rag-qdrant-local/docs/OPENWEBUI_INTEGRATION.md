# OpenWebUI-Integration

Zwei Wege OpenWebUI an das Backend anzubinden — beide funktionieren parallel.

## 1. OpenAI-kompatibler Endpoint (empfohlen für simple Setups)

OpenWebUI fragt das Backend wie eine OpenAI-API ab.

1. **Settings → Connections → OpenAI API → +**
2. **Base URL:** `http://localhost:8000/v1` (Host) oder `http://host.docker.internal:8000/v1` (OpenWebUI in Docker)
3. **API Key:** beliebig (Backend prüft ihn nicht)
4. Im Modell-Picker erscheint `rag:<tenant>:<project>` pro indexiertem Paar.

Backend-Endpunkt: `POST /v1/chat/completions`. Streaming **muss aus** sein (MVP unterstützt kein `stream=true`).

## 2. Pipe-Function (volle Kontrolle, eigene Status-Indikatoren)

Die fertige Pipe liegt unter [`openwebui_pipe.py`](./openwebui_pipe.py). Datei öffnen, **kompletten Inhalt** kopieren, und in OpenWebUI:

1. **Workspace → Functions → +**
2. Code einfügen → **Save**
3. Toggle **Enabled** anschalten
4. **Settings (⚙)** der Function:
   - `rag_url`:
     - OpenWebUI auf dem Host → `http://localhost:8000/chat`
     - OpenWebUI in Docker → `http://host.docker.internal:8000/chat`
   - `tenant` = `reineke`, `project` = `watch` (anpassen je Korpus)
5. Im Chat-Modell-Picker erscheint **Reineke RAG**.

Backend-Endpunkt: `POST /chat`. Vorteile gegenüber Variante 1:
- Liefert OpenWebUI bessere Status-Updates ("Frage Reineke-RAG…")
- Tenant/Project pro Pipe konfigurierbar (mehrere Pipes für mehrere Korpora möglich)
- Fehlermeldungen aus dem Backend werden direkt im Chat angezeigt

### Wichtige Details

- **Streaming:** wird nicht benötigt; die Pipe wartet die volle Antwort ab und liefert sie geschlossen aus.
- **Async:** nutzt `httpx.AsyncClient`, blockt also den OpenWebUI-Event-Loop nicht (sonst Risiko `Connection lost`).
- **Session-Reuse:** OpenWebUI-`chat_id` wird als unsere `session_id` reingereicht — Konversationen gruppieren sich in `chat_messages`.
- **Sources:** das Backend hängt bereits einen `Quellen:`-Block an. Falls nicht, rendert die Pipe selbst aus dem strukturierten `sources`-Array.
- **Backend-Voraussetzung:** muss mit `--host 0.0.0.0` laufen, damit OpenWebUI-Container über `host.docker.internal` reinkommt.

### Update-Workflow

Wenn du die Pipe verbesserst:
1. Datei `docs/openwebui_pipe.py` anpassen
2. In OpenWebUI: **Workspace → Functions → Reineke RAG → ✏ Edit** → Inhalt ersetzen → **Save**
3. Function-Toggle einmal aus/an für sauberen Re-Init
