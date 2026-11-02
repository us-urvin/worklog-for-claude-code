# Worklog for Claude Code

**Your Claude Code work, written down for you: what you did each day, how long it took, and what it used.**

A free, open-source **Claude Code usage tracker** and **daily work log**: tokens, cost and hours **per project**, a timesheet, and plain-language summaries of what you did, across all your Claude accounts and computers. It runs on your computer and keeps your data in your own private git repo.

[![Downloads](https://static.pepy.tech/badge/worklog-for-claude-code)](https://pepy.tech/project/worklog-for-claude-code)
[![PyPI](https://img.shields.io/pypi/v/worklog-for-claude-code)](https://pypi.org/project/worklog-for-claude-code/)
[![GitHub stars](https://img.shields.io/github/stars/us-urvin/worklog-for-claude-code?style=flat)](https://github.com/us-urvin/worklog-for-claude-code/stargazers)
[![CI](https://github.com/us-urvin/worklog-for-claude-code/actions/workflows/ci.yml/badge.svg)](https://github.com/us-urvin/worklog-for-claude-code/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](https://github.com/us-urvin/worklog-for-claude-code/blob/main/LICENSE)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/us-urvin/worklog-for-claude-code/badge)](https://scorecard.dev/viewer/?uri=github.com/us-urvin/worklog-for-claude-code)
[![Signed releases](https://img.shields.io/badge/releases-signed%20%26%20verified-brightgreen)](https://github.com/us-urvin/worklog-for-claude-code/blob/main/SECURITY.md#verify-a-release)

🌐 **[Website](https://us-urvin.github.io/worklog-for-claude-code/)** · 📦 **[PyPI](https://pypi.org/project/worklog-for-claude-code/)**

![A tour of Worklog: today, project summaries, editing a project, trends, ask, timesheet and a share card](https://raw.githubusercontent.com/us-urvin/worklog-for-claude-code/main/docs/images/demo.gif)

▶️ **[Watch the 40-second tour](https://github.com/us-urvin/worklog-for-claude-code/blob/main/docs/media/worklog-demo.mp4)**

## What you get

**Every day**
- ✍️ What you worked on, project by project, **in plain words**. Claude writes it with your own account.
- 📍 **Where you left off**: your last projects, open tasks, PRs and tickets.
- ✏️ Wrong project? **Fix it in two clicks**, and Worklog remembers.

**Every week**
- 🧾 A **timesheet** with hours, kind of work and one line per project. Download it for Excel or as CSV.
- 📈 **Trends** compared with last week, in plain sentences, plus **goals** for hours and tokens.
- 🔎 **Ask your worklog**: *"When did I review PR 505?"*

**Anywhere**
- 📱 On your **phone** through the GitHub app, from a summary in your private data repo.
- 🖼️ A **card to share** your week. It shows totals only, never prompts or files.
- 👥 Several **Claude accounts and computers**, even with one-click account switchers.

🔒 **Private**: everything stays on your computer and in your own private git repo. 💻 Runs on **Linux, macOS and Windows**. ⚡ Light and fast.

## Try it (nothing is installed)

```bash
pipx run --spec worklog-for-claude-code claude-worklog demo
```

Open **http://localhost:8766** to look around with made-up data. Press **Ctrl+C** to stop.

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

A checklist shows anything still missing, with the exact command for your computer, and disappears when everything is ticked. Then just use Claude Code as usual.

## A closer look

<table><tr>
<td width="50%"><img src="https://raw.githubusercontent.com/us-urvin/worklog-for-claude-code/main/docs/images/card.png" alt="A project card with a plain-language summary, tag, PR links and Claude's task list"><br><b>Every project, in plain words</b>, with tasks and PRs.</td>
<td width="50%"><img src="https://raw.githubusercontent.com/us-urvin/worklog-for-claude-code/main/docs/images/leaveoff.png" alt="Goals and where you left off"><br><b>Goals and where you left off</b>, at the top of Today.</td>
</tr><tr>
<td width="50%"><img src="https://raw.githubusercontent.com/us-urvin/worklog-for-claude-code/main/docs/images/trends.png" alt="Trends: hours, sessions, tokens and cost compared with last week, in plain sentences"><br><b>Trends</b>, compared with last week.</td>
<td width="50%"><img src="https://raw.githubusercontent.com/us-urvin/worklog-for-claude-code/main/docs/images/ask.png" alt="Ask your worklog: search results for a question"><br><b>Ask your worklog</b> anything about past work.</td>
</tr><tr>
<td width="50%"><img src="https://raw.githubusercontent.com/us-urvin/worklog-for-claude-code/main/docs/images/timesheet.png" alt="Timesheet: hours, kind of work and a one-line summary per day and project"><br><b>A timesheet</b>, ready for Excel.</td>
<td width="50%"><img src="https://raw.githubusercontent.com/us-urvin/worklog-for-claude-code/main/docs/images/share-card.jpg" alt="A share card: hours, sessions, projects and streak for the week"><br><b>A card to share</b> your week.</td>
</tr></table>

## Privacy and trust

- Only short notes are saved: the first 300 characters of your prompts, token counts, file names and commit messages. **Never your files or Claude's replies.**
- Common secrets (API keys, passwords, e-mails) are blanked out before saving. This is best effort, so keep your data repo **private**.
- Summaries and "Ask Claude" use your own Claude account. Turn AI off in Settings to stay fully offline.
- The only other request: once a day, Worklog asks pypi.org whether a new version is out. Nothing about you is sent, and you can turn it off.
- It's a **personal log, not a performance score**. Hours are your active time with Claude, not a measure of your output.
- Every release is built and signed by GitHub, and checked on Linux, macOS and Windows before it's published. [Verify a release →](https://github.com/us-urvin/worklog-for-claude-code/blob/main/SECURITY.md#verify-a-release)

[Privacy in detail →](https://github.com/us-urvin/worklog-for-claude-code/blob/main/docs/GUIDE.md#privacy)

## Questions

**Can I see it on my phone?** Yes. Turn on **Settings → On your phone** on one computer, then open your data repo's `phone` folder in the GitHub app. It stays private.

**Wrong project on a card?** Click **✏️ Edit** to rename it or move the sessions. Worklog can remember the folder you always start in.

**Several accounts, computers or GitHub accounts?** Settings walks you through it. **Test connection** shows which GitHub account your computer signs in as. See [Full setup](https://github.com/us-urvin/worklog-for-claude-code/blob/main/docs/GUIDE.md#full-setup).

**It says Sonnet, but I use Opus?** The "By model" list shows what Claude Code really used. For better summaries, pick **Opus** in Settings → Summaries.

**How do I update?** Click **Update now** when the dashboard says a new version is out. From a terminal: `pipx upgrade --pip-args=--no-cache-dir worklog-for-claude-code`, then click **Restart now** on the page. ([Still the old version?](https://github.com/us-urvin/worklog-for-claude-code/blob/main/docs/TROUBLESHOOTING.md#i-updated-but-the-page-still-shows-the-old-version))

**Something isn't working?** Run `claude-worklog doctor`, or see [Troubleshooting](https://github.com/us-urvin/worklog-for-claude-code/blob/main/docs/TROUBLESHOOTING.md).

**How do I remove it?** `claude-worklog uninstall-service`, then `pipx uninstall worklog-for-claude-code`. Your log stays.

## Like it? ⭐

If Worklog saves you time, please **[star it on GitHub](https://github.com/us-urvin/worklog-for-claude-code)**. It helps others find it.

📘 [Beginner guide](https://github.com/us-urvin/worklog-for-claude-code/blob/main/docs/GETTING-STARTED.md) · 📖 [Full guide](https://github.com/us-urvin/worklog-for-claude-code/blob/main/docs/GUIDE.md) · 🐛 [Report a bug](https://github.com/us-urvin/worklog-for-claude-code/issues/new/choose) · 📝 [Changelog](https://github.com/us-urvin/worklog-for-claude-code/blob/main/CHANGELOG.md)

---

[MIT](https://github.com/us-urvin/worklog-for-claude-code/blob/main/LICENSE) © 2026 Urvin · Not affiliated with or endorsed by Anthropic.
