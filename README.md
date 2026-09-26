# worklog-for-claude-code

**An automatic daily work log and token report for Claude Code, across all your accounts and machines. Your data stays on your machines and in your own private git repo.**

> Independent community project, not affiliated with or endorsed by Anthropic.

[![CI](https://github.com/us-urvin/worklog-for-claude-code/actions/workflows/ci.yml/badge.svg)](https://github.com/us-urvin/worklog-for-claude-code/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/worklog-for-claude-code)](https://pypi.org/project/worklog-for-claude-code/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

<!-- SCREENSHOT: take it with `claude-worklog demo`, save as docs/screenshot.png, then replace this comment with:
![The day view: token chart, projects worked on, tokens by account](https://raw.githubusercontent.com/us-urvin/worklog-for-claude-code/main/docs/screenshot.png)
-->

## What it does

At the end of the day, you get a page (and a Markdown file) that answers *"what did I actually do today, and how many tokens did it take?"*:

- **What you worked on, per project.** Written by Claude from your prompts, edited files and commits, or a plain list of your prompts if you prefer. The same repo cloned on two machines counts as one project. Work outside any git repo gets its own "R&D / misc" section.
- **Token usage per account and per model**, with a 14-day chart.
- **Several Claude accounts**: separate config folders, or one folder where you switch with `/login`.
- **Several machines**: each one syncs small, redacted records to a private git repo you own (GitHub, GitLab, Bitbucket or self-hosted). Any of them can show the combined view.

It runs by itself. A Claude Code hook syncs when a session ends, an hourly timer catches anything missed, and a nightly timer writes the summaries. A small web UI on `localhost` shows the result and handles settings.

## Quick start

Want to look first? `pipx run --spec worklog-for-claude-code claude-worklog demo` opens the UI on fake data and touches none of your files.

On one Linux machine with Claude Code installed:

```bash
# 1. Install
pipx install worklog-for-claude-code

# 2. Create a local data repo and start the background services
git init ~/claude-worklog-data
claude-worklog install-service

# 3. Open http://worklog.localhost:8765, finish setup, then press "Install hook" in Settings
```

That's all. To sync several machines through a private remote repo, see [Full setup](#full-setup).

## Privacy

This tool reads your Claude Code transcripts, so it is built to keep as little as possible.

**What is stored.** One JSON record per session per day, in your data repo:
- date, start and end time, account **name** and device **name** (the names you choose),
- project name, git remote as `host/owner/repo` (credentials always removed), branch names, and the project path relative to your home folder,
- token counts per model, number of API calls and tool calls,
- your first 30 prompts in the session, **cut to 300 characters and redacted**,
- paths of files Claude edited (relative to the repo, or to your home folder outside a repo), and the short hash and redacted subject of **your own** commits that day.

**What is never stored.** File contents, Claude's replies, tool output, command output, your git e-mail, credentials. Account ids and login e-mails (used to tell accounts apart) stay in a local file (`~/.local/state/claude-worklog/accounts.json`, mode 600) and are never written to the data repo.

**What is redacted.** Before anything is written, prompts and commit subjects are scrubbed of common API keys (Anthropic, OpenAI, GitHub, GitLab, Bitbucket, AWS, Slack, Google), JWTs, bearer tokens, private keys, `password=…`-style pairs, credentials in URLs, e-mail addresses and IPv4 addresses. This is **best effort**, which is why the data repo **must be private**.

**Where data goes.** Records stay on your machine and in the git remote you configure for the data repo, and nowhere else. There is no telemetry and no server run by this project. One exception you control: **AI summaries** (on by default, `use_ai = true`) run your own `claude -p` once per project per day. That sends that project's redacted prompts, edited file names and commit subjects to Anthropic under your Claude account, the same way any Claude Code session does, and uses tokens. Set `use_ai = false` (or untick it in Settings) to keep summaries fully local as plain prompt lists.

**The web UI** listens on 127.0.0.1 and ::1 only. It rejects requests with a non-loopback `Host` header (which blocks DNS rebinding), requires a CSRF token and a same-origin `Origin` on every form, loads nothing external, and sends a strict Content-Security-Policy. See [SECURITY.md](SECURITY.md) to report problems.

## Limitations

- **Claude Code only.** Chats in claude.ai (web, desktop, mobile) aren't written to local transcripts, so they aren't tracked.
- **The transcript format is unofficial.** Claude Code's `~/.claude/projects/**/*.jsonl` files are not a documented, stable API and may change in any update. The parser is defensive: lines it doesn't understand are skipped and counted, never fatal. Still, after a Claude Code upgrade it's worth comparing totals with another tool such as `npx ccusage daily`.
- **Linux with systemd only, for now.** The background services are systemd user units. It is tested on Ubuntu. macOS and Windows aren't supported yet, and contributions are welcome.
- **Tokens are not cost.** On Pro/Max plans, usage counts against limits rather than being billed, so no dollar figures are shown.
- **Days use each device's local time.**
- **Retention.** Claude Code deletes old transcripts after `cleanupPeriodDays` (in `settings.json`). Collection runs at least hourly, so this only matters for backfilling with `collect --days N` (up to 30).

## Full setup

About 5 minutes per machine, one time.

**1. Create the data repo (once).** On GitHub, GitLab or Bitbucket, create an **empty, private** repo, for example `claude-worklog-data`.

**2. Give each machine non-interactive access to it.** Background services have no SSH agent, so use a **deploy key with write access, scoped to this one repo**:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/claude_worklog -N "" -C "claude-worklog $(hostname)"
cat ~/.ssh/claude_worklog.pub     # add this as a deploy key (write access) on the data repo
cat >> ~/.ssh/config <<'CFG'
Host worklog-git
  HostName github.com          # or gitlab.com / bitbucket.org
  User git
  IdentityFile ~/.ssh/claude_worklog
  IdentitiesOnly yes
CFG
ssh -T worklog-git              # accept the host key once
```

**3. Install and start it:**

```bash
sudo apt update && sudo apt install -y pipx git
pipx ensurepath && source ~/.bashrc
pipx install worklog-for-claude-code
claude-worklog install-service               # on ONE machine (it builds the nightly summaries)
claude-worklog install-service --no-report   # on every other machine
```

**4. Open <http://worklog.localhost:8765>** and finish setup: pick a unique device name, set the data repo (paste `worklog-git:<you>/claude-worklog-data.git` and it is cloned for you), and add your first account. Then press **Install hook** for each account in Settings.

Already started with a local-only repo from the quick start? Connect it with `git -C ~/claude-worklog-data remote add origin worklog-git:<you>/claude-worklog-data.git`. The next sync pushes to it.

`install-service` registers three systemd **user** units that start whenever you log in:

| Unit | What it does |
|---|---|
| `claude-worklog-web.service` | The web UI at `worklog.localhost:8765` (loopback only; restarts itself if it crashes) |
| `claude-worklog-collect.timer` | Hourly sync, a backstop for sessions whose hook didn't fire |
| `claude-worklog-report.timer` | Summaries at 23:30, re-run at 00:20 for late work (one machine only) |

The timers use `Persistent=true`, so a run missed while the machine was asleep or off happens when it wakes. To keep the services running when you aren't logged in (for example on a desktop you reach over SSH), run `sudo loginctl enable-linger $USER`.

`*.localhost` names resolve to your own machine in Chrome, Firefox and systemd-resolved, so no `/etc/hosts` edit is needed. `http://localhost:8765` works too. For another port: `claude-worklog install-service --port 9000`.

The config file is `~/.config/claude-worklog/config.toml`. The UI edits it for you; `claude-worklog init` writes a commented example.

## Multiple accounts

**One folder per account.** In the UI, open **Settings → Add an account**, name it (for example `work`), then log in to it once in a terminal. The UI shows the exact command:

```bash
CLAUDE_CONFIG_DIR=~/.claude-work claude    # then type /login
```

Then press **Install hook** next to it. Logging in has to happen in a terminal because Claude Code's login is interactive.

**Several accounts in one folder.** If you keep one folder (such as `~/.claude`) and switch with `/logout` and `/login`, each session is still counted under the account that was logged in when it **started**:

1. Install both hooks: `claude-worklog install-hook` (or **Install hook** in Settings). The `SessionStart` hook reads only the account id and e-mail from Claude Code's `~/.claude.json` (never credentials) and saves `session → account` in the local state file. It prints nothing.
2. Log in with each account and start at least one session.
3. Open **Settings → Detected accounts**. Each login appears with a masked e-mail such as `a***@example.com`. Type a name and press **Name this account**.

This writes an entry like the following to `config.toml` (you can also write it by hand):

```toml
[[accounts]]
name = "personal"            # no match_*: the folder's default
config_dir = "~/.claude"

[[accounts]]
name = "work"
config_dir = "~/.claude"
match_account_id = "…"       # or: match_email = "you@example.com"
```

- Sessions match by account id, then e-mail (case-insensitive). Anything else, including sessions from before the hook was installed, goes to the folder's **default** account (the one without `match_*`).
- If you name a login after its sessions were synced, the next sync moves those records to the right account. Only hook-recorded sessions are moved.
- Tracking is per session. If you `/login` to another account *inside* a running session, the session stays with the account it started with. Start a new session after switching.
- Summaries run `claude -p` with whichever account is logged in to the summarizer account's folder.

## Command line

The UI covers everyday use. Everything is also available from the terminal:

```bash
claude-worklog demo                            # try the UI on fake data (port 8766, nothing touched)
claude-worklog doctor                          # check config, data repo, accounts and hooks
claude-worklog report --stdout                 # today's report in the terminal
claude-worklog report --date 2026-09-25        # write reports/2026-09-25/<device>.md to the data repo
claude-worklog report --no-ai                  # plain prompt lists, no claude -p
claude-worklog collect --days 7                # backfill a week from this machine
claude-worklog install-hook [--account NAME]   # add the Claude Code hooks (backs up settings.json)
claude-worklog install-service [--no-report] [--port N]
claude-worklog serve --port 8765               # run the UI in the foreground
systemctl --user status claude-worklog-web     # service state
journalctl --user -u claude-worklog-web -f     # live logs
```

Data repo layout (every path is written by a single device, so there are no merge conflicts):

```
records/<date>/<device>/<account>/<session>.json
reports/<date>/<device>.md
```

## Uninstall

```bash
systemctl --user disable --now claude-worklog-web.service claude-worklog-collect.timer claude-worklog-report.timer
rm ~/.config/systemd/user/claude-worklog-*
systemctl --user daemon-reload
pipx uninstall worklog-for-claude-code
```

Then remove the `SessionStart` and `SessionEnd` entries that run `claude-worklog hook` from each account's `settings.json` (a backup from before the install sits next to it as `settings.json.bak-*`). Optionally delete `~/.config/claude-worklog/` and `~/.local/state/claude-worklog/`. Your data repo is left untouched. Delete it yourself if you no longer want it.

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the dev setup (`pip install -e '.[dev]' && pytest`), and use `claude-worklog demo` to try changes without touching your own data. Please follow the [Code of Conduct](CODE_OF_CONDUCT.md), and report security issues privately as described in [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE) © 2026 Urvin
