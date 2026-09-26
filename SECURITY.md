# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Report them privately through GitHub:
[**Report a vulnerability**](https://github.com/us-urvin/worklog-for-claude-code/security/advisories/new)
(repository → *Security* tab → *Report a vulnerability*).

Please include what you found, how to reproduce it, and what an attacker could do with it. Remove any personal
data (your transcripts, e-mail, tokens) from the report. You should get a first reply within 7 days. This is a
volunteer project, so fixes are made on a best-effort basis; you will be credited in the advisory unless you
prefer not to be.

## Supported versions

Only the latest release receives security fixes.

## Scope

In scope:

- **Redaction gaps**: a secret or personal detail that makes it from a transcript or commit message into a record,
  report or log in a form that `worklog/redact.py` should reasonably have caught, or any path by which file
  contents, tool output or command output end up in the data repo.
- **The local web UI** (`claude-worklog serve`): anything that lets a website, another local user or another
  machine read data, change settings, trigger git or `claude` runs, or bypass the Host, Origin, CSRF or CSP
  protections.
- **Hooks and services**: the Claude Code hooks (`claude-worklog hook`) or the systemd user units doing something
  unsafe, such as breaking or blocking a Claude Code session, leaking data through stdout into Claude's context,
  writing files outside the expected places, or weakening file permissions.
- Credentials leaking from git remote URLs, `~/.claude.json` or the local state file.

Out of scope:

- The contents of your own private data repo being readable by people you gave access to.
- Anything that needs an attacker who already runs code as your user.
- Claude Code itself: report those to Anthropic.
- Redaction is best effort by design. "Pattern X is not redacted" is welcome as a normal issue or pull request
  unless it is a common credential format.
