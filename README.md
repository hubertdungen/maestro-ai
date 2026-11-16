<p align="center"><img src="maestro-mark.svg" width="84" alt=""/></p>
<h1 align="center">Maestro AI</h1>
<p align="center"><b>One front door for every AI coding agent you pay for.</b><br/>Routes each request to the right engine, splits big jobs into a multi-agent plan, and keeps every model inside its quota.</p>
<p align="center"><a href="README.pt-PT.md">🇵🇹 Português</a> · <a href="#features">Features</a> · <a href="#the-strategy-panel">Strategy panel</a> · <a href="#themes-worlds">Themes</a> · <a href="#quick-start">Quick start</a> · <a href="ORCHESTRATION.md">Design</a></p>
<p align="center"><img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white"/> <img alt="License" src="https://img.shields.io/badge/license-PolyForm%20NC%201.0-blue"/> <img alt="Engines" src="https://img.shields.io/badge/engines-Claude%20%C2%B7%20Codex%20%C2%B7%20Gemini%20%C2%B7%20DeepSeek-8A2BE2"/> <img alt="PWA" src="https://img.shields.io/badge/PWA-desktop%20%C2%B7%20tablet%20%C2%B7%20phone-0A7"/></p>

<p align="center"><img src="docs/screenshots/maestro-hero.jpg" width="900" alt="Maestro AI: chat, live agent tree and quotas"/></p>

> **License:** source-available, not open source. Free for personal, study, research and hobby use under [PolyForm Noncommercial 1.0.0](LICENSE). Commercial use needs a paid licence — see [COMMERCIAL.md](COMMERCIAL.md) (reference price: €100 in Europe).

## Why

Subscriptions and API credits run out at different times, each CLI has its own limits, and a long task can burn a week of quota in an afternoon. Maestro treats them as one fleet: it knows what each engine has left, routes by domain and size, and falls back automatically when one is exhausted.

## How it works

```text
request ─► triage ─► simple?  ─► one engine ─► answer
             (domain,   │
              size,     └─ big? ─► plan (JSON DAG)
              quota)               ├─ scout      (read-only, cheap)
                                   ├─ specialist (writes code)  ┐ parallel
                                   └─ reviewer   (checks it)    ┘ where independent
                                              │ findings? ─► back to the specialist, same turn
                              delivery gate ◄─┘ ─► answer + Strategy panel
                              (reply text + real git state; fixes in place, never requeues)
```

### Delivery, not reports

An orchestrator is only useful if what comes back is finished. Three mechanisms enforce that:

- **One delivery contract** for every writer (single turns, plan nodes, fix passes): commit, push, merge and deploy belong to the turn; the only things that need confirmation are deleting data the work did not create, spending money and sending something to third parties.
- **Review closes inside the turn.** A read-only reviewer never has the last word on unfinished work: its findings become the specialist's work list in the same turn, and a request for changes can never end in a read-only plan.
- **A gate that checks reality.** A reply that asks for permission, declares pending work or says "press Ctrl+F5" is refused and fixed in place. The gate also reads the repositories the turn touched: source files changed and left uncommitted, or commits left unpushed, go back to the maker.

`tools/ver.py URL --texto "…" [--movel]` gives every engine eyes: a screenshot plus a check of the rendered text, desktop or phone, using the Chromium that Playwright already installed.

## Features

| | |
|---|---|
| **Engine routing and fallback** | Classifies by domain and size, then falls back by quota (e.g. Codex → Antigravity → Claude → DeepSeek). |
| **Multi-agent workflows** | Complex requests become a dependency graph (scout → specialist → review), run with parallel read-only tasks. Plans and results are persisted and resumable. |
| **Strategy panel** | Beside the chat: the orchestrator's reasoning, the agent graph, per-node status, engine/model used and a decision timeline. Follows the request you are reading; with none in view it shows the latest or live plan. |
| **Quota budgets** | Weekly quotas split into daily budgets; DeepSeek has a per-cycle money budget with a daily bar and yellow/red lines for justified extra use. |
| **Read-only scouts and reviewers** | A restricted DeepSeek API mode that can only list folders, read excerpts and grep inside the project; Claude reviewers get inspection-only shell (`git status/diff/log/show` and the page checker). |
| **Conversation-aware routing** | A follow-up inherits the domain and level of the work it continues; when you say the last attempt failed, the next one goes to a single stronger engine. |
| **Real parallelism** | Requests on different apps run at the same time on different subscriptions; a cheap Sentinel decides independence, and same-plan writers stay exclusive. |
| **Web studio** | Chats and folders with archive, request queue, live quota and fleet panels, six visual worlds, PT/EN UI, installable PWA for desktop, tablet and phone. |
| **Interactive answers** | Replies can render inline HTML, tables, charts and step-by-step Q&A cards per platform. |
| **Optional integrations** | NAS media, Home Assistant and 3D printers live in `maestro_features.py`; you configure them. |

