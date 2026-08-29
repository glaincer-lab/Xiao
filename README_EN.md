# Xiao (小二) · Voice-Powered Work Assistant for Windows

[简体中文](README.md) | English

An always-on Chinese voice assistant for Windows: wake it with「小二」(Xiao Er), talk, and it transcribes in real time — then speaks back a two-stage reply ("what I'm about to do" first, then the result).

**What it is**: a voice frontend for a general-purpose agent (DeepSeek Harness, DSH). It turns DSH into a "voice-controlled agent workbench" — not just weather and web search, but reading/writing files, running commands, and multi-step agent loops. The voice layer only does three things: wake, transcribe, and route; the hard part (agent loop, coding tools, subagents, knowledge base) is delegated to DSH.

**Pipeline**: Sherpa-ONNX local Chinese wake word (free, offline, zero training) → Silero VAD → Aliyun streaming ASR (cloud `qwen-audio` default + `fun-asr` dialect fallback; local FunASR as fallback) → routing (chat via DeepSeek/Qwen/OpenAI/GLM/Kimi cloud, or local Ollama / MiniCPM-o (local vLLM); work via DSH) → voice approval for dangerous actions → speech (Qwen realtime streaming default, edge-tts free cloud, Piper offline, MiniCPM-o local vLLM).

**Multi-provider**: wake / ASR / TTS / LLM each support multiple providers (card list + create/edit modal + one-click switch; model names are free-form, follow official docs). Dangerous actions (network / writing outside workspace / delete / install / system changes) require voice approval; DSH headless mode is fail-closed. Long tasks can run in the background with completion notification.

Stack: Python (FastAPI + WebSocket) backend + React (Vite + TypeScript + Three.js) frontend + Electron tray shell; depends on DeepSeek Harness as the brain.

## About DeepSeek Harness

Xiao is not a voice brain built from scratch — it is a **voice frontend for DeepSeek Harness (DSH)**.

- **What DSH is**: an "everything is a plugin" general-purpose agent framework that provides the real intelligence — agent loops, coding tools (read/write files, run commands), subagents, workflows, knowledge-base retrieval.
- **Xiao's role**: only three voice jobs — wake, transcribe, and route. Whether a sentence goes to "chat" or "work (DSH)" is decided by the routing layer.
- **How it bridges**: via `backend/bridge/` (the only place that knows DSH), calling `dsh --profile headless`; multi-turn context is maintained by the bridge. Dangerous actions flow back to voice approval through the `plugins/xiao-approval-bridge` plugin.
- **DSH is an external dependency**: this repo contains no DSH code. Install [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) first (tested with version `0.1.1-rc.2`).

> In one line: DSH provides the "read files, run commands, multi-step iteration" brain; Xiao provides the voice layer.

## Screenshot

![Xiao interface](docs/screenshot.png)

## Features

- **Wake**: local Sherpa-ONNX keyword spotting, native Chinese「小二」, zero training, offline
- **VAD**: Silero VAD (ONNX), robust speech/noise separation
- **ASR**: Aliyun streaming (qwen-audio-3.0-asr-flash-streaming default + fun-asr-flash-8k-realtime dialect fallback), local FunASR fallback; multiple providers
- **Brain**: DeepSeek / Qwen / OpenAI / GLM / Kimi (all OpenAI-compatible) + local Ollama / MiniCPM-o (local vLLM), function calling
- **DSH execution**: routes to DeepSeek Harness for real tasks (read/write files, run commands, multi-step agent loops), with multi-turn context, voice approval, background long tasks
- **TTS**: 4 providers — Qwen realtime streaming (default, ~0.4s first audio, syncs with subtitles) / edge-tts free cloud / Piper offline / MiniCPM-o (local vLLM)
- **Live voice line**: real microphone RMS level pushed over WebSocket, rendered as a live waveform (not a fake animation)
- **Two-stage reply**: plan → execute → result
- **Pluggable tools**: web search, open app/URL, weather, reminders; Huawei smart-home reserved
- **UI**: React + Vite + Three.js, three-column layout (conversation / nebula status orb + live transcript / input) + glassmorphism

## Architecture

