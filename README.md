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
- 💬 **Your claude.ai chats too** (optional): import claude.ai's data export to see chats next to your coding work

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

Mostly yes, with a few things you should know.

- 🔒 **Everything stays on your computer**, plus your own private git repo if you set one up. No cloud service, no tracking.
- ✂️ **Only short notes are saved**: the first 300 characters of each prompt, token counts, file names and commit messages. Your files and Claude's replies are never saved, but a code snippet you paste into a prompt can end up in those 300 characters.
- 🧹 **Common secrets are hidden (best effort)**: well-known API key formats, `password=…` / `token=…` pairs, e-mail addresses and IP addresses are replaced with `[REDACTED]` before saving. This can miss things, such as a password written in a normal sentence, so keep your data repo **private**.
- 🤖 **Optional AI summaries** use your own Claude account (`claude -p`). Turn them off in Settings to keep everything fully local.
- 💬 **Imported claude.ai chats** keep only chat titles and short redacted snippets of your own messages. Claude's replies, chat summaries and attachments are never saved.

[More about privacy →](docs/GUIDE.md#privacy)

## Good to know

- **Claude Code is tracked automatically.** claude.ai chats have no live connection: you import its data export on the **Sources** page (upload it, or let worklog pick it up from your Downloads folder). Exports have no token counts. See [claude.ai chats](docs/GUIDE.md#claudeai-chats).
- **Linux only** for now (it uses systemd).
- It reads Claude Code's local log files. Their format isn't official, so a Claude Code update could break it for a while.

## More

- 📖 [Full guide](docs/GUIDE.md): several computers, several accounts, all commands, uninstalling
- 🐛 [Report a bug](https://github.com/us-urvin/worklog-for-claude-code/issues/new/choose) · 🔐 [Security](SECURITY.md) · 🤝 [Contributing](CONTRIBUTING.md) · 📝 [Changelog](CHANGELOG.md)

## License

[MIT](LICENSE) © 2026 Urvin
