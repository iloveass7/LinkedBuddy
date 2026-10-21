"""Evidence validation: a segment is supported only if every cited id exists in the context."""

from linkedbuddy.drafts.validate import Segment, validate_segments


def test_validate_unknown_id_unsupported() -> None:
    segment = Segment(text="Moved to rank 1.", evidence=["readme:L999"])
    out = validate_segments([segment], {"readme:L1"})
    assert out.segments[0]["supported"] is False
    assert out.unsupported_claims == 1


def test_validate_no_evidence_unsupported() -> None:
    out = validate_segments([Segment(text="It is great.", evidence=[])], {"readme:L1"})
    assert out.segments == [{"text": "It is great.", "evidence": [], "supported": False}]
    assert out.unsupported_claims == 1


def test_validate_mixed_known_and_unknown_unsupported() -> None:
    out = validate_segments(
        [Segment(text="x", evidence=["readme:L1", "metric:fake"])], {"readme:L1"}
    )
    assert out.segments[0]["supported"] is False
    assert out.unsupported_claims == 1


def test_validate_supported_and_body_join() -> None:
    known = {"readme:L1", "commit:abc1234"}
    out = validate_segments(
        [
            Segment(text="First line.\n\n", evidence=["readme:L1"]),
            Segment(text="Second sentence.", evidence=["commit:abc1234"]),
            Segment(text="Third.", evidence=["readme:L1", "commit:abc1234"]),
        ],
        known,
    )
    assert out.body == "First line.\n\nSecond sentence. Third."
    assert [s["supported"] for s in out.segments] == [True, True, True]
    assert out.unsupported_claims == 0
