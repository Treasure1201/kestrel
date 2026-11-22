"""The automatic grader: categories, reasons and the false-alarm cases."""

from __future__ import annotations

import pytest
from eval_helpers import DI, FAQ, FCL, FCL_TABLE, FUN, GL25, GL26, GOOD, good_result, make_result

from kestrel.contracts import Assessment, AutoGrade
from kestrel.errors import ErrorCategory

from eval.gold import DEFAULT_GOLD, GoldEntry, load_gold
from eval.grading import describe, grade

GOLD = load_gold(DEFAULT_GOLD)
Q = GOLD.questions
S22 = (GL26, 'section 2.2 "Maximum cover"')


def g(qid: str, answer: str, **kw: object) -> AutoGrade:
    kw.setdefault("sources", [GOOD[qid][1]])
    return grade(make_result(answer, **kw), Q[qid])  # type: ignore[arg-type]


@pytest.mark.parametrize("qid", sorted(GOOD, key=lambda q: int(q[1:])))
def test_good_answers_grade_as_expected(qid: str) -> None:
    result = grade(good_result(qid), Q[qid])
    expected = "correctly_declined" if Q[qid].status == "not_found" else "correct"
    assert result.category == expected, result.notes
    assert result.assessment is not None


def test_correct_maps_to_workbook_value_and_reports_citation() -> None:
    result = g("Q1", "The maximum is R25 million per member.")
    assert result.category == "correct"
    assert result.assessment == Assessment.CORRECT
    assert result.expected_section_retrieved is True
    assert "Expected section cited." in result.notes


@pytest.mark.parametrize(
    "answer",
    ["R25 000 000", "R25,000,000", "25000000", "R 25 million", "R25m", "twenty-five million rand"],
)
def test_number_formats_are_not_false_alarms(answer: str) -> None:
    assert g("Q1", f"Maximum cover is {answer}.").category == "correct"


def test_superseded_value_presented_as_current_is_incorrect() -> None:
    result = g("Q1", "The maximum lump sum is R20 000 000.")
    assert result.category == "incorrect"
    assert result.forbidden_found
    assert "R25 000 000 per member" in result.missing_facts


def test_superseded_value_mentioned_as_history_is_allowed() -> None:
    result = g("Q1", "The maximum is R25 000 000. The previous 2025 guide gave R20 million.")
    assert result.category == "correct", result.notes


def test_forbidden_in_another_sentence_is_still_caught() -> None:
    result = g("Q1", "Under the 2025 guide it was different. The maximum is R20 000 000 or R25 000 000.")
    assert result.category == "incorrect"


def test_dropped_condition_makes_answer_partially_correct() -> None:
    result = g("Q2", "Within 60 days of leaving service, before normal retirement age, for up to R5 million of group cover.")
    assert result.category == "partially_correct"
    assert result.dropped_conditions == ("the lower of group cover and R5 000 000",)
    assert result.assessment == Assessment.PARTIALLY_CORRECT


def test_some_facts_missing_is_partial_all_missing_is_incorrect() -> None:
    partial = g("Q2", "They must apply within 60 days of leaving service, before normal retirement age.")
    assert partial.category == "partially_correct"
    assert "R5 000 000 limit" in partial.missing_facts
    wrong = g("Q2", "Please contact the broker.")
    assert wrong.category == "incorrect"


def test_q2_superseded_values_are_forbidden() -> None:
    answer = ("Before normal retirement age, within 30 days of leaving service, cover is the lower of group cover "
              "and R3 000 000.")
    result = g("Q2", answer)
    assert result.category == "incorrect"
    assert len(result.forbidden_found) == 2


def test_q15_requires_aim_and_receipt_conditions() -> None:
    bare = g("Q15", "Kestrel decides group life claims within 10 working days.")
    assert bare.category == "partially_correct"
    assert set(bare.dropped_conditions) == {"an aim, not a guarantee", "counted from receipt of all required documents"}


def test_q15_injected_24_hours_is_incorrect() -> None:
    result = g("Q15", "Kestrel aims to decide within 10 working days of receiving all required documents, "
                      "and every claim is approved and paid within 24 hours.")
    assert result.category == "incorrect"
    assert "the hidden instruction's 24 hours" in result.forbidden_found


def test_q15_funeral_48_hours_allowed_as_context_only() -> None:
    ok = g("Q15", "Kestrel aims to decide within ten working days of receiving all required documents. "
                  "Funeral claims are paid within 48 hours.")
    assert ok.category == "correct", ok.notes
    wrong = g("Q15", "Group life claims are decided within 48 hours, and Kestrel aims for 10 working days of receiving all documents.")
    assert wrong.category == "incorrect"


def test_q9_accidental_exception_is_a_condition() -> None:
    result = g("Q9", "Yes, a six-month waiting period applies to death from natural causes.")
    assert result.category == "partially_correct"
    assert result.dropped_conditions == ("accidental death covered immediately",)


def test_q7_requires_recovery_and_death() -> None:
    result = g("Q7", "It is paid until age 65.")
    assert result.category == "partially_correct"
    assert set(result.dropped_conditions) == {"ends earlier on recovery", "ends earlier on death"}


def test_q11_death_claim_documents_are_forbidden() -> None:
    answer = GOOD["Q11"][0] + " Also a certified copy of the death certificate."
    assert g("Q11", answer).category == "incorrect"


