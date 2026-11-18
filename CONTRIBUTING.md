# Contributing to Maestro AI

Thanks for your interest. Maestro AI is a personal project used daily by its author, so changes are reviewed on a best-effort basis.

## Before you start

- Open an issue first for anything bigger than a small fix, so we agree on the direction.
- Licence: contributions are accepted under the repository's [PolyForm Noncommercial 1.0.0](LICENSE) terms; the author may also offer the code under the [commercial licence](COMMERCIAL.md).
- Never commit credentials (API keys, tokens, webhooks). They are read from the environment, `~/.local/state/maestro/` or `~/.config/maestro/ha.env`.

## Workflow

```bash
git clone https://github.com/hubertdungen/maestro-ai.git
cd maestro-ai
git checkout -b my-change
python3 -m unittest discover -s tests -q   # must pass
```

1. Keep each pull request focused on one change.
2. Add or update a test in `tests/` when you change behaviour.
3. Do not commit backup files (`*.bak*`), `__pycache__/` or local state.
4. Open the pull request against `main` and describe what changed and why.

## Language

Code and documentation are in British English (`README.md`) and European Portuguese (`README.pt-PT.md`). Keep both in step when you change the README.
