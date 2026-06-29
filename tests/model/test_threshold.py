"""Unit tests for the threshold model and the workpaper Determination.

Covers pass/fail across pct-only, count-only, both-set, and implicit-zero
threshold configurations, plus the prose the Conclusion section renders.
See ``docs/superpowers/specs/2026-06-18-workpaper-revisions-design.md`` §8/§9.
"""

from __future__ import annotations

import pytest

from uticen_lite.model.control import Threshold
from uticen_lite.model.workpaper import Determination


class TestThresholdParse:
    def test_empty_block_is_implicit_zero(self) -> None:
        t = Threshold.from_raw(None)
        assert t.is_implicit_zero is True
        assert t.failure_threshold_pct is None
        assert t.failure_threshold_count is None

    def test_empty_dict_is_implicit_zero(self) -> None:
        assert Threshold.from_raw({}).is_implicit_zero is True

    def test_pct_parsed(self) -> None:
        t = Threshold.from_raw({"failure_threshold_pct": 5})
        assert t.failure_threshold_pct == 5.0
        assert t.failure_threshold_count is None
        assert t.is_implicit_zero is False

    def test_count_parsed(self) -> None:
        t = Threshold.from_raw({"failure_threshold_count": 3})
        assert t.failure_threshold_count == 3
        assert t.failure_threshold_pct is None
        assert t.is_implicit_zero is False

    def test_both_parsed(self) -> None:
        t = Threshold.from_raw({"failure_threshold_pct": 10, "failure_threshold_count": 2})
        assert t.failure_threshold_pct == 10.0
        assert t.failure_threshold_count == 2

    @pytest.mark.parametrize("bad", [-1, 101, 150.5])
    def test_pct_out_of_range_rejected(self, bad: float) -> None:
        with pytest.raises(ValueError, match="failure_threshold_pct"):
            Threshold.from_raw({"failure_threshold_pct": bad})

    def test_negative_count_rejected(self) -> None:
        with pytest.raises(ValueError, match="failure_threshold_count"):
            Threshold.from_raw({"failure_threshold_count": -1})

    def test_to_dict_roundtrips(self) -> None:
        t = Threshold(failure_threshold_pct=5.0, failure_threshold_count=1)
        assert t.to_dict() == {
            "failure_threshold_pct": 5.0,
            "failure_threshold_count": 1,
            "rationale": None,
        }

    def test_rationale_parsed_and_serialized(self) -> None:
        t = Threshold.from_raw(
            {"failure_threshold_count": 2, "rationale": "  3% is immaterial here  "}
        )
        assert t.rationale == "3% is immaterial here"  # trimmed
        assert t.to_dict()["rationale"] == "3% is immaterial here"

    def test_blank_rationale_normalizes_to_none(self) -> None:
        assert Threshold.from_raw({"rationale": "   "}).rationale is None
        assert Threshold.from_raw({"failure_threshold_pct": 5}).rationale is None


class TestThresholdPasses:
    # ── implicit zero ─────────────────────────────────────────────────────────
    def test_implicit_zero_passes_with_no_exceptions(self) -> None:
        assert Threshold().passes(exception_count=0, records_tested=100) is True

    def test_implicit_zero_fails_with_any_exception(self) -> None:
        assert Threshold().passes(exception_count=1, records_tested=100) is False

    # ── pct only ──────────────────────────────────────────────────────────────
    def test_pct_passes_under_rate(self) -> None:
        # 4/100 = 4% <= 5%
        assert Threshold(failure_threshold_pct=5).passes(4, 100) is True

    def test_pct_passes_at_exact_rate(self) -> None:
        # 5/100 = 5% <= 5% (boundary inclusive)
        assert Threshold(failure_threshold_pct=5).passes(5, 100) is True

    def test_pct_fails_over_rate(self) -> None:
        # 6/100 = 6% > 5%
        assert Threshold(failure_threshold_pct=5).passes(6, 100) is False

    def test_pct_zero_records_no_exceptions_passes(self) -> None:
        assert Threshold(failure_threshold_pct=5).passes(0, 0) is True

    # ── count only ────────────────────────────────────────────────────────────
    def test_count_passes_under(self) -> None:
        assert Threshold(failure_threshold_count=3).passes(2, 100) is True

    def test_count_passes_at_exact(self) -> None:
        assert Threshold(failure_threshold_count=3).passes(3, 100) is True

    def test_count_fails_over(self) -> None:
        assert Threshold(failure_threshold_count=3).passes(4, 100) is False

    def test_count_zero_tolerates_nothing(self) -> None:
        assert Threshold(failure_threshold_count=0).passes(1, 100) is False
        assert Threshold(failure_threshold_count=0).passes(0, 100) is True

    # ── both set (AND semantics) ──────────────────────────────────────────────
    def test_both_pass_when_both_satisfied(self) -> None:
        # 3/100 = 3% <= 5% AND 3 <= 5
        assert Threshold(failure_threshold_pct=5, failure_threshold_count=5).passes(3, 100) is True

    def test_both_fail_when_count_exceeded(self) -> None:
        # rate OK (2%) but count 2 > 1
        assert Threshold(failure_threshold_pct=5, failure_threshold_count=1).passes(2, 100) is False

    def test_both_fail_when_pct_exceeded(self) -> None:
        # count OK (6 <= 10) but rate 6% > 5%
        assert (
            Threshold(failure_threshold_pct=5, failure_threshold_count=10).passes(6, 100) is False
        )


class TestDetermination:
    def test_verdict_pass(self) -> None:
        d = Determination(Threshold(failure_threshold_pct=5), exception_count=2, records_tested=100)
        assert d.passed is True
        assert d.verdict == "Operated effectively"
        assert d.exception_rate == 2.0

    def test_verdict_fail(self) -> None:
        d = Determination(Threshold(), exception_count=2, records_tested=100)
        assert d.passed is False
        assert d.verdict == "Operated with deficiencies"

    def test_conclusion_text_implicit_zero_fail(self) -> None:
        d = Determination(Threshold(), exception_count=4, records_tested=30)
        threshold_text, result_text = d.conclusion_text()
        assert threshold_text == "Threshold: zero exceptions tolerated."
        assert "4 exception(s)" in result_text
        assert "did not operate effectively" in result_text

    def test_conclusion_text_implicit_zero_pass(self) -> None:
        d = Determination(Threshold(), exception_count=0, records_tested=30)
        threshold_text, result_text = d.conclusion_text()
        assert threshold_text == "Threshold: zero exceptions tolerated."
        assert "0 exceptions" in result_text
        assert "operated effectively" in result_text

    def test_conclusion_text_pct(self) -> None:
        d = Determination(Threshold(failure_threshold_pct=5), exception_count=4, records_tested=30)
        threshold_text, result_text = d.conclusion_text()
        assert "at or below 5%" in threshold_text
        assert "13.33% (4 / 30 records)" in result_text
        assert "exceeds threshold" in result_text

    def test_conclusion_text_pct_and_count(self) -> None:
        d = Determination(
            Threshold(failure_threshold_pct=5, failure_threshold_count=0),
            exception_count=4,
            records_tested=30,
        )
        threshold_text, _ = d.conclusion_text()
        assert "at or below 5%" in threshold_text
        assert "no more than 0 exception(s)" in threshold_text

    def test_conclusion_text_count_only_pass(self) -> None:
        d = Determination(
            Threshold(failure_threshold_count=2), exception_count=2, records_tested=30
        )
        threshold_text, result_text = d.conclusion_text()
        assert "no more than 2 exception(s)" in threshold_text
        assert "within threshold" in result_text