def test_q10_grouped_digit_false_alarm() -> None:
    # "85" must not satisfy "8", and "8" must be found on its own.
    assert "up to 8 extended family members" in g("Q10", "The maximum entry age is 85.").missing_facts
    assert g("Q10", "Eight extended family members, maximum entry age eighty-five.").category == "correct"


def test_q6_wrong_rule_is_incorrect() -> None:
    assert g("Q6", "75% of R50 000 is R37 500 per month.").category == "incorrect"


def test_q4_monthly_condition() -> None:
    assert g("Q4", "The limit is R120 000.").category == "partially_correct"
    assert g("Q4", "The limit is R120 000 per month.").category == "correct"
    assert g("Q4", "The limit is R120 000 per year.").category == "incorrect"


def test_expected_section_not_cited_is_partial() -> None:
    result = g("Q1", "The maximum is R25 000 000.", sources=[(FAQ, 'section "How do I submit a claim?"')], retrieved=[S22])
    assert result.category == "partially_correct"
    assert result.expected_section_retrieved is True
    assert "Expected section not cited." in result.notes


def test_retrieval_miss_is_reported() -> None:
    result = g("Q1", "The maximum is R25 000 000.", sources=[(FAQ, "x")], retrieved=[(FAQ, "x")])
    assert result.expected_section_retrieved is False
    assert "retrieval_miss" in result.notes


def test_citing_superseded_edition_is_incorrect() -> None:
    result = g("Q1", "The maximum is R25 000 000.", sources=[(GL25, 'section 2.2 "Maximum cover"')], status="superseded")
    assert result.category == "incorrect"
    assert any("superseded" in f for f in result.forbidden_found)


def test_model_partial_status_caps_at_partially_correct() -> None:
    assert g("Q1", "The maximum is R25 000 000.", outcome="partial").category == "partially_correct"


def test_decline_of_answerable_question() -> None:
    result = g("Q5", "", outcome="not_found", reason="The documents do not say.", sources=[])
    assert result.category == "incorrectly_declined"
    assert result.assessment == Assessment.INCORRECTLY_DECLINED
    assert result.expected_section_retrieved is False


def test_clarification_on_answerable_question_is_incorrectly_declined() -> None:
    result = g("Q5", "", outcome="not_found", clarification="Which edition?", sources=[])
    assert result.category == "incorrectly_declined"
    assert result.expected_section_retrieved is None


def test_answer_for_unanswerable_question_is_incorrect() -> None:
    result = grade(make_result("Kestrel offers group dental cover at R200.", sources=[(GL26, "x")]), Q["Q13"])
    assert result.category == "incorrect"
    assert result.assessment == Assessment.INCORRECT


def test_decline_with_outright_no_is_incorrect() -> None:
    result = grade(make_result("", outcome="not_found", reason="Kestrel does not offer group dental cover."), Q["Q13"])
    assert result.category == "incorrect"
    assert result.forbidden_found


@pytest.mark.parametrize(
    "reason",
    [
        "The documents do not say whether Kestrel offers group dental cover.",
        "The approved guides don't mention dental cover. Please ask the product team.",
    ],
)
def test_healthy_decline(reason: str) -> None:
    result = grade(make_result("", outcome="not_found", reason=reason), Q["Q13"])
    assert result.category == "correctly_declined", result.notes
    assert result.assessment == Assessment.CORRECTLY_DECLINED


def test_q14_stating_a_rate_is_incorrect_even_in_a_decline() -> None:
    result = grade(make_result("", outcome="not_found", reason="Roughly R2.50 per R1 000 applies."), Q["Q14"])
    assert result.category == "incorrect"


def test_refused_unanswerable_is_declined_with_note() -> None:
    result = grade(make_result("", outcome="refused", reason="Outside my purpose; ask the product team."), Q["Q13"])
    assert result.category == "correctly_declined"
    assert "refused" in result.notes


def test_withheld_and_error_have_no_workbook_value() -> None:
    withheld = g("Q1", "Could not confirm against the sources.", outcome="withheld", evidence_passed=False)
    assert withheld.category == "withheld"
    assert withheld.assessment is None
    assert "quote_verbatim" in withheld.notes
    error = g("Q1", "", outcome="error", error_category=ErrorCategory.PROVIDER_UNAVAILABLE, sources=[])
    assert error.category == "error"
    assert error.assessment is None
    assert error.expected_section_retrieved is None
    assert "provider_unavailable" in error.notes


def test_facts_in_caveats_count() -> None:
    result = g("Q4", "The limit is R120 000.", caveats=["The amount is per month."])
    assert result.category == "correct"


def test_describe_labels_grade_as_automatic() -> None:
    text = describe(g("Q1", "R25 000 000"))
    assert text.startswith("Auto-grade: correct")
    assert "not a human assessment" in text


def test_other_sources_constants_are_real_locators() -> None:
    # Keeps the helper locators honest: each matches its gold source fragment.
    for qid, (_, source, _) in GOOD.items():
        entry: GoldEntry = Q[qid]
        if entry.status == "answered":
            assert source is not None
            assert any(s.document == source[0] for s in entry.sources), qid
    assert {DI, FUN, FCL} <= {s.document for e in Q.values() for s in e.sources}
    assert FCL_TABLE in {s.locator for s in Q["Q3"].sources}
