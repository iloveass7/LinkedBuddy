import pytest

from linkedbuddy.enums import EventType, RepoMode
from linkedbuddy.triage.score import (
    HEURISTIC_GATE,
    LARGE_CHANGE_LINES,
    EventSignals,
    heuristic_score,
    passes_mode,
)


def signals(
    type: EventType = EventType.push_group,
    meaningful: int = 0,
    new_files: int = 0,
    has_feat: bool = False,
    readme_changed: bool = False,
    labeled: bool = False,
    kinds: list[str] | None = None,
) -> EventSignals:
    return EventSignals(
        type=type,
        meaningful=meaningful,
        new_files=new_files,
        has_feat=has_feat,
        readme_changed=readme_changed,
        labeled=labeled,
        kinds=kinds if kinds is not None else ["code"],
    )


def test_constants() -> None:
    assert HEURISTIC_GATE == 2.0
    assert LARGE_CHANGE_LINES == 300


def test_score_noise_zero() -> None:
    s = signals(
        type=EventType.release,
        meaningful=5000,
        new_files=9,
        has_feat=True,
        kinds=["merge", "dependency", "noise", "docs"],
    )
    assert heuristic_score(s) == 0.0


def test_score_new_repo_passes_gate() -> None:
    s = signals(type=EventType.new_repo, kinds=["code"])
    assert heuristic_score(s) >= HEURISTIC_GATE


def test_score_new_repo_empty_kinds_not_zeroed() -> None:
    assert heuristic_score(signals(type=EventType.new_repo, kinds=[])) == 5.0


def test_score_tiny_code_below_gate() -> None:
    assert heuristic_score(signals(meaningful=5)) < HEURISTIC_GATE


def test_score_components() -> None:
    s = signals(
        type=EventType.release,
        meaningful=999,
        new_files=10,
        has_feat=True,
        readme_changed=True,
        labeled=True,
    )
    # release 4 + labeled 5 + feat 2 + min(10,3) + readme 1 + min(3, log10(1000)=3)
    assert heuristic_score(s) == pytest.approx(18.0)


def test_score_log_term_capped() -> None:
    assert heuristic_score(signals(meaningful=10**9)) == pytest.approx(3.0)


MODE_CASES = [
    # (mode, signals kwargs, score, expected)
    (RepoMode.muted, dict(type=EventType.new_repo), 9.0, False),
    (RepoMode.muted, dict(type=EventType.manual), 9.0, False),
    (RepoMode.manual_only, dict(type=EventType.manual), 0.0, True),
    (RepoMode.manual_only, dict(type=EventType.release, meaningful=999), 9.0, False),
    (RepoMode.manual_only, dict(type=EventType.new_repo), 9.0, False),
    (RepoMode.releases_and_large, dict(type=EventType.new_repo), 0.0, True),
    (RepoMode.releases_and_large, dict(type=EventType.release), 0.0, True),
    (RepoMode.releases_and_large, dict(type=EventType.manual), 0.0, True),
    (RepoMode.releases_and_large, dict(type=EventType.labeled), 0.0, True),
    (RepoMode.releases_and_large, dict(meaningful=300), 0.0, True),
    (RepoMode.releases_and_large, dict(meaningful=299), 9.0, False),
    (RepoMode.always_suggest, dict(), 2.0, True),
    (RepoMode.always_suggest, dict(), 1.99, False),
    (RepoMode.always_suggest, dict(type=EventType.release), 0.0, True),
    (RepoMode.always_suggest, dict(type=EventType.labeled), 0.0, True),
    (RepoMode.always_suggest, dict(type=EventType.pr_merged), 1.0, False),
]


@pytest.mark.parametrize(("mode", "kwargs", "score", "expected"), MODE_CASES)
def test_passes_mode(
    mode: RepoMode, kwargs: dict[str, object], score: float, expected: bool
) -> None:
    assert passes_mode(mode, signals(**kwargs), score) is expected  # type: ignore[arg-type]
