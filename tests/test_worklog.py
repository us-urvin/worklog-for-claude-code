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


def test_subagent_usage_is_kept_apart_per_model(tmp_path):
    """Sub-agents often run on another model; the 'By model' table must be able to say so."""
    ts = datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    f = tmp_path / "s1.jsonl"
    f.write_text("\n".join([
        _line(type="assistant", uuid="a1", sessionId="s1", timestamp=ts, requestId="r1",
              message={"id": "m1", "model": "claude-opus-5-5", "usage": {"input_tokens": 100}, "content": []}),
        _line(type="assistant", uuid="a2", sessionId="s1", timestamp=ts, requestId="r2", isSidechain=True,
              message={"id": "m2", "model": "claude-sonnet-5-5", "usage": {"input_tokens": 40}, "content": []}),
    ]))
    sessions = {}
    day = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().date()
    parse_transcript(f, day, sessions, set(), set(), ParseStats())
    sd = sessions["s1"]
    assert sd.tokens_by_model["claude-opus-5-5"]["input"] == 100
    assert sd.tokens_by_model["claude-sonnet-5-5"]["input"] == 40  # totals still include sub-agents
    assert sd.subagent_tokens_by_model == {"claude-sonnet-5-5": {"input": 40, "output": 0,
                                                                 "cache_creation": 0, "cache_read": 0}}


def test_model_names_are_readable():
    from worklog.report import model_label
    assert model_label("claude-opus-5-5") == "Opus 5.5"
    assert model_label("claude-sonnet-4-5-20250929") == "Sonnet 4.5"
    assert model_label("claude-opus-4-1-20250805") == "Opus 4.1"
    assert model_label("claude-3-5-haiku-20241022") == "Haiku 3.5"
    assert model_label("claude-sonnet-4-20250514") == "Sonnet 4"
    assert model_label("claude-sonnet-5-5[1m]") == "Sonnet 5.5 (1M)"
    assert model_label("claude-haiku-5-5") == "Haiku 5.5"
    assert model_label("claude-fable-5-1") == "Fable 5.1"
    assert model_label("unknown") == "unknown"
    assert model_label("gpt-x") == "gpt-x"


def test_by_model_rows_share_and_old_records():
    from worklog.report import aggregate, model_rows
    new = {"account": "personal", "tokens": {"input": 140}, "session_id": "a", "date": "2026-09-26",
           "tokens_by_model": {"claude-opus-5-5": {"input": 100}, "claude-sonnet-5-5": {"input": 40}},
           "subagent_tokens_by_model": {"claude-sonnet-5-5": {"input": 40}}, "project": {"name": "p"}}
    old = {"account": "personal", "tokens": {"input": 60}, "session_id": "b", "date": "2026-09-26",
           "tokens_by_model": {"claude-opus-5-5": {"input": 60}}, "project": {"name": "p"}}  # before 0.3.0
    accounts, _ = aggregate([new, old])
    rows = model_rows(accounts)
    assert [(r["account"], r["label"], r["total"], r["sub"], r["share"]) for r in rows] == [
        ("personal", "Opus 5.5", 160, 0, 80), ("personal", "Sonnet 5.5", 40, 40, 20)]
