# Worklog for Claude Code

**A diary of your Claude Code work, written for you. See what you did each day and how many tokens it used.**

[![Downloads](https://static.pepy.tech/badge/worklog-for-claude-code)](https://pepy.tech/project/worklog-for-claude-code)
[![PyPI](https://img.shields.io/pypi/v/worklog-for-claude-code)](https://pypi.org/project/worklog-for-claude-code/)
[![GitHub stars](https://img.shields.io/github/stars/us-urvin/worklog-for-claude-code?style=flat)](https://github.com/us-urvin/worklog-for-claude-code/stargazers)
[![CI](https://github.com/us-urvin/worklog-for-claude-code/actions/workflows/ci.yml/badge.svg)](https://github.com/us-urvin/worklog-for-claude-code/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](https://github.com/us-urvin/worklog-for-claude-code/blob/main/LICENSE)

![Worklog: today's tokens, sessions and time, a 14-day chart and what you worked on](https://raw.githubusercontent.com/us-urvin/worklog-for-claude-code/main/docs/images/demo.gif)

▶️ **[Watch the 90-second video](https://github.com/us-urvin/worklog-for-claude-code/blob/main/docs/media/worklog-demo.mp4)**

## What it does

- ✅ **Writes down what you worked on**, project by project, every day. Claude writes the summary.
- 📊 **Shows how many tokens you used**, per day, account and model.
- 👥 **Handles several Claude accounts and computers**, even if you switch accounts with a one-click app.
- 🔒 **Stays private**: everything lives on your computer (and your own private git repo, if you want).
- 💻 **Runs on Linux, macOS and Windows**, quietly in the background.

## Try it (nothing is installed)

```bash
pipx run --spec worklog-for-claude-code claude-worklog demo
```

Open **http://localhost:8766** to see it with made-up data. Press **Ctrl+C** to stop.

## Install in 3 steps

You need **Python 3.10+**, **git** and **[Claude Code](https://code.claude.com/docs)**. No pipx yet? See the [beginner guide](https://github.com/us-urvin/worklog-for-claude-code/blob/main/docs/GETTING-STARTED.md).

**1. Install**

```bash
pipx install worklog-for-claude-code
```

**2. Create a folder for your log and start Worklog**

```bash
git init ~/claude-worklog-data          # Windows PowerShell: git init "$HOME\claude-worklog-data"
claude-worklog install-service
```

**3. Open http://worklog.localhost:8765** (or http://localhost:8765) and fill in the short form.

A checklist then shows anything still missing, with the exact command for your computer. When everything is ticked, it disappears. That's it: use Claude Code as usual.

## A closer look

<table><tr>
<td width="50%"><img src="https://raw.githubusercontent.com/us-urvin/worklog-for-claude-code/main/docs/images/insights.png" alt="When you worked, the last 5 weeks and today's projects"><br>When you worked, your streak, and where the time went.</td>
<td width="50%"><img src="https://raw.githubusercontent.com/us-urvin/worklog-for-claude-code/main/docs/images/checklist.png" alt="Setup checklist"><br>A setup checklist with steps for your system.</td>
</tr><tr>
<td width="50%"><img src="https://raw.githubusercontent.com/us-urvin/worklog-for-claude-code/main/docs/images/notice.png" alt="New account found"><br>New accounts are noticed for you.</td>
<td width="50%"><img src="https://raw.githubusercontent.com/us-urvin/worklog-for-claude-code/main/docs/images/update.png" alt="A new version is out: Update now"><br>Updates in one click.</td>
</tr></table>

## Privacy

- Only short notes are saved: the first 300 characters of your prompts, token counts, file names and commit messages. Never your files or Claude's replies.
- Common secrets (API keys, passwords, e-mails) are blanked out before saving. This is best effort, so keep your data repo **private**.
- Summaries are written by your own Claude account. Turn them off in Settings to stay fully offline.
- The only other request: once a day, Worklog asks pypi.org whether a new version is out. Nothing about you is sent, and you can turn it off.

[Privacy in detail →](https://github.com/us-urvin/worklog-for-claude-code/blob/main/docs/GUIDE.md#privacy)

## Questions

**It says Sonnet, but I use Opus?** The "By model" list shows what Claude Code really used. To get better summaries, pick **Opus** in Settings → Summaries.

**Several accounts or computers?** Settings walks you through it. For several computers, see [Full setup](https://github.com/us-urvin/worklog-for-claude-code/blob/main/docs/GUIDE.md#full-setup).

**How do I update?** Click **Update now** when the dashboard says a new version is out, or run `pipx upgrade worklog-for-claude-code`.

**Something isn't working?** Run `claude-worklog doctor`, or see [Troubleshooting](https://github.com/us-urvin/worklog-for-claude-code/blob/main/docs/TROUBLESHOOTING.md).

**How do I remove it?** `claude-worklog uninstall-service`, then `pipx uninstall worklog-for-claude-code`. Your log stays.

## Like it? ⭐

If Worklog saves you time, please **[star it on GitHub](https://github.com/us-urvin/worklog-for-claude-code)**. It helps others find it.

📘 [Beginner guide](https://github.com/us-urvin/worklog-for-claude-code/blob/main/docs/GETTING-STARTED.md) · 📖 [Full guide](https://github.com/us-urvin/worklog-for-claude-code/blob/main/docs/GUIDE.md) · 🐛 [Report a bug](https://github.com/us-urvin/worklog-for-claude-code/issues/new/choose) · 📝 [Changelog](https://github.com/us-urvin/worklog-for-claude-code/blob/main/CHANGELOG.md)

---

[MIT](https://github.com/us-urvin/worklog-for-claude-code/blob/main/LICENSE) © 2026 Urvin · Not affiliated with or endorsed by Anthropic.