```
[Python backend]                              [Web frontend]
  mic (sounddevice) → wake (Sherpa-ONNX)        3 columns: chat + nebula orb
     → VAD (Silero)                              + live transcript + input
     → streaming ASR (cloud Paraformer / local FunASR) ──WS──► realtime text
     → router → chat (DeepSeek direct) or dsh (DSH)
     → tools / DSH bridge
     → TTS (Qwen realtime / edge-tts / Piper / MiniCPM-o) ──► speech
```

Stack: **Python 3.11/3.12 + FastAPI + WebSocket** (backend) | **React + Vite + TypeScript + Three.js** (frontend).

## Directory Structure

```
backend/
  agent.py          two-stage reply agent (plan → execute → result)
  core.py           audio pipeline state machine + soft config hot-reload
  main.py           FastAPI entry + WebSocket relay + REST API
  config.py         config loading (.env + config.yaml)
  settings_schema.py  settings field registry (schema-driven UI)
  router.py         routing (auto/chat/dsh + keywords)
  perms.py          permission model (standing grants + keyword prediction + deferred)
  tasks.py          background long tasks (queue + concurrency + persistence)
  bridge/           ★ the only place that knows DSH (headless CLI + multi-turn context)
  audio/            mic / vad / wake
  asr/              cloud qwen-audio(default)/fun-asr(dialect fallback) / local FunASR
  llm/              cloud DeepSeek/Qwen/OpenAI/GLM/Kimi / local Ollama / MiniCPM-o (local vLLM)
  tts/              Qwen realtime / edge-tts / Piper / MiniCPM-o
  tools/            search / open / weather / reminders (registry)
  devices/          device adapter abstraction (Huawei smart-home reserved)
  session/state.py  state machine + thread-safe event bus
frontend/           React app
desktop/            Electron tray shell
plugins/            DSH thin plugin (approval bridge xiao-approval-bridge)
config.yaml         non-sensitive config
.env.example        secret template
```

## Quick Start

### 1. Backend

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

copy .env.example .env
# edit .env, fill at least one of DEEPSEEK_API_KEY or DASHSCOPE_API_KEY

python run.py
```

Backend runs at `http://127.0.0.1:8123` (health check `GET /health`).

### 2. Frontend

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173` (the frontend WebSocket connects directly to the backend on 8123, no proxy needed).

### 3. Usage

- Say「小二」(Xiao Er) to wake (or click the wake button)
- Talk; text appears in real time; it auto-submits ~1.5s after you stop
- The assistant first says what it's about to do, then reports the result
- You can also type in the right input box (Ctrl+Enter) to bypass voice and test the full pipeline

## Voice Commands

After waking, these phrases are matched at the code level (not via the LLM; editable in `config.yaml`):

| Phrase | Action | Confirm |
|---|---|---|
| 你退下吧 / 再见 … | return to idle + clear history | no |
| 清空对话 / 重新开始 … | clear history, stay listening | no |
| 你把自己关了吧 / 退出程序 … | two-step confirm → exit backend | yes |

## Routing (chat vs. work)

Which channel a sentence takes is decided by `backend/router.py`; you can also switch manually in the top bar (Auto / Chat / DSH):

| Mode | Behavior |
|---|---|
| `auto` | keyword hit (write code / fix / debug …) → DSH; otherwise → chat |
| `chat` | force chat (DeepSeek direct + built-in tools) |
| `dsh` | force DSH execution |

## Settings

Click "Settings" (top-right): left nav + right scroll, schema-driven (`backend/settings_schema.py`; adding an option only touches the registry).

- **Wake / ASR / TTS / LLM** use multi-provider management: each is a card list with status icon (✅ ready / ⬜ reserved) + name + type, with [Set current] / [Edit] / [Delete]; "＋ Add" opens a modal (pre-filled on edit, API key shown as dots); tunable attributes (voice / rate / temperature / sensitivity) expand inline.
- **Execution / Permissions**: forms (routing mode, DSH keywords, command, timeout, concurrency; approval toggle, phrase lists, standing grants).
- **Audio / UI**: microphone device dropdown (auto-enumerated) / font, text size, etc.

| Stage | Ready (✅) | Reserved (⬜) |
|---|---|---|
| Wake | local Sherpa-ONNX「小二」 | unified MiniCPM-o (local vLLM) |
| ASR | cloud qwen-audio-3.0-asr-flash-streaming (default) / fun-asr-flash-8k-realtime (dialect fallback), local FunASR | unified MiniCPM-o (local vLLM) |
| TTS | Qwen realtime streaming (default) / edge-tts / Piper offline / MiniCPM-o (local vLLM) | — |
| LLM | cloud DeepSeek/Qwen/OpenAI/GLM/Kimi, local Ollama, MiniCPM-o (local vLLM) | — |

## Cloud / Local Switch

| Module | Switch to local | Note |
|---|---|---|
| ASR | `pip install -r requirements-local-asr.txt`, create/select a "local FunASR" provider | FunASR `paraformer-zh`, slower on CPU |
| LLM | install Ollama, `ollama pull qwen2.5:7b`, select "local Ollama" | Ollama's OpenAI-compatible endpoint (no key) |
| LLM (local vLLM) | start local vLLM-omni (`llm.omni` defaults to `localhost:8000`), select "MiniCPM-o" | OpenAI-compatible endpoint; same base_url+model+key trio |

## Extending: Add a Tool

Create a file under `backend/tools/`, implement the `Tool` interface and register it:

```python
from backend.tools.base import Tool