Design details: [ORCHESTRATION.md](ORCHESTRATION.md) (in Portuguese). Cost model: [DEEPSEEK_PRICING.md](DEEPSEEK_PRICING.md).

## The Strategy panel

<p align="center"><img src="docs/screenshots/strategy-panel.jpg" width="380" alt="Strategy panel"/></p>

Every multi-agent request gets a live plan next to the chat:

- **Orchestrator thinking** — why the work was split the way it was.
- **Agent graph** — scout → specialist → reviewer, with status, engine and model per node.
- **Decision timeline** — start, finish and guidance events with timestamps.
- **Always there** — follows the request you are reading, shows the live plan while it runs, and becomes a slide-over drawer on narrow windows and phones.

## Screenshots

The **Strategy panel** (right of the chat) shows the plan the orchestrator built for the request: its reasoning, who does what, and what is done.

<p align="center"><img src="docs/screenshots/maestro-tablet.jpg" width="260" alt="Tablet (PWA)"/> &nbsp; <img src="docs/screenshots/maestro-mobile.jpg" width="170" alt="Phone (PWA)"/></p>

*Tablet and phone run the same interface as an installable PWA; the Windows app is the same UI in its own window.*

### Themes ("worlds")

Each world re-skins colour, type and effects of the whole app. Pick one from the logo menu.

<table><tr>
<td align="center"><img src="docs/screenshots/theme-matrix.jpg" width="300" alt="Matrix"/><br/><sub>Matrix</sub></td>
<td align="center"><img src="docs/screenshots/theme-scifi.jpg" width="300" alt="Sci-Fi HUD"/><br/><sub>Sci-Fi HUD</sub></td>
<td align="center"><img src="docs/screenshots/theme-cyberpunk.jpg" width="300" alt="Cyberpunk"/><br/><sub>Cyberpunk</sub></td>
</tr><tr>
<td align="center"><img src="docs/screenshots/theme-hesperia.jpg" width="300" alt="Hesperia"/><br/><sub>Hesperia</sub></td>
<td align="center"><img src="docs/screenshots/theme-dune.jpg" width="300" alt="Dune · Arrakis"/><br/><sub>Dune · Arrakis</sub></td>
<td align="center"><img src="docs/screenshots/theme-dungen.jpg" width="300" alt="Hubert Dungen"/><br/><sub>Hubert Dungen</sub></td>
</tr></table>

*Captures use a demo workspace with fictional conversations; quota bars show demo values.*

## Requirements

- Linux (developed on a home NAS), Python 3.11+
- The agent CLIs you want to use, already logged in: `claude`, `codex`, `agy`, and/or a DeepSeek API key
- Optional, for GIF export: `pip install -r requirements-gif.txt` into `.gif-deps/`
- Optional, for visual verification (`tools/ver.py`): a Playwright Chromium in `~/.cache/ms-playwright` (`npx playwright install chromium`)

## Quick start

```bash
git clone https://github.com/hubertdungen/maestro-ai.git
cd maestro-ai
python3 maestro.py --status                # quotas, resets, balances
python3 maestro.py --plan "your request"   # preview the agent plan
python3 maestro.py "your request"          # run it
python3 maestro_web.py                     # web studio on port 8109
python3 -m unittest discover -s tests -q
```

Credentials (API keys, tokens) are read from your environment or local state under `~/.local/state/maestro/`. Never commit them.

## Status

Personal project, used daily by its author. Interfaces change without notice. Issues are welcome; support is best-effort unless covered by a commercial licence.

## License

Copyright © 2026 Hubert Dungen. [PolyForm Noncommercial 1.0.0](LICENSE) for noncommercial use; [commercial licence](COMMERCIAL.md) otherwise. Not legal advice.


<p align="center"><a href="https://dlforge.dungenlabs.com"><img src="docs/branding/dlforge-logo.svg" alt="DL Forge" width="120"></a><br><a href="https://dlforge.dungenlabs.com">DL Forge</a></p>
