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
- [My other computer doesn't show up](#my-other-computer-doesnt-show-up)
- [Token numbers look wrong after a Claude Code update](#token-numbers-look-wrong-after-a-claude-code-update)
- [Reading the logs](#reading-the-logs)

## command not found: claude-worklog

Your terminal doesn't know where pipx put the app yet. Run:

```bash
pipx ensurepath
```

Then **close the terminal and open a new one** and try again.

## The page doesn't open

1. Try **http://localhost:8765** instead of `worklog.localhost:8765`. Some browsers don't support the `.localhost` name.
2. Check that Worklog is running:

   ```bash
   systemctl --user status claude-worklog-web
   ```

   Look for `active (running)`. If it says something else, start it again:

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

- They're written at **23:30** each night. Click **Write summaries** on a day to get them now.
- `claude-worklog doctor` must show `✓ claude binary`. If it shows `!`, Worklog can't find Claude Code and falls back to plain lists.
- Check that AI summaries are switched on in **Settings**.
- With several computers, only **one** of them writes summaries (the one where you ran `install-service` without `--no-report`).

## My other computer doesn't show up

Each computer has to sync to the same private data repo. See [Full setup](GUIDE.md#full-setup). On the other computer, run `claude-worklog doctor`. The `store repo` line should show a `remote`, not `none — local only`.

## Token numbers look wrong after a Claude Code update

Worklog reads Claude Code's log files, and their format isn't official, so an update can change them. Compare with another tool, such as `npx ccusage daily`, and [report it](https://github.com/us-urvin/worklog-for-claude-code/issues/new/choose) with your Claude Code version (`claude --version`).

## Reading the logs

For the detailed story of what Worklog is doing:

```bash
journalctl --user -u claude-worklog-web -f          # the web page (Ctrl+C to stop)
journalctl --user -u claude-worklog-collect -n 50   # the last hourly syncs
journalctl --user -u claude-worklog-report -n 50    # the last nightly summaries
```
