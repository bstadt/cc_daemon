# Repository Guidelines

## Project Structure & Modules
- `src/claudeconnect/` — CLI and core logic (`cli.py`, `config.py`, `auth.py`, `session.py`, `encryption.py`, `transcripts.py`, `scanner.py`).
- `src/claudeconnect/skills/` — skill docs and command docs.
- `tests/` — pytest suite (unit + `@pytest.mark.integration`).
- `server/` — FastAPI sync server (optional for local dev/integration).
- `scripts/` — helper scripts (e.g., `cc-startup-hook.sh`).
- `docs/` — design notes and session docs.

## Build, Test, and Development
- Create env + install dev deps:
  - `python -m venv .venv && source .venv/bin/activate`
  - `pip install -e '.[test]'`
- Run CLI locally: `claudeconnect --help`
- Run tests (unit only): `pytest -m "not integration" -q`
- Run integration tests: `pytest -m integration -s`
- Build wheel (optional): `pip install build && python -m build`
- Run API server (optional):
  - `pip install -r server/requirements.txt`
  - `uvicorn server.app:app --reload`

## Coding Style & Naming
- Python 3.9+, PEP 8, 4‑space indent, 100‑char line guide.
- Use type hints and docstrings for public functions/classes.
- Naming: `snake_case` for functions/vars, `CamelCase` for classes, `UPPER_SNAKE` for constants.
- Prefer `pathlib.Path`, avoid global state, keep CLI interactions in `cli.py` (Click).

## Testing Guidelines
- Framework: pytest (see `pyproject.toml`). Tests live in `tests/`, files `test_*.py`, functions `test_*`.
- Mark network/server‑dependent tests with `@pytest.mark.integration`.
- Useful env for integration: `CC_TEST_ALICE`, `CC_TEST_BOB`. Use `tmp_path`/temp dirs.
- Add unit tests for new code; keep unit tests offline and deterministic.

## Commit & Pull Requests
- Messages: imperative and scoped. Suggested prefixes: `Fix:`, `Feat:`, `Docs:`, `Refactor:`, `Test:`.
- PRs should include: clear description, rationale, before/after notes, tests, and docs/README updates when relevant.
- Keep changes minimal; avoid unrelated refactors. Note any breaking changes.

## Security & Configuration
- Never commit secrets. Tokens/config live under `~/.config/claudeconnect/` (client) and `.env` for local use.
- Validate inputs at boundaries; be cautious with filesystem and network operations.

## Agent‑Specific Notes
- Follow this guide, update tests/docs with code changes, and keep patches small and focused.
- When creating issues, format them cleanly with a short summary, bullet points, and a clear goal/outcome.
