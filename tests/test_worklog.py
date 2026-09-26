import json
from datetime import date, datetime, timezone

from worklog.collector import sanitize_remote
from worklog.parser import ParseStats, parse_transcript
from worklog.redact import redact


def test_redact_secrets_and_pii():
    s = redact("key sk-ant-api03-abcdefghijklmnop mail me@x.com ip 10.1.2.3 password=hunter22 "
               "https://bob:s3cret@github.com/o/r ghp_" + "a" * 36)
    for leaked in ("sk-ant", "me@x.com", "10.1.2.3", "hunter22", "s3cret", "ghp_"):
        assert leaked not in s


def test_sanitize_remote_drops_credentials():
    assert sanitize_remote("https://u:tok@gitlab.com/g/sub/r.git") == {"host": "gitlab.com", "slug": "g/sub/r"}
    assert sanitize_remote("git@bitbucket.org:team/r.git") == {"host": "bitbucket.org", "slug": "team/r"}
    assert sanitize_remote("ssh://git@github.com:22/o/r.git") == {"host": "github.com", "slug": "o/r"}
    assert sanitize_remote("/srv/local.git") is None


def _line(**kw):
    return json.dumps(kw)


def test_usage_dedup_and_prompt_filtering(tmp_path):
    ts = datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    usage = {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 100}
    f = tmp_path / "s1.jsonl"
    f.write_text("\n".join([
        _line(type="user", uuid="u1", sessionId="s1", timestamp=ts, cwd="/tmp",
              message={"role": "user", "content": "add login"}),
        _line(type="user", uuid="u2", sessionId="s1", timestamp=ts,
              message={"role": "user", "content": "<command-name>/clear</command-name>"}),
        # same message streamed as two lines -> usage counted once
        _line(type="assistant", uuid="a1", sessionId="s1", timestamp=ts, requestId="r1",
              message={"id": "m1", "model": "claude-x", "usage": usage,
                       "content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "/tmp/a.py"}}]}),
        _line(type="assistant", uuid="a2", sessionId="s1", timestamp=ts, requestId="r1",
              message={"id": "m1", "model": "claude-x", "usage": usage, "content": [{"type": "text", "text": "ok"}]}),
        "{not json",
    ]))
    sessions, stats = {}, ParseStats()
    day = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().date()
    parse_transcript(f, day, sessions, set(), set(), stats)
    sd = sessions["s1"]
    assert sd.prompts == ["add login"]
    assert sd.tokens == {"input": 10, "output": 5, "cache_creation": 0, "cache_read": 100}
    assert sd.files == {"/tmp/a.py"} and stats.bad_lines == 1
    # a different day yields nothing
    other = {}
    parse_transcript(f, date(2020, 1, 1), other, set(), set(), ParseStats())
    assert other == {}
