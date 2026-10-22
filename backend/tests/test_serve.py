"""Bind gate (SECURITY.md 2.1): loopback only unless ALLOW_PUBLIC_BIND and APP_ENV=prod."""

from typing import Any

import pytest

from linkedbuddy import serve
from linkedbuddy.config import Settings


@pytest.mark.parametrize(
    "host", ["127.0.0.1", "::1", "[::1]", "localhost", "LOCALHOST", "127.0.0.2"]
)
def test_loopback_hosts_allowed(host: str) -> None:
    serve.check_bind(host, allow_public_bind=False, app_env="dev")


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.10", "my-laptop.lan", ""])
def test_public_hosts_refused(host: str) -> None:
    with pytest.raises(serve.PublicBindRefused, match="ALLOW_PUBLIC_BIND"):
        serve.check_bind(host, allow_public_bind=False, app_env="prod")


def test_public_bind_allowed_when_configured_in_prod() -> None:
    serve.check_bind("0.0.0.0", allow_public_bind=True, app_env="prod")


def test_public_bind_refused_in_dev_even_when_allowed() -> None:
    # dev serves /docs and /openapi.json, a non-Secure cookie and no HSTS (SECURITY.md 2).
    with pytest.raises(serve.PublicBindRefused, match="APP_ENV=prod"):
        serve.check_bind("0.0.0.0", allow_public_bind=True, app_env="dev")


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


def test_main_refuses_public_host_in_dev_with_allow_public_bind(
    runs: list[dict[str, Any]], settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    settings.allow_public_bind = True  # the fixture's app_env is "dev"
    with pytest.raises(SystemExit) as exc:
        serve.main(["--host", "0.0.0.0"])
    assert exc.value.code == 2
    assert "APP_ENV=prod" in capsys.readouterr().err
    assert runs == []


def test_main_runs_uvicorn_on_loopback_by_default(runs: list[dict[str, Any]]) -> None:
    serve.main(["--reload"])
    assert runs == [
        {"app": "linkedbuddy.main:app", "host": "127.0.0.1", "port": 8000, "reload": True}
    ]
