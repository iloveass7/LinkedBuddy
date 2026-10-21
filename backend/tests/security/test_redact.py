import pytest

from linkedbuddy.security.redact import RedactionBlocked, RedactResult, redact

PEM = "-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----"

CASES = [
    ("github_token", "ghp_" + "a" * 36),
    ("github_token", "github_pat_" + "a" * 82),
    ("aws_key", "AKIAABCDEFGHIJKLMNOP"),
    ("google_key", "AIza" + "a" * 35),
    ("slack_token", "xoxb-123-abc"),
    ("private_key", PEM),
    ("jwt", "eyJhbGciOi.eyJzdWIi.sig_abc"),
    ("env_secret", "DB_PASSWORD=hunter2hunter2"),
    ("email", "me@example.com"),
]


@pytest.mark.parametrize(("kind", "secret"), CASES)
def test_redacts_kind(kind: str, secret: str) -> None:
    result = redact(f"before {secret} after")
    assert f"[REDACTED:{kind}]" in result.text
    assert secret not in result.text
    assert kind in result.kinds
    assert result.text.startswith("before ")
    assert result.text.endswith(" after")


def test_clean_text_untouched() -> None:
    result = redact("Added reranker in src/rerank.py")
    assert result == RedactResult(text="Added reranker in src/rerank.py", kinds=[])


def test_private_key_swallows_inner_matches() -> None:
    pem = "-----BEGIN PRIVATE KEY-----\nme@example.com\n-----END PRIVATE KEY-----"
    result = redact(pem)
    assert result.text == "[REDACTED:private_key]"
    assert result.kinds == ["private_key"]


def test_kinds_are_unique_and_ordered() -> None:
    result = redact("a@b.co c@d.co ghp_" + "b" * 36)
    assert result.kinds == ["github_token", "email"]


def test_env_secret_keeps_more_specific_kind() -> None:
    result = redact("GITHUB_TOKEN=ghp_" + "c" * 36)
    assert result.kinds == ["github_token"]


def test_env_secret_multiline_and_quotes() -> None:
    result = redact('x=1\nAPI_KEY="s3cr3t value"\ny=2')
    assert result.text == "x=1\n[REDACTED:env_secret]\ny=2"


def test_redaction_blocked_is_exception() -> None:
    assert issubclass(RedactionBlocked, Exception)
