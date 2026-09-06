# Contributing

Thanks for taking the time to help. This is a small project — issues and pull
requests are both welcome, and asking before building something large saves
everyone effort.

## Development setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Run the daemon from the checkout:

```bash
export TG_BOT_TOKEN=123:abc TG_CHAT_ID=-1001234567890
python -m bridge_for_agents          # or: bridge-for-agents
```

## Checks

```bash
ruff check .
pytest
```

CI runs both, plus a package build, on Python 3.11–3.13. Please make them pass
before opening a pull request. Formatting is not enforced by a formatter —
match the surrounding style and keep lines within 100 columns.

## Layout

| Path | What lives there |
|---|---|
| `src/bridge_for_agents/bridge.py` | the daemon: channels, hook handlers, HTTP endpoint |
| `src/bridge_for_agents/store.py` | in-memory session and event history (no persistence) |
| `src/bridge_for_agents/audit.py` | append-only record of requests and decisions, and its verifier |
| `src/bridge_for_agents/logs.py` | diagnostic logging: level, rotating private file, redaction filter |
| `src/bridge_for_agents/replies.py` | parsing a typed reply into a decision — pure, and fails safe |
| `src/bridge_for_agents/mcp.py` | MCP stdio server exposing the send-only `notify_user` tool |
| `skills/notify-user/` | Claude Code skill: how to write a notification, and what never to put in one |
| `src/bridge_for_agents/admin.py` | read-only `/admin` routes and the page |
| `src/bridge_for_agents/cli.py` | console entry point |
| `examples/hooks.settings.json` | Claude Code hook configuration to merge into your settings |
| `scripts/make-certs.sh` | local CA, server cert and shared secret |
| `tests/` | pytest suite |

`bridge.py` reads its configuration from the environment **at import time**.
Tests that import it must set `TG_BOT_TOKEN` and `TG_CHAT_ID` first — see
`tests/conftest.py`.

## Pull requests

Ideas worth picking up, and the reasoning behind their priority, are in
[ROADMAP.md](ROADMAP.md); how the thing is actually run is in
[USER-GUIDE.md](USER-GUIDE.md).

- One logical change per PR; keep the diff focused.
- Update `README.md` when you change configuration or behaviour.
- Add a line to the `Unreleased` section of `CHANGELOG.md`.
- New environment variables belong in three places: the module docstring in
  `bridge.py`, the README env table, and the changelog.

## Releasing

The version lives in one place, `src/bridge_for_agents/__init__.py`;
`pyproject.toml` reads it from there.

- **Pre-release**: push to the `test` branch. CI runs, then a
  `X.Y.(Z+1).devN` build goes to TestPyPI. Nothing else is affected, and the
  version is unique per run so a build never collides.
- **Release**: bump `__version__`, add a `CHANGELOG.md` entry, then tag:

  ```bash
  git tag v0.2.0 && git push origin v0.2.0
  ```

  CI runs, the tag is checked against `__version__`, PyPI is checked for that
  version already existing, `twine check` inspects the metadata, and only then
  does it upload.

Both use PyPI Trusted Publishing, so there is no API token in the repository
secrets. It needs a one-off setup on PyPI and TestPyPI: add a trusted publisher
for `spazyCZ/bridge-for-agents` with the workflow name
(`publish-pypi.yml` / `publish-testpypi.yml`) and the environment
(`pypi` / `testpypi`).

## Adding a channel

`Channel` in `bridge.py` is the extension point — implement `send` and `ask`
(and `start` / `stop` if your transport needs them). See the WhatsApp/Twilio
sketch at the end of the README for the shape of a second channel.

## Reporting security issues

Please do not open a public issue for a vulnerability. See [SECURITY.md](SECURITY.md).
