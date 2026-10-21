"""Post-generation lint: ban list, buzzwords, specificity, LinkedIn length limit."""

from linkedbuddy.drafts.lint import BUILTIN_BAN, lint


def test_builtin_ban_list() -> None:
    assert BUILTIN_BAN == ["excited to announce", "game-changer", "thrilled", "delighted to share"]


def test_lint_banned_case_insensitive() -> None:
    report = lint("Thrilled to share our new reranker", BUILTIN_BAN)
    assert report.banned_hits == ["thrilled"]


def test_lint_owner_ban_list_merged_and_deduped() -> None:
    report = lint("A real Game-Changer. Synergy!", [*BUILTIN_BAN, "synergy", "SYNERGY", " "])
    assert report.banned_hits == ["game-changer", "synergy"]


def test_lint_over_limit() -> None:
    assert lint("a" * 3001, []).over_limit is True
    at_limit = lint("a" * 3000, [])
    assert (at_limit.over_limit, at_limit.char_count) == (False, 3000)


def test_lint_specificity() -> None:
    report = lint("Moved rerank.py to 3 stages because latency", [])
    assert report.specificity_count >= 3


def test_lint_specificity_counts_backticks_and_phrases() -> None:
    report = lint("Switched `RetrievalCache` to LRU instead of FIFO: a tradeoff.", [])
    # `RetrievalCache`, switched, instead of, tradeoff
    assert report.specificity_count == 4
    assert lint("A nice update to the project.", []).specificity_count == 0


def test_lint_buzzwords() -> None:
    report = lint("We leverage a seamless, cutting-edge pipeline.", [])
    assert report.buzzword_count == 3
    assert lint("Fixed the cache key.", []).buzzword_count == 0
