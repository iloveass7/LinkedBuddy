import pytest

from linkedbuddy.github.classify import Classified, classify
from linkedbuddy.github.client import GhCommit, GhFile


def commit(
    message: str = "update",
    files: list[tuple[str, int, int]] | None = None,
    parents: int = 1,
    author: str | None = "alice",
) -> GhCommit:
    return GhCommit(
        sha="a" * 40,
        parents=["p"] * parents,
        author_login=author,
        message=message,
        files=[GhFile(filename=n, additions=a, deletions=d) for n, a, d in (files or [])],
        committed_at=None,
    )


CASES = [
    ("merge_two_parents", commit("wip", [("a.py", 5, 5)], parents=2), "merge", 10),
    ("merge_pr_squash", commit("Merge pull request #12 from x/y", [("a.py", 1, 0)]), "merge", 1),
    ("merge_branch", commit("Merge branch 'main' into dev", [("a.py", 1, 0)]), "merge", 1),
    ("dependabot", commit("update", [("a.py", 2, 2)], author="dependabot[bot]"), "dependency", 4),
    ("bump", commit("Bump lodash from 1 to 2", [("package.json", 1, 1)]), "dependency", 2),
    ("chore_deps", commit("chore(deps): update x", [("package.json", 1, 1)]), "dependency", 2),
    (
        "lockfile_only",
        commit("update", [("package-lock.json", 500, 20), ("sub/uv.lock", 9, 9)]),
        "noise",
        0,
    ),
    (
        "build_output_only",
        commit("update", [("dist/app.js", 9, 9), ("web/node_modules/x.js", 1, 1)]),
        "noise",
        0,
    ),
    ("min_js_only", commit("update", [("static/app.min.js", 50, 0)]), "noise", 0),
    ("no_files", commit("update", []), "noise", 0),
    (
        "docs_only",
        commit("update", [("README.md", 3, 1), ("docs/x.txt", 4, 0), ("docs/y.md", 2, 0)]),
        "docs",
        10,
    ),
    (
        "feature",
        commit("feat(api): x", [("a.py", 20, 10), ("package-lock.json", 900, 1)]),
        "feature",
        30,
    ),
    ("feature_bang", commit("feat!: breaking", [("a.py", 3, 0)]), "feature", 3),
    ("feature_mixed_docs", commit("feat: x", [("a.py", 3, 0), ("README.md", 1, 0)]), "feature", 4),
    ("fix", commit("fix: crash", [("a.py", 2, 1)]), "fix", 3),
    ("plain_code", commit("tweak things", [("a.py", 2, 1)]), "code", 3),
    (
        "notebook_only",
        commit("analysis", [("nb.ipynb", 900, 100)]),
        "code",
        100,
    ),
    (
        "notebook_per_file_sum_floored",
        commit("analysis", [("a.ipynb", 15, 0), ("b.ipynb", 15, 0), ("c.py", 1, 0)]),
        "code",
        4,  # 1 + floor(0.1 * 30)
    ),
    ("notebook_feat", commit("feat: nb", [("nb.ipynb", 100, 0)]), "feature", 10),
]


@pytest.mark.parametrize(("name", "c", "kind", "meaningful"), CASES, ids=[c[0] for c in CASES])
def test_classify(name: str, c: GhCommit, kind: str, meaningful: int) -> None:
    assert classify(c) == Classified(kind=kind, meaningful=meaningful)  # type: ignore[arg-type]
