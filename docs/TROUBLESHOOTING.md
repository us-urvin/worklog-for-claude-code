# Troubleshooting

Something not working? Start with the health check. It looks at your setup and says what's wrong:

```bash
claude-worklog doctor
```

Lines starting with ✓ are fine. Lines starting with ✗ show the problem. If none of the fixes below help, [open an issue](https://github.com/us-urvin/worklog-for-claude-code/issues/new/choose) and paste the `doctor` output (it hides your e-mail addresses).

**Problems**
- [command not found: claude-worklog](#command-not-found-claude-worklog)
- [The page doesn't open](#the-page-doesnt-open)
- [Nothing shows up for today](#nothing-shows-up-for-today)
- [There are no summaries, only lists of prompts](#there-are-no-summaries-only-lists-of-prompts)
- [It shows Sonnet, but I use Opus](#it-shows-sonnet-but-i-use-opus)
- [A session is under the wrong project](#a-session-is-under-the-wrong-project)
- [Timesheet hours look too low or too high](#timesheet-hours-look-too-low-or-too-high)
- [A session is counted under the wrong account](#a-session-is-counted-under-the-wrong-account)
- [My other computer doesn't show up](#my-other-computer-doesnt-show-up)
- [Uploads fail, or the wrong GitHub account is used](#uploads-fail-or-the-wrong-github-account-is-used)
- [I updated, but the page still shows the old version](#i-updated-but-the-page-still-shows-the-old-version)
- [pipx says "already at latest version" but a newer one is out](#pipx-says-already-at-latest-version-but-a-newer-one-is-out)
- [Token numbers look wrong after a Claude Code update](#token-numbers-look-wrong-after-a-claude-code-update)
- [The phone summary doesn't update](#the-phone-summary-doesnt-update)
- [Trends or badges look wrong after an edit](#trends-or-badges-look-wrong-after-an-edit)
- [Reading the logs](#reading-the-logs)

## command not found: claude-worklog

Your terminal doesn't know where pipx put the app yet. Run:

```bash
pipx ensurepath
```

Then **close the terminal and open a new one** and try again.

## The page doesn't open

1. Try **http://localhost:8765** instead of `worklog.localhost:8765`. Some browsers don't support the `.localhost` name.
2. Check that Worklog is running: `claude-worklog doctor` shows `starts at login: yes`. To look closer:
   - Linux: `systemctl --user status claude-worklog-web`
   - macOS: `launchctl print gui/$(id -u)/dev.worklog.web`
   - Windows: Task Manager → Startup apps should list "Worklog for Claude Code"

   If it isn't running, start it again:

   ```bash
   claude-worklog install-service
   ```

3. Using another port? Open the address that `install-service` printed.

## Nothing shows up for today

- **A session only appears after it ends.** Close Claude Code (or type `/exit`), then click **Sync now** on the page.
- **Is the hook installed?** Open **Settings**. Each account should say the hook is installed. If not, click **Install hook**. `claude-worklog doctor` shows the same thing (`hooks installed` or `hook NOT installed`).
- **Is the right folder set?** Claude Code keeps its files in `~/.claude` unless you set `CLAUDE_CONFIG_DIR`. The account's folder in **Settings** has to match.
- **Looking at the right day?** Days follow your computer's clock. Work after midnight is on the next day.
- **Older work** from before you installed Worklog can be added with `claude-worklog collect --days 7` (up to 30 days, if Claude Code still has the files).

## There are no summaries, only lists of prompts

Summaries are written by your own Claude account using the `claude` command.

- Want more accurate summaries? Set **Model for summaries** to **Opus** in **Settings**.
- They're written at **23:30** each night. Click **Write summaries** on a day to get them now.
- `claude-worklog doctor` must show `✓ claude binary`. If it shows `!`, Worklog can't find Claude Code and falls back to plain lists.
- Check that AI summaries are switched on in **Settings**.
- With several computers, only **one** of them writes summaries (the one where you ran `install-service` without `--no-report`).

## It shows Sonnet, but I use Opus

There are two different places a model shows up:

- **The By model list** on a day shows the model Claude Code itself wrote down for each reply. If it says Sonnet, Claude Code really ran those replies on Sonnet: your plan's default, a `/model` choice, or a sub-agent (the list says how much came from sub-agents). Run `claude-worklog doctor`: it prints each account's default model (`default model: …`).
- **The daily summaries** are written by `claude -p`. Open **Settings → Summaries** and set **Model for summaries** to **Opus**. `claude-worklog doctor` shows the model in use (`summaries are written with: …`).

## A session is under the wrong project

Click **✏️ Edit** on the card and pick the right project under **Move to another project**, or **New project…**. If you always start Claude Code in the same folder (like `~/development`), tick **Always put sessions started in … into that project**, and Worklog will remember it. A card asking **"Probably …?"** just needs a **Yes** or **No**. Sessions from before 0.4.0 were recorded without the extra clues, so moving them yourself is the fix for those.

## Timesheet hours look too low or too high

Hours are **active time**: gaps over 15 minutes between messages are breaks, and sessions at the same time count once. Waiting a long time for Claude, or reading without typing, can count as a break. To count from the first to the last message instead, set `time = "wall"` in the `[report]` section of the config. Days from before 0.4.0 are marked "≈" and always use first to last message.

## A session is counted under the wrong account

1. Open **Settings** and check that every account says **Hook installed**. Sessions without a hook record go to the folder's default account.
2. Under **Logins seen on this device**, give every login marked **No name yet** a name. Its sessions from the last 30 days move right away.
3. Switching accounts *inside* a running session isn't tracked: a session stays with the account it started with. Start a new session after `/login`.
4. A login you don't recognize, or don't want tracked? Click **Forget…** next to it under **Logins seen on this device**. It shows what will be deleted and asks you to confirm first.
5. Using a one-click account-switching app? Keep Worklog running (it starts at login after `install-service`): its watcher notices every switch, even without the hook. Sessions from times it wasn't running go to the folder's default account.

## My other computer doesn't show up

Each computer has to sync to the same private data repo. See [Full setup](GUIDE.md#full-setup). On the other computer, run `claude-worklog doctor`. The `store repo` line should show a `remote`, not `none — local only`.

## I updated, but the page still shows the old version

The page is a program that keeps running in the background. After `pipx upgrade` it keeps showing the old version until it restarts. The footer at the bottom of the page shows which version is running.

- **From 0.3.2 on**, the page notices and shows **Restart now**. Click it. (**Update now** on the dashboard restarts it by itself.)
- **Updating from 0.3.1 or older?** That version doesn't have the button yet, so restart it once by hand:

  ```bash
  systemctl --user restart claude-worklog-web                 # Linux
  launchctl kickstart -k gui/$(id -u)/dev.worklog.web          # macOS
  ```

  On Windows, sign out and back in (or restart the computer).

Then reload the page. `claude-worklog doctor` tells you if the page is still running an older version, and gives the command for your system:

```
✗ web page: still running 0.2.0, but 0.4.0 is installed. Restart it once: systemctl --user restart claude-worklog-web
```

Hooks, the hourly sync and the nightly summaries start fresh each time, so they use the new version right away.

## pipx says "already at latest version" but a newer one is out

Right after a release, pip can still be using the version list it saved a few minutes ago, and PyPI takes a few minutes to publish a new version everywhere. Tell pip to skip its saved list:

```bash
pipx upgrade --pip-args=--no-cache-dir worklog-for-claude-code
```

Still the old version? Wait 5–10 minutes and run it again. **Update now** on the dashboard skips the saved list by itself, and if PyPI isn't ready yet it says so.

Upgraded from a terminal while Worklog was running? The dashboard shows **Restart now** so the page switches to the new version.

## Uploads fail, or the wrong GitHub account is used

Open **Settings → Private data repo** and click **Test connection**. It shows which account this computer signs in as and why it fails:

- **Says your key belongs to another account** (for example your work account): that account can't see the data repo. Follow *Several GitHub accounts on this computer?* in the same section: give the data repo its own key and an SSH nickname.
- **Connection timed out** on port 22: some networks block SSH. In `~/.ssh/config`, use `HostName ssh.github.com` and add `Port 443`.
- **"Committer identity unknown"** in *Last download failed*: fixed in 0.3.2. Update, then click **Sync now**. Changes that were waiting upload too.

## Token numbers look wrong after a Claude Code update

Worklog reads Claude Code's log files, and their format isn't official, so an update can change them. Compare with another tool, such as `npx ccusage daily`, and [report it](https://github.com/us-urvin/worklog-for-claude-code/issues/new/choose) with your Claude Code version (`claude --version`).

## The phone summary doesn't update

- Check **Settings → On your phone** on the computer that should write it: it must say *On: this computer writes it*.
- It updates when that computer syncs. If it's asleep or off, the summary waits. Click **Sync now** there to update it right away.
- Uploads must work: see **Settings → Private data repo**.

## Trends or badges look wrong after an edit

Worklog keeps a small per-day cache for these pages and rebuilds a day whenever its data changes. If something still looks off, delete the cache; it's rebuilt automatically:

```bash
rm ~/.local/state/claude-worklog/cache/days.json
```

## Reading the logs

Worklog writes its own log on every system: `~/.local/state/claude-worklog/worklog.log` (on Windows: `%USERPROFILE%\.local\state\claude-worklog\worklog.log`). On Linux the services also log to the journal:

```bash
journalctl --user -u claude-worklog-web -f          # the web page (Ctrl+C to stop)
journalctl --user -u claude-worklog-collect -n 50   # the last hourly syncs
journalctl --user -u claude-worklog-report -n 50    # the last nightly summaries
```

On macOS, background output also goes to `~/.local/state/claude-worklog/launchd.log`.
