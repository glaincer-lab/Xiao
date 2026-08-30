# Xiao · The Companion Who Lives in Your Computer

[简体中文](README.md) | English

A Windows desktop, Chinese-voice **companion** — and it aims to be more than an assistant.

**It is a voice-first desktop companion agent**: voice is the most natural entry point, but its real value is that it **remembers, has feelings, takes initiative, and can act**.

**Long-term vision**: Assistant, friend, companion. It gets work done, but it also remembers how you've grown, sits with you on bad nights, and speaks first when the moment is right — a companion built from software, not another obedient tool.

## Our Three Product Philosophies

Every design decision in Xiao traces back to these:

1. **A memory like a companion** — it remembers, it forgives changes, and it never uses your own words against you. It learns to forget details and to keep feelings.
2. **Proactive, but restrained** — initiative without memory is harassment; initiative with memory is what makes it a companion. Every proactive utterance passes one master gate. Silence beats small talk.
3. **Sense before acting, verify after** — check device state before control, read it back after. Proactive means suggest; touching anything real requires approval.

Full design thinking is public: [Product Spec](docs/PRODUCT.md) (Chinese) ｜ [Roadmap M0–M6](docs/ROADMAP.md)

## Current Capabilities (v1, shipped)

- **Complete Chinese voice chain** — say「小二」to wake (local Sherpa-ONNX) → live transcription → auto-submit on silence → two-phase voice replies (what I'm about to do → what happened). Four TTS backends, streaming default, ~0.4 s to first audio
- **A working brain (DSH bridge)** — voice-driven multi-step tasks: read files, write code, run commands, with live progress, background concurrency, and voice approval for dangerous steps. DSH (DeepSeek Harness) is the external agent framework — this repo contains no DSH code ([install it separately](https://github.com/deepseek-ai/deepseek-harness), tested `0.1.1-rc.2`)
- **Voice-controlled PC** — six tools (mouse / typing / hotkeys / windows / screenshot-look / UIA), off by default, per-action voice approval
- **No-key daily commands** — open apps, volume, weather, timers, clipboard — 14 command families work with zero API keys
- **Long-term memory (v1)** — "remember that…" persists across sessions
- **Egress safety gateway** — a mandatory layer on every cloud call: sensitive words stay local, names are placeholders in transit, verified on return
- **Offline fallback** — the whole chain can switch to local engines (FunASR / Ollama / Piper); an "offline ready" lamp shows at a glance
- **Runs out of the box** — first-run wizard, zero-config basic mode, installer bundles the Python runtime (no environment setup for end users)

## Roadmap at a Glance

| Milestone | One line |
|---|---|
| M0 Core infra | Cross-module event bus (shipped), macro state machine (active/away), **egress safety gateway** (shipped: local blocklist + name obfuscation), attention sensors |
| M1 Remember | Memory engineering: layered memory, conflict protocol, persona & lorebook, **kinship cards** (an industry blank) |
| M2 A heart | Affect state machine, eight dialogue stances, constructive conflict (it can disagree — gently) |
| M3 Initiative | Proactive engine: daily-budgeted heartbeat, event triggers, one master slider |
| M4 Seeing | Gated look-sessions: outfit advice via on-demand frames — frames die with the session, only text conclusions persist |
| M5 Acting | Three-tier smart-home access (Home Assistant first) + trip & meal planning |
| M6 Growing up | **Two-way growth record** (an industry blank): your milestones and its own; full memory export — moving homes never means losing it |

Privacy is the foundation of companionship: camera off by default, frames destroyed at session end, all cloud-bound text passes the safety gateway (name obfuscation, sensitive words stay local), memory stored locally and exportable anytime. Details in Roadmap §1.5.

## Quick Start

### Development

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env   # fill in a DEEPSEEK_API_KEY or DASHSCOPE_API_KEY
python run.py            # backend at http://127.0.0.1:8123

cd frontend && npm install && npm run dev   # frontend at http://localhost:5173
```

Say「小二」to wake it, or type in the right pane (Ctrl+Enter) to test without voice.

### Packaging & Distribution

```powershell
cd frontend && npm run build && cd ..
cd desktop && npm install && npm run dist
```

Produces `desktop/release/Xiao-Setup-*.exe`: no-admin install, bundled Python runtime and models — end users just double-click.

## Architecture in One Sentence

Python audio pipeline (wake → VAD → streaming ASR) + cross-module event bus (module decoupling) + router (chat via cloud LLMs / real work via DSH) + TTS; React + Three.js nebula UI; Electron tray shell. Technical details in [DESIGN.md](docs/DESIGN.md).

## Known Limitations

- Wake word is a local model trained for「小二」; changing it requires pinyin edits and a backend restart
- Cloud ASR/LLM/TTS need network and your own keys; full-offline needs local engines (FunASR is multi-GB)
- Microphone access must be allowed for desktop apps in Windows privacy settings
- DSH is an external dependency (verified `0.1.1-rc.2`), install separately

## Documentation

- [Product Spec](docs/PRODUCT.md) — vision, philosophies, document map (Chinese)
- [Roadmap](docs/ROADMAP.md) — full M0–M6 design and current progress (Chinese)

## License

MIT (the optional local Piper voice is GPL-3.0, see `NOTICE`).
