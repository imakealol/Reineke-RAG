# Ollama-Tuning für Reineke-RAG

Empfohlenes Setup für **M4 Max** + lokales RAG + bis zu 10 parallele Nutzer.

## Modell-Auswahl

| Rolle | Modell | VRAM | Hinweis |
| --- | --- | --- | --- |
| Embedder | `bge-m3` | ~2 GB | 1024-dim, multilingual, 8k Kontext — verarbeitet auch breite XLSX. `nomic-embed-text` ist zu schwach für deutsche Policy-PDFs. |
| Chat (Qualität) | `qwen2.5:32b-instruct-q4_K_M` | ~26 GB | Beste deutsche Antwortqualität. Für 1-2 parallele Nutzer. |
| Chat (Throughput) | `qwen2.5:14b-instruct-q4_K_M` *oder* `gemma2:9b-instruct-q5_K_M` | 9-14 GB | 2-3× schneller, 3-4 parallele Sessions. Für 5-10 Nutzer. |

Im RAG-Kontext (gutes Retrieval) ist 14b ↔ 32b Qualitätsunterschied klein, weil das LLM hauptsächlich extrahiert.

## Server-Konfiguration

### Variante A — Interaktiv (Dev / Test)

```bash
pkill -f "ollama serve" 2>/dev/null

export OLLAMA_KEEP_ALIVE=24h
export OLLAMA_NUM_PARALLEL=2
export OLLAMA_MAX_LOADED_MODELS=3
export OLLAMA_FLASH_ATTENTION=1
export OLLAMA_HOST=127.0.0.1:11434

ollama serve
```

### Variante B — macOS-LaunchAgent (Always-On)

```bash
mkdir -p ~/Library/LaunchAgents
cat > ~/Library/LaunchAgents/com.ollama.server.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>            <string>com.ollama.server</string>
  <key>ProgramArguments</key> <array>
    <string>/opt/homebrew/bin/ollama</string>
    <string>serve</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>OLLAMA_KEEP_ALIVE</key>          <string>24h</string>
    <key>OLLAMA_NUM_PARALLEL</key>        <string>2</string>
    <key>OLLAMA_MAX_LOADED_MODELS</key>   <string>3</string>
    <key>OLLAMA_FLASH_ATTENTION</key>     <string>1</string>
    <key>OLLAMA_HOST</key>                <string>127.0.0.1:11434</string>
    <key>OLLAMA_NUM_THREADS</key>         <string>10</string>
  </dict>
  <key>RunAtLoad</key>     <true/>
  <key>KeepAlive</key>     <true/>
  <key>StandardOutPath</key><string>/tmp/ollama.out.log</string>
  <key>StandardErrorPath</key><string>/tmp/ollama.err.log</string>
</dict>
</plist>
EOF

launchctl unload ~/Library/LaunchAgents/com.ollama.server.plist 2>/dev/null
launchctl load   ~/Library/LaunchAgents/com.ollama.server.plist
```

## Env-Variablen

| Variable | Empf. | Wirkung | Ohne Setting |
| --- | :-: | --- | --- |
| `OLLAMA_KEEP_ALIVE` | `24h` | Modell bleibt nach Last-Request 24 h im VRAM | Default 5 min → erste Anfrage nach Idle braucht 5-15 s zum Reload, Connection-Drops möglich |
| `OLLAMA_NUM_PARALLEL` | `2` | 2 parallele Generationen pro Modell | Default 1 → zweiter Request wartet, einzelne Drops bei manchen Versionen |
| `OLLAMA_MAX_LOADED_MODELS` | `3` | Embedder + Chat + 1 Reserve gleichzeitig im VRAM | Default 1 → ständiges Re-Loading bei jedem `/chat` |
| `OLLAMA_FLASH_ATTENTION` | `1` | FlashAttention-2 auf MPS, ~10-20 % schneller | Performance liegt brach |
| `OLLAMA_HOST` | `127.0.0.1:11434` | Bindet nur Loopback | Default ist OK, explizit ist sauberer; `0.0.0.0:11434` falls Remote-Zugriff |
| `OLLAMA_NUM_THREADS` | ~70 % der Cores | CPU-Threads für Tokenizer/Pre-/Post | Default = alle Kerne, kann unter Volllast bremsen |

### Nicht setzen

- `OLLAMA_NUM_PARALLEL ≥ 4` mit `qwen2.5:32b` → OOM bei Folge-Anfragen.
- `OLLAMA_KV_CACHE_TYPE=q4` → mehr parallele Sessions, aber spürbare Qualitätseinbuße bei langen Antworten. Nur wenn VRAM eng wird.
- `OLLAMA_LLM_LIBRARY` manuell — Ollama wählt automatisch Metal auf macOS.

## RAG-Backend `.env` passend dazu

```env
OLLAMA_BASE_URL=http://localhost:11434

EMBEDDING_MODEL=bge-m3
CHAT_MODEL=qwen2.5:32b-instruct-q4_K_M
# Throughput-Variante:
# CHAT_MODEL=qwen2.5:14b-instruct-q4_K_M

CHAT_TEMPERATURE=0.1
CHAT_MAX_TOKENS=1024

# Optional — Recall verbessern:
RETRIEVAL_TOP_K=8
MIN_RETRIEVAL_SCORE=0.30
```

## Betriebs-Checks

```bash
# Welche Modelle sind im VRAM?
ollama ps

# Alle installierten Modelle
ollama list

# Logs (LaunchAgent)
tail -f /tmp/ollama.err.log

# VRAM-Nutzung auf M4 Max (unified memory)
sudo powermetrics --samplers gpu_power -i 2000 -n 1 2>/dev/null | grep "GPU"

# Ollama erreichbar?
curl -s http://localhost:11434/api/tags | jq '.models[].name'
```

## TL;DR — Eine-Zeile-Fix

```bash
pkill -f "ollama serve"
OLLAMA_KEEP_ALIVE=24h OLLAMA_NUM_PARALLEL=2 OLLAMA_MAX_LOADED_MODELS=3 OLLAMA_FLASH_ATTENTION=1 ollama serve
```

Behebt: Idle-Reloads, parallele Connection-Drops, Embedder-/Chat-Eviction.

## Backend-Seite ist bereits getuned

Bestehende Fixes (siehe Code-Historie):

- HTTPX-Timeouts aufgeteilt: `connect=10s, read=600s, write=30s`
- Retry bei transienten Ollama-Drops (`ConnectError`, `ReadError`, `RemoteProtocolError`)
- Kurzlebige DB-Sessions: Connection nicht während LLM-Generation gehalten
- SQLite `busy_timeout=30s` + WAL-Modus
- Vollständiges Exception-Logging im Server-Output

Mit Env-Variablen oben + diesem Backend-Stand: 5-8 parallele Nutzer mit `qwen2.5:32b`, 10-15 mit dem 14b-Modell.
