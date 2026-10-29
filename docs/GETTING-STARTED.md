# Getting started, step by step

This guide is for you if you're not used to the terminal. Each step says what to type, what it does, and what you should see. It takes about 10 minutes.

It's written for **Ubuntu**. Other Linux versions work too, but the install commands in step 1 may be a bit different.

**On this page**
1. [Install the tools Worklog needs](#1-install-the-tools-worklog-needs)
2. [Try the demo (optional)](#2-try-the-demo-optional)
3. [Install Worklog](#3-install-worklog)
4. [Start Worklog](#4-start-worklog)
5. [Finish setup in your browser](#5-finish-setup-in-your-browser)
6. [Your first day](#6-your-first-day)

> **How to open a terminal:** press **Ctrl+Alt+T**, or search for "Terminal" in your apps.
> **How to paste into it:** **Ctrl+Shift+V** (plain Ctrl+V doesn't work in most terminals).

## 1. Install the tools Worklog needs

You need three things: **Python** (the language Worklog is written in), **pipx** (installs Python apps cleanly) and **git** (saves your log). Check whether Python is already there:

```bash
python3 --version
```

✅ You should see `Python 3.10` or a higher number. Ubuntu 22.04 and newer already have it.

Now install pipx and git. Your computer asks for your password: type it and press Enter (nothing appears while you type, that's normal).

```bash
sudo apt update && sudo apt install -y pipx git
pipx ensurepath
```

Then **close the terminal and open a new one**, so it picks up the change.

You also need **Claude Code** itself, and you should have used it at least once. If you haven't, follow [Claude Code's setup guide](https://code.claude.com/docs) first.

## 2. Try the demo (optional)

Want to see what Worklog looks like before installing it? Run:

```bash
pipx run --spec worklog-for-claude-code claude-worklog demo
```

Open **http://localhost:8766** in your browser. Everything you see is made-up example data, and none of your files are touched. Click around, then go back to the terminal and press **Ctrl+C** to stop it.

## 3. Install Worklog

```bash
pipx install worklog-for-claude-code
```

✅ You should see a message ending with `done! ✨ 🌟 ✨`.

Check that it worked:

```bash
claude-worklog --help
```

✅ You should see a list of commands. If it says `command not found`, see [Troubleshooting](TROUBLESHOOTING.md#command-not-found-claude-worklog).

## 4. Start Worklog

First, make a folder where your daily log is saved. It's a git folder, so it keeps a history and can be shared between computers later:

```bash
git init ~/claude-worklog-data
```

Then start Worklog in the background:

```bash
claude-worklog install-service
```

✅ You should see `✓ Worklog UI is running: http://worklog.localhost:8765`.

From now on Worklog starts by itself whenever you log in to your computer. You don't need to run this again.

## 5. Finish setup in your browser

Open **http://worklog.localhost:8765**. (If that doesn't open, try **http://localhost:8765**.)

You'll see a short form called **Set up this device**. The suggested values are fine for most people:

| Field | What it means | What to enter |
|---|---|---|
| **Device name** | A name for this computer, shown in your log | Keep the suggestion, or type something like `laptop` |
| **Folder** | Where your log is saved | Keep `~/claude-worklog-data` (the folder from step 4) |
| **Clone URL** | Only for sharing between computers | Leave it empty |
| **Account name** | A name for your Claude account | Keep `personal`, or type `work` |
| **Claude config folder** | Where Claude Code keeps its files | Keep `~/.claude` |

Click **Save setup**. Then open **Settings** at the top and click **Install hook**.

**What's a hook?** It's a small note in Claude Code's settings that says "tell Worklog when a session starts and ends". It doesn't change how Claude Code works. Before adding it, Worklog makes a backup of your Claude Code settings.

## 6. Your first day

Use Claude Code as you normally do. Then open **http://worklog.localhost:8765** (bookmark it!).

- **After each Claude Code session ends**, it appears on today's page with its project, prompts and token count. Don't want to wait? Click **Sync now**.
- **At 23:30 each night**, Worklog writes a short summary of what you did in each project. Want it now? Click **Write summaries**.
- **Use the arrows** at the top to look at earlier days. The chart shows your last 14 days of token use.
- **Sources** lets you import your claude.ai chats ([how](GUIDE.md#claudeai-chats)).
- **Settings** lets you add more Claude accounts or turn off AI summaries.

## What next?

- **Use more than one computer?** Put your log in a private GitHub repo so all computers share it: [Full setup](GUIDE.md#full-setup).
- **Use more than one Claude account?** See [Multiple accounts](GUIDE.md#multiple-accounts).
- **Something not working?** See [Troubleshooting](TROUBLESHOOTING.md).
- **Want to know exactly what's saved?** See [Privacy](GUIDE.md#privacy).

If Worklog is useful to you, a ⭐ [star on GitHub](https://github.com/us-urvin/worklog-for-claude-code) helps others find it.
