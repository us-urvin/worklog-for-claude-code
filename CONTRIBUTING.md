# Contributing

Thanks for your interest! Bug reports, fixes, docs and ideas are all welcome.

## Ground rules

- **Standard library only at runtime.** The one exception is `tomli` on Python 3.10. Test and dev tools are fine.
- **Python 3.10+.** CI tests 3.10, 3.11 and 3.12.
- **Privacy first.** Never add anything that sends data anywhere except the user's own data repo. Records must
  hold metadata only, never file contents or tool output. New free-text fields go through `redact()`.
- **Hooks must never break Claude Code.** The `hook` command must be fast, print nothing and never raise.
- **Don't include real data.** No real transcripts, e-mails, account ids or paths in issues, tests or fixtures.
  Use `claude-worklog demo` or the fake helpers in `tests/` instead.

## Development setup

```bash
git clone https://github.com/us-urvin/worklog-for-claude-code.git
cd worklog-for-claude-code
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
```

Try your changes without touching your own setup:

```bash
claude-worklog demo            # web UI on fake data at http://localhost:8766
```

## Tests and lint

```bash
pytest -q
ruff check .
```

Tests run in a temporary HOME (see the `home` fixture in `tests/test_accounts.py`), so they never read or write
your real `~/.claude`, config, state or data repo. Please keep it that way, and add tests for any behaviour you
add or change.

## Code map

`cli.py` (commands), `web.py` (UI server), `services.py` (systemd units), `hooks.py`, `identity.py` (logged-in
account per session), `parser.py` (reads the JSONL), `collector.py` (projects, remotes, commits, records),
`redact.py`, `store.py` (git sync with a file lock), `report.py` (merging, summaries, Markdown), `demo.py`.

The Claude Code transcript format is not a documented API. Parser changes should stay defensive: skip and count
what you don't understand rather than fail.

## Pull requests

1. For anything bigger than a small fix, open an issue first so we can agree on the approach.
2. Branch from `main`, keep the change focused, and match the style of the surrounding code.
3. Make sure `pytest` and `ruff check .` pass. CI runs both on every PR.
4. Add a line under **Unreleased** in `CHANGELOG.md` for user-visible changes.
5. Fill in the PR template. For UI changes, add a screenshot taken with `claude-worklog demo`.

By contributing you agree that your contributions are licensed under the MIT License and that you will follow
the [Code of Conduct](CODE_OF_CONDUCT.md).

## Releasing (maintainers)

1. Bump `version` in `pyproject.toml` and `worklog/__init__.py`, and move the **Unreleased** notes into a new
   version section in `CHANGELOG.md`.
2. Commit, then `git tag vX.Y.Z && git push origin main vX.Y.Z`.
3. The `release` workflow builds the package and publishes it to PyPI through Trusted Publishing.
