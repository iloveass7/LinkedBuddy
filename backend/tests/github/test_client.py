import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest
import respx

from linkedbuddy.github.client import GitHubClient, GitHubRateLimited
from linkedbuddy.http import DisallowedHost, make_http_client

API = "https://api.github.com"
SHA = "a" * 40
SHA2 = "b" * 40
OK_HEADERS = {"x-ratelimit-remaining": "4000", "x-ratelimit-reset": "1900000000"}


@pytest.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    async with make_http_client() as c:
        yield c


@pytest.fixture
def gh(http: httpx.AsyncClient) -> GitHubClient:
    return GitHubClient(http, "ghp_faketoken")


def _repo(name: str) -> dict[str, object]:
    return {
        "id": 1,
        "full_name": name,
        "default_branch": "main",
        "private": False,
        "fork": False,
        "archived": False,
        "created_at": "2026-01-01T00:00:00Z",
        "pushed_at": "2026-02-01T00:00:00Z",
        "description": None,
    }


def _commit_json() -> dict[str, object]:
    return {
        "sha": SHA,
        "parents": [{"sha": SHA2}],
        "author": {"login": "octo"},
        "commit": {"message": "feat: x", "committer": {"date": "2026-02-01T12:00:00Z"}},
        "files": [{"filename": "a.py", "additions": 3, "deletions": 1, "status": "modified"}],
    }


@respx.mock
async def test_etag_304_not_modified(gh: GitHubClient) -> None:
    route = respx.get(f"{API}/repos/o/r/commits").respond(304, headers=OK_HEADERS)
    page = await gh.list_commits("o/r", "main", None, '"abc"')
    assert page.not_modified is True
    assert page.items == []
    assert page.etag == '"abc"'
    assert route.calls.last.request.headers["if-none-match"] == '"abc"'


@respx.mock
async def test_commits_200_returns_items_and_etag(gh: GitHubClient) -> None:
    route = respx.get(f"{API}/repos/o/r/commits").respond(
        200,
        json=[{"sha": SHA, "commit": {"committer": {"date": "2026-02-01T12:00:00Z"}}}],
        headers={**OK_HEADERS, "etag": '"new"'},
    )
    since = datetime(2026, 1, 1, tzinfo=UTC)
    page = await gh.list_commits("o/r", "main", since, None)
    assert page.not_modified is False
    assert page.etag == '"new"'
    assert [c.sha for c in page.items] == [SHA]
    req = route.calls.last.request
    assert "if-none-match" not in req.headers
    assert req.url.params["sha"] == "main"
    assert req.url.params["since"].startswith("2026-01-01T00:00:00")


@respx.mock
async def test_empty_repo_409(gh: GitHubClient) -> None:
    respx.get(f"{API}/repos/o/r/commits").respond(409, json={"message": "empty"})
    page = await gh.list_commits("o/r", "main", None, None)
    assert page.items == []
    assert page.not_modified is False


@respx.mock
async def test_readme_missing(gh: GitHubClient) -> None:
    respx.get(f"{API}/repos/o/r/readme").respond(404)
    assert await gh.get_readme("o/r") is None


@respx.mock
async def test_readme_raw(gh: GitHubClient) -> None:
    route = respx.get(f"{API}/repos/o/r/readme").respond(200, text="# Hi", headers=OK_HEADERS)
    assert await gh.get_readme("o/r") == "# Hi"
    assert route.calls.last.request.headers["accept"] == "application/vnd.github.raw+json"


@respx.mock
async def test_compare_404_returns_none(gh: GitHubClient) -> None:
    respx.get(url__regex=rf"{API}/repos/o/r/compare/.*").respond(404)
    assert await gh.compare("o/r", SHA, SHA2) is None


@respx.mock
async def test_compare_returns_shas(gh: GitHubClient) -> None:
    respx.get(f"{API}/repos/o/r/compare/{SHA}...{SHA2}").respond(
        200, json={"commits": [{"sha": SHA2}]}, headers=OK_HEADERS
    )
    assert await gh.compare("o/r", SHA, SHA2) == [SHA2]


@respx.mock
async def test_rate_limit_low_raises(gh: GitHubClient) -> None:
    respx.get(f"{API}/repos/o/r/releases").respond(
        200, json=[], headers={"x-ratelimit-remaining": "50", "x-ratelimit-reset": "1900000000"}
    )
    with pytest.raises(GitHubRateLimited) as exc:
        await gh.list_releases("o/r")
    assert exc.value.reset_at == datetime.fromtimestamp(1900000000, UTC)


@respx.mock
async def test_403_retry_after_raises(gh: GitHubClient) -> None:
    respx.get(f"{API}/repos/o/r/releases").respond(403, headers={"retry-after": "60"})
    before = datetime.now(UTC)
    with pytest.raises(GitHubRateLimited) as exc:
        await gh.list_releases("o/r")
    assert 55 <= (exc.value.reset_at - before).total_seconds() <= 70


