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

## Verify a release

Every release is built and uploaded by GitHub Actions ([`release.yml`](.github/workflows/release.yml)) from a
`vX.Y.Z` tag. No one uploads files by hand, and no PyPI password or token is stored anywhere: PyPI accepts the
upload only from that workflow (Trusted Publishing). Before publishing, the workflow installs the built package on
Linux, macOS and Windows and checks it works ([`scripts/smoke_test.py`](scripts/smoke_test.py)).

You can check three things yourself. Replace `0.5.2` with the version you have.

**1. The file on PyPI was built by this repository.** PyPI keeps a signed record (an attestation) of which
repository and workflow built each file. Check it with the official tool:

```bash
pip install pypi-attestations
pypi-attestations verify pypi --repository https://github.com/us-urvin/worklog-for-claude-code \
  pypi:worklog_for_claude_code-0.5.2-py3-none-any.whl
```

It prints `OK` when the file was signed by this repository's release workflow, and fails otherwise. You can also
see it on PyPI: open the release, **Download files**, then **View details** next to a file. It shows the
repository, the workflow and the commit.

**2. The file holds exactly the source code of the tag.** Download it and compare it with the source of that
tag, file by file (SHA-256, ignoring Windows/Unix line endings):

```bash
git clone --branch v0.5.2 https://github.com/us-urvin/worklog-for-claude-code && cd worklog-for-claude-code
pip download --no-deps --only-binary :all: worklog-for-claude-code==0.5.2 -d dist
python scripts/smoke_test.py dist/worklog_for_claude_code-0.5.2-py3-none-any.whl
```

Step 1 of the output says whether every file is identical. The script then installs the package in a throwaway
environment, runs the demo and uninstalls it again, touching none of your files.

**3. What you installed is that file.** `pip hash dist/*.whl` gives the SHA-256 shown on PyPI under
**View details**.

What it does not prove: that the code is free of bugs. For that, read it: it's about 30 Python files with no
dependencies outside Python's standard library.

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
