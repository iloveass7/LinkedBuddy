"""Bind gate (SECURITY.md 2.1): loopback only unless ALLOW_PUBLIC_BIND."""

from typing import Any

import pytest

from linkedbuddy import serve
from linkedbuddy.config import Settings


@pytest.mark.parametrize(
    "host", ["127.0.0.1", "::1", "[::1]", "localhost", "LOCALHOST", "127.0.0.2"]
)
def test_loopback_hosts_allowed(host: str) -> None:
    serve.check_bind(host, allow_public_bind=False)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.10", "my-laptop.lan", ""])
def test_public_hosts_refused(host: str) -> None:
    with pytest.raises(serve.PublicBindRefused, match="ALLOW_PUBLIC_BIND"):
        serve.check_bind(host, allow_public_bind=False)


def test_public_bind_allowed_when_configured() -> None:
    serve.check_bind("0.0.0.0", allow_public_bind=True)


@pytest.fixture
def runs(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(serve.uvicorn, "run", lambda app, **kw: calls.append({"app": app, **kw}))
    monkeypatch.setattr(serve, "get_settings", lambda: settings)
    return calls


def test_main_refuses_public_host_before_starting(
    runs: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        serve.main(["--host", "0.0.0.0"])
    assert exc.value.code == 2
    assert "ALLOW_PUBLIC_BIND" in capsys.readouterr().err
    assert runs == []


def test_main_runs_uvicorn_on_loopback_by_default(runs: list[dict[str, Any]]) -> None:
    serve.main(["--reload"])
    assert runs == [
        {"app": "linkedbuddy.main:app", "host": "127.0.0.1", "port": 8000, "reload": True}
    ]