@respx.mock
async def test_429_retry_after_raises(gh: GitHubClient) -> None:
    respx.get(f"{API}/user/repos").respond(429, headers={"retry-after": "30"})
    with pytest.raises(GitHubRateLimited):
        await gh.list_repos()


@respx.mock
async def test_other_403_is_plain_http_error(gh: GitHubClient) -> None:
    respx.get(f"{API}/repos/o/r/releases").respond(403, headers=OK_HEADERS)
    with pytest.raises(httpx.HTTPStatusError):
        await gh.list_releases("o/r")


@respx.mock
async def test_pagination_follows_link_header(gh: GitHubClient) -> None:
    page2 = f"{API}/user/repos?per_page=100&affiliation=owner&page=2"
    respx.get(url__regex=r".*/user/repos\?.*page=2").respond(200, json=[_repo("o/b")])
    respx.get(url__regex=r".*/user/repos\?(?!.*page=2).*").respond(
        200, json=[_repo("o/a")], headers={"link": f'<{page2}>; rel="next"'}
    )
    repos = await gh.list_repos()
    assert [r.full_name for r in repos] == ["o/a", "o/b"]


@respx.mock
async def test_get_commit_parses(gh: GitHubClient) -> None:
    respx.get(f"{API}/repos/o/r/commits/{SHA}").respond(200, json=_commit_json())
    c = await gh.get_commit("o/r", SHA)
    assert c.sha == SHA
    assert c.parents == [SHA2]
    assert c.author_login == "octo"
    assert c.message == "feat: x"
    assert c.committed_at == datetime(2026, 2, 1, 12, tzinfo=UTC)
    assert (c.files[0].filename, c.files[0].additions, c.files[0].status) == ("a.py", 3, "modified")


@respx.mock
async def test_get_tree_truncates_to_5000_blobs(gh: GitHubClient) -> None:
    tree = [{"path": f"f{i}", "type": "blob"} for i in range(5100)]
    tree.append({"path": "dir", "type": "tree"})
    route = respx.get(f"{API}/repos/o/r/git/trees/{SHA}").respond(200, json={"tree": tree})
    paths = await gh.get_tree("o/r", SHA)
    assert len(paths) == 5000
    assert "dir" not in paths
    assert route.calls.last.request.url.params["recursive"] == "1"


@respx.mock
async def test_request_headers(gh: GitHubClient) -> None:
    route = respx.get(f"{API}/repos/o/r/releases").respond(200, json=[], headers=OK_HEADERS)
    await gh.list_releases("o/r")
    h = route.calls.last.request.headers
    assert h["authorization"] == "Bearer ghp_faketoken"
    assert h["accept"] == "application/vnd.github+json"
    assert h["x-github-api-version"] == "2022-11-28"


async def test_invalid_repo_name_rejected(gh: GitHubClient) -> None:
    with pytest.raises(ValueError):
        await gh.get_readme("o/r/../../user")


@respx.mock
async def test_only_get_requests(gh: GitHubClient) -> None:
    respx.get(f"{API}/user/repos").respond(200, json=[_repo("o/r")], headers=OK_HEADERS)
    respx.get(f"{API}/repos/o/r/commits").respond(200, json=[], headers=OK_HEADERS)
    respx.get(re.compile(rf"{API}/repos/o/r/commits/.+")).respond(
        200, json=_commit_json(), headers=OK_HEADERS
    )
    respx.get(re.compile(rf"{API}/repos/o/r/compare/.+")).respond(
        200, json={"commits": []}, headers=OK_HEADERS
    )
    respx.get(f"{API}/repos/o/r/readme").respond(200, text="x", headers=OK_HEADERS)
    respx.get(f"{API}/repos/o/r/releases").respond(200, json=[], headers=OK_HEADERS)
    respx.get(re.compile(rf"{API}/repos/o/r/git/trees/.+")).respond(
        200, json={"tree": []}, headers=OK_HEADERS
    )
    await gh.list_repos()
    await gh.list_commits("o/r", "main", None, None)
    await gh.get_commit("o/r", SHA)
    await gh.compare("o/r", SHA, SHA2)
    await gh.get_readme("o/r")
    await gh.list_releases("o/r")
    await gh.get_tree("o/r", SHA)
    assert len(respx.calls) == 7
    assert {call.request.method for call in respx.calls} == {"GET"}


async def test_disallowed_host_blocked(http: httpx.AsyncClient) -> None:
    with pytest.raises(DisallowedHost):
        await http.get("https://evil.example")


async def test_non_https_blocked(http: httpx.AsyncClient) -> None:
    with pytest.raises(DisallowedHost):
        await http.get("http://api.github.com/user")


async def test_lifespan_creates_and_closes_shared_client() -> None:
    from asgi_lifespan import LifespanManager

    from linkedbuddy.config import Settings
    from linkedbuddy.main import create_app

    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url="postgresql+asyncpg://u:p@localhost:5432/t",
        app_encryption_key="k",  # type: ignore[arg-type]
        owner_password_hash="h",  # type: ignore[arg-type]
    )
    app = create_app(settings)
    async with LifespanManager(app):
        shared = app.state.http
        assert not shared.is_closed
    assert shared.is_closed
