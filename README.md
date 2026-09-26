# Worklog for Claude Code

**See what you did with Claude Code each day, and how many tokens it used. Automatically.**

[![CI](https://github.com/us-urvin/worklog-for-claude-code/actions/workflows/ci.yml/badge.svg)](https://github.com/us-urvin/worklog-for-claude-code/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/worklog-for-claude-code)](https://pypi.org/project/worklog-for-claude-code/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

> Independent community project, not affiliated with or endorsed by Anthropic.

![Daily view: a 14-day token chart, what you worked on per project, and tokens by account](https://raw.githubusercontent.com/us-urvin/worklog-for-claude-code/main/docs/images/day.png)

## What is this?

You use Claude Code all day. At the end of it, this tool gives you a simple page that shows:

- ✅ **What you worked on**: a short summary for each project
- 📊 **How many tokens you used**: per day, per account and per model
- 💻 **All in one place**: even if you use several computers or several Claude accounts

It works in the background. You don't have to do anything after setup.

![How it works: Claude Code → claude-worklog → your private repo → daily work log](https://raw.githubusercontent.com/us-urvin/worklog-for-claude-code/main/docs/images/how-it-works.svg)

## Try it first (no setup)

```bash
pipx run --spec worklog-for-claude-code claude-worklog demo
```

Then open **http://localhost:8766**. It shows fake example data and doesn't touch your files. Press Ctrl+C to stop.

## Install

You need Linux, Python 3.10+ and [Claude Code](https://code.claude.com/docs).

```bash
# 1. Install
pipx install worklog-for-claude-code

# 2. Create a folder for your data and start the tool
git init ~/claude-worklog-data
claude-worklog install-service
```

**3.** Open **http://worklog.localhost:8765**, fill in the short setup form, then click **Install hook**.

Done. Your work log fills up as you use Claude Code.

> Using more than one computer? Put the data folder in a **private** GitHub/GitLab repo so all your machines share it. See [Full setup](docs/GUIDE.md#full-setup).

## Is my data private?

Yes.

- 🔒 **Everything stays on your computer**, plus your own private git repo if you set one up. No cloud service, no tracking.
- ✂️ **Only short notes are saved**: your prompts (shortened), token counts, file names and commit messages. Never your code, and never Claude's replies.
- 🧹 **Secrets are removed**: API keys, passwords, tokens and e-mail addresses are cleaned out before saving.
- 🤖 **Optional AI summaries** use your own Claude account (`claude -p`). Turn them off in Settings to keep everything fully local.

[More about privacy →](docs/GUIDE.md#privacy)

## Good to know

- Works with **Claude Code only**, not claude.ai chat.
- **Linux only** for now (it uses systemd).
- It reads Claude Code's local log files. Their format isn't official, so a Claude Code update could break it for a while.

## More

- 📖 [Full guide](docs/GUIDE.md): several computers, several accounts, all commands, uninstalling
- 🐛 [Report a bug](https://github.com/us-urvin/worklog-for-claude-code/issues/new/choose) · 🔐 [Security](SECURITY.md) · 🤝 [Contributing](CONTRIBUTING.md) · 📝 [Changelog](CHANGELOG.md)

## License

[MIT](LICENSE) © 2026 Urvin
