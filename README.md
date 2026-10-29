# Worklog for Claude Code

**A diary of your Claude Code work, written for you. See what you did each day and how many tokens it used.**

[![Downloads](https://static.pepy.tech/badge/worklog-for-claude-code)](https://pepy.tech/project/worklog-for-claude-code)
[![PyPI](https://img.shields.io/pypi/v/worklog-for-claude-code)](https://pypi.org/project/worklog-for-claude-code/)
[![GitHub stars](https://img.shields.io/github/stars/us-urvin/worklog-for-claude-code?style=flat)](https://github.com/us-urvin/worklog-for-claude-code/stargazers)
[![CI](https://github.com/us-urvin/worklog-for-claude-code/actions/workflows/ci.yml/badge.svg)](https://github.com/us-urvin/worklog-for-claude-code/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](https://github.com/us-urvin/worklog-for-claude-code/blob/main/LICENSE)

> Free and open source. An independent community project, not affiliated with or endorsed by Anthropic.

![Worklog in action: the daily page with stat tiles, a 14-day token chart and what you worked on](https://raw.githubusercontent.com/us-urvin/worklog-for-claude-code/main/docs/images/demo.gif)

▶️ **[Watch the 90-second video](https://github.com/us-urvin/worklog-for-claude-code/blob/main/docs/media/worklog-demo.mp4)**: setup checklist, account switching, the animated dashboard and summaries.

## What is this?

At the end of a day with Claude Code it's easy to forget what you actually did. Worklog keeps track for you and shows it on a simple page in your browser:

- ✅ **What you worked on**: a short summary for each project, every day
- 📊 **How many tokens you used**: per day, per account and per model
- 💻 **Everything in one place**: even if you use several computers or several Claude accounts
- 💬 **Your claude.ai chats too** (optional): see your chats next to your coding work
- 🎉 **A dashboard that's fun to open**: animated charts of when you worked, your streak, and a few surprises (switch them off in Settings)

You set it up once. After that it runs quietly in the background.

![How it works: Claude Code → claude-worklog → your private repo → daily work log](https://raw.githubusercontent.com/us-urvin/worklog-for-claude-code/main/docs/images/how-it-works.svg)

## What it looks like

![A day in Worklog: stat tiles, your streak, a 14-day chart and a daily fun fact](https://raw.githubusercontent.com/us-urvin/worklog-for-claude-code/main/docs/images/day.png)

![When you worked, the last 5 weeks with your streak, and today's projects](https://raw.githubusercontent.com/us-urvin/worklog-for-claude-code/main/docs/images/insights.png)

<table><tr>
<td width="50%"><img src="https://raw.githubusercontent.com/us-urvin/worklog-for-claude-code/main/docs/images/checklist.png" alt="The setup checklist: what's missing, with the command for your system"><br><b>Setup checklist</b>: shows what's missing for your system, then disappears.</td>
<td width="50%"><img src="https://raw.githubusercontent.com/us-urvin/worklog-for-claude-code/main/docs/images/notice.png" alt="New account found: name it"><br><b>Switch accounts any way you like</b>: new logins and folders are noticed for you.</td>
</tr><tr>
<td width="50%"><img src="https://raw.githubusercontent.com/us-urvin/worklog-for-claude-code/main/docs/images/accounts.png" alt="Settings: several Claude accounts, with each login named once"><br><b>Several accounts</b>: each folder or login gets a name.</td>
<td width="50%"><img src="https://raw.githubusercontent.com/us-urvin/worklog-for-claude-code/main/docs/images/summary-model.png" alt="Settings: pick Opus to write the daily summaries"><br><b>Pick the model</b> that writes your summaries.</td>
</tr><tr>
<td width="50%"><img src="https://raw.githubusercontent.com/us-urvin/worklog-for-claude-code/main/docs/images/day-light.png" alt="The daily page in light mode"><br><b>Light mode</b> follows your system.</td>
<td width="50%"><img src="https://raw.githubusercontent.com/us-urvin/worklog-for-claude-code/main/docs/images/phone.png" alt="The daily page on a phone-sized screen" width="220"><br><b>Fits any screen.</b></td>
</tr></table>

## Who is it for?

- **Freelancers and consultants** who need to know what they did for which client
- **Anyone writing a daily standup** or a weekly status update
- **People with several computers or Claude accounts** who want one combined view
- **Anyone curious about their token usage** day by day

## Try it in 1 minute (nothing is installed or changed)

Copy this into a terminal:

```bash
pipx run --spec worklog-for-claude-code claude-worklog demo
```

Then open **http://localhost:8766** in your browser. You'll see the app filled with made-up example data. Press **Ctrl+C** in the terminal to stop it.

> Don't have `pipx`? The [step-by-step guide](https://github.com/us-urvin/worklog-for-claude-code/blob/main/docs/GETTING-STARTED.md) shows how to install it.

## Install in 3 steps

Works on **Linux, macOS and Windows**. You need **Python 3.10 or newer**, **git** and **[Claude Code](https://code.claude.com/docs)**. The app checks all of this for you and shows the missing steps for your system.

**Step 1. Install the app**

```bash
pipx install worklog-for-claude-code
```

**Step 2. Make a folder for your log and start the app**

<details open><summary><b>Linux and macOS</b> (Terminal)</summary>

```bash
git init ~/claude-worklog-data
claude-worklog install-service
```
</details>
<details><summary><b>Windows</b> (PowerShell)</summary>

```powershell
git init "$HOME\claude-worklog-data"
claude-worklog install-service
```
</details>

The first line creates a folder where your daily log is saved. The second starts Worklog and makes it start again by itself every time you log in (with systemd on Linux, launchd on macOS, and the Startup list plus Task Scheduler on Windows; no admin rights needed).

**Step 3. Finish setup in your browser**

Open **http://worklog.localhost:8765** (or http://localhost:8765), fill in the short form (the suggested values are fine), then follow the **Get Worklog ready** checklist at the top. It shows what's still missing, with the exact command for your system and a Copy button, and disappears once everything works.

That's it. Use Claude Code as usual, and your log fills up on its own.

🆕 New to the terminal? Follow the **[step-by-step guide for beginners](https://github.com/us-urvin/worklog-for-claude-code/blob/main/docs/GETTING-STARTED.md)**.
Using more than one computer? See **[Full setup](https://github.com/us-urvin/worklog-for-claude-code/blob/main/docs/GUIDE.md#full-setup)**.

## Is my data private?

Yes, with a few things you should know.

- 🔒 **It stays on your computer**, plus your own private git repo if you set one up. There is no cloud service and no tracking.
- ✂️ **Only short notes are saved**: the first 300 characters of each prompt, token counts, file names and commit messages. Your files and Claude's replies are never saved. A code snippet you paste into a prompt can end up in those 300 characters.
- 🧹 **Common secrets are hidden (best effort)**: well-known API key formats, `password=…` / `token=…` pairs, e-mail addresses and IP addresses are replaced with `[REDACTED]` before saving. This can miss things, such as a password written in a normal sentence, so keep your data repo **private**.
- 🤖 **The daily summaries are written by your own Claude account** (`claude -p`). Turn them off in Settings to keep everything fully local.
- 💬 **Imported claude.ai chats** keep only chat titles and short redacted snippets of your own messages. Claude's replies and attachments are never saved.

[More about privacy →](https://github.com/us-urvin/worklog-for-claude-code/blob/main/docs/GUIDE.md#privacy)

## Questions

**Does it cost anything?**
No. Worklog is free and open source. The optional daily summaries use your own Claude account, a small amount each day. You can turn them off.

**Will it slow down Claude Code?**
No. It only reads Claude Code's log files after a session ends, and once an hour as a backup.

**Does it work on Mac or Windows?**
Yes, from version 0.4.0: Linux, macOS and Windows (WSL works too). Mac and Windows support is new, so please [report anything odd](https://github.com/us-urvin/worklog-for-claude-code/issues/new/choose).

**I switch accounts with a one-click tool. Does that work?**
Yes. While Worklog runs, it notices every account switch, whether it happens with `/login` or any account-switching app, and counts each session under the right account, even without the hook. New logins and new Claude folders show up as a notice with a one-click fix.

**Do I need a GitHub account?**
Only if you want to combine several computers. On one computer, a local folder is enough.

**It says Sonnet, but I use Opus. Why?**
The **By model** list shows the model Claude Code itself recorded for each reply. Depending on your plan, Claude Code may use Sonnet unless you pick another model with `/model`, and sub-agents often run on a smaller model (the list shows how much came from sub-agents). Run `claude-worklog doctor` to see each account's default model.

**How do I get better summaries?**
Open **Settings → Summaries** and choose **Opus** as the *Model for summaries*. By default summaries use the account's default model, which is often Sonnet.

**I use several Claude accounts. How do I set that up?**
Open **Settings**. If each account has its own folder, add each folder as an account. If you switch with `/login` in one folder, start one session with each login, then name each login once. Naming also moves that login's sessions from the last 30 days. [Details →](https://github.com/us-urvin/worklog-for-claude-code/blob/main/docs/GUIDE.md#multiple-accounts)

**Where are my claude.ai chats?**
claude.ai doesn't share chats with apps directly, so you import its data export on the **Sources** page. [How to import →](https://github.com/us-urvin/worklog-for-claude-code/blob/main/docs/GUIDE.md#claudeai-chats)

**The page is empty or doesn't open. What now?**
See [Troubleshooting](https://github.com/us-urvin/worklog-for-claude-code/blob/main/docs/TROUBLESHOOTING.md), or run `claude-worklog doctor` to check your setup.

**How do I remove it?**
See [Uninstall](https://github.com/us-urvin/worklog-for-claude-code/blob/main/docs/GUIDE.md#uninstall). Your saved log is never deleted for you.

## Good to know

- Claude Code is tracked automatically. claude.ai chats come from an export you import, and the export has no token counts.
- It reads Claude Code's local log files. Their format isn't official, so a Claude Code update could break it for a while.

## Like it? ⭐

If Worklog saves you time, please **[give it a star on GitHub](https://github.com/us-urvin/worklog-for-claude-code)**. It's free, takes one click, and helps other people find it.

- 📘 [Beginner guide](https://github.com/us-urvin/worklog-for-claude-code/blob/main/docs/GETTING-STARTED.md) · 📖 [Full guide](https://github.com/us-urvin/worklog-for-claude-code/blob/main/docs/GUIDE.md) · 🛠️ [Troubleshooting](https://github.com/us-urvin/worklog-for-claude-code/blob/main/docs/TROUBLESHOOTING.md)
- 🐛 [Report a bug or ask for a feature](https://github.com/us-urvin/worklog-for-claude-code/issues/new/choose) · 🤝 [Contributing](https://github.com/us-urvin/worklog-for-claude-code/blob/main/CONTRIBUTING.md) · 🔐 [Security](https://github.com/us-urvin/worklog-for-claude-code/blob/main/SECURITY.md) · 📝 [Changelog](https://github.com/us-urvin/worklog-for-claude-code/blob/main/CHANGELOG.md)

## License

[MIT](https://github.com/us-urvin/worklog-for-claude-code/blob/main/LICENSE) © 2026 Urvin
