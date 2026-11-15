# Maestro AI

**A local orchestrator for AI coding agents.** Maestro routes each request to the right engine (Claude, Codex, Gemini/Antigravity, DeepSeek), plans multi-agent work as a dependency graph, and keeps every model inside a daily and weekly quota budget. It ships with a web app (desktop, tablet and phone, installable as a PWA).

🇵🇹 [Versão em português (PT-PT)](README.pt-PT.md)

> **License:** source-available, not open source. Free for personal, study, research and hobby use under [PolyForm Noncommercial 1.0.0](LICENSE). Commercial use needs a paid licence — see [COMMERCIAL.md](COMMERCIAL.md) (reference price: €100 in Europe).

## What it does

- **Engine routing and fallback.** Classifies a request by domain and size, picks the engine, and falls back by quota (e.g. Codex → Antigravity → Claude → DeepSeek).
- **Multi-agent workflows.** Complex requests become a JSON plan (scout → specialist → review), executed as a DAG with parallel read-only tasks. Plans and results are persisted and resumable.
- **Quota budgets.** Weekly quotas are split into daily budgets; DeepSeek has a per-cycle money budget with yellow/red lines for justified extra use.
- **Read-only scouts.** A restricted DeepSeek API mode that can only list folders, read excerpts and grep inside the project.
- **Web studio.** Chats, folders, queue, quota and fleet panels, themes, PT/EN interface.
- **Optional integrations** (NAS media, Home Assistant, 3D printers) live in `maestro_features.py` and are configured by you.

See [ORCHESTRATION.md](ORCHESTRATION.md) (in Portuguese) for the design details and [DEEPSEEK_PRICING.md](DEEPSEEK_PRICING.md) for the cost model.

## Screenshots

<p align="center"><img src="docs/screenshots/maestro-desktop.jpg" width="620" alt="Maestro AI desktop"/> <img src="docs/screenshots/maestro-tablet.jpg" width="190" alt="Maestro AI tablet"/> <img src="docs/screenshots/maestro-mobile.jpg" width="130" alt="Maestro AI phone"/></p>

*Desktop (browser or Windows app window) · tablet (PWA) · phone (PWA). Demo workspace with fictional conversations.*

## Requirements

- Linux (developed on a home NAS), Python 3.11+
- The agent CLIs you want to use, already logged in: `claude`, `codex`, `agy`, and/or a DeepSeek API key
- Optional, for GIF export: `pip install -r requirements-gif.txt` into `.gif-deps/`

## Quick start

```bash
git clone https://github.com/hubertdungen/maestro-ai.git
cd maestro-ai
python3 maestro.py --status          # quotas, resets, balances
python3 maestro.py --plan "your request"   # preview the agent plan
python3 maestro.py "your request"          # run it
python3 maestro_web.py               # web studio on port 8109
python3 -m unittest discover -s tests -q
```

Credentials (API keys, tokens) are read from your environment or local state under `~/.local/state/maestro/`. Never commit them.

## Status

Personal project, used daily by its author. Interfaces change without notice. Issues are welcome; support is best-effort unless covered by a commercial licence.

## License

Copyright © 2026 Hubert Dungen. [PolyForm Noncommercial 1.0.0](LICENSE) for noncommercial use; [commercial licence](COMMERCIAL.md) otherwise. Not legal advice.