class MyTool(Tool):
    name = "my_tool"
    description = "What this tool does"
    parameters = {
        "type": "object",
        "properties": {"x": {"type": "string", "description": "argument"}},
        "required": ["x"],
    }

    async def run(self, x: str) -> str:
        return f"result: {x}"
```

Then register it in `register_builtin_tools` in `backend/tools/__init__.py` and add its name to `tools.enabled` in `config.yaml`.

## Desktop Shell (Electron · system tray)

Development mode:

```powershell
cd frontend && npm run build && cd ..
cd desktop
npm install
npm start
```

- Closing the window hides to tray; backend and mic keep running
- Tray menu: show/hide, launch on startup, quit
- Auto-starts the backend: prefers `.venv\Scripts\python.exe` (override with `XIAO_PYTHON`)

### Packaging (no Python required)

Publisher steps (online build machine):

```powershell
cd frontend && npm run build && cd ..
cd desktop
npm install
npm run dist
```

Output: `desktop/release/Xiao-Setup-*.exe`. `npm run dist` first runs `scripts/prepare-runtime.ps1`, which assembles `desktop/runtime/python` (embeddable Python + pip dependencies + wake-word/VAD/Piper models; first run downloads from the network, later runs reuse the cache) and hands it to electron-builder.

End users (third-user perspective): double-click the installer — it silently installs to the user directory (`%LOCALAPPDATA%\Programs\Xiao`, **no admin rights needed**) and auto-starts. The installer bundles the Python runtime and all dependencies, so **end users do not need to install Python**. API keys stay in a local `.env` per key hygiene (copy `.env.example` from the install directory, or fill them in the settings panel).

## Development & Testing

Backend uses Python unit tests (`tests/`); the frontend gates on type checks + Lint (`eslint.config.js`, ESLint v9 flat config):

```powershell
# Backend unit tests
.venv\Scripts\python.exe -m unittest discover -s tests

# Frontend gate (typecheck + Lint; both must pass)
cd frontend
npm run check

# Frontend production build (tsc && vite build)
npm run build
```

## Known Limitations

- **Wake word**: Sherpa-ONNX local model; changing it requires updating the pinyin and restarting
- **First run online**: wake model downloads on first run; edge-tts uses Microsoft's online API; Aliyun ASR needs network
- **Microphone**: allow desktop app mic access in Windows privacy settings
- **DSH version**: tested with `0.1.1-rc.2`; the bridge layer is version-locked in one place (`bridge/`)
- **Local FunASR is large**: torch + model ~several GB
- **Piper offline TTS**: optional dependency (GPL-3.0), `pip install -r requirements-local-tts.txt`; Chinese voice `zh_CN-huayan-medium.onnx` shipped in `models/`
- **MiniCPM-o TTS/ASR/wake**: requires a running local vLLM-omni service (`llm.omni` defaults to `localhost:8000`), otherwise no audio

## Docs

- `DESIGN.md`: design (architecture / state machine / routing / bridge / approval / risks) — Chinese
- `ROADMAP.md`: roadmap (phased) — Chinese
- `AGENTS.md`: project rules (third-party-user perspective + engine layering) — Chinese

## License

MIT — see [LICENSE](LICENSE). Model weights are not distributed with this repo; see [NOTICE](NOTICE).
