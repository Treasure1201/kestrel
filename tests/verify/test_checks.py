"""The status contract and each of the seven evidence checks, passing and failing."""

from __future__ import annotations

from verify.builders import EXPLICIT, SCOPE, answer, prose, table_evidence

from kestrel.agent.calculator import evaluate
from kestrel.contracts import DocumentStatus
from kestrel.verify import evidence_report, repair_message, run_checks
from kestrel.verify.checks import self_reference

WAITING = prose("c1", "Option B: the waiting period is three months. Benefits are paid monthly in arrears.")
CLAIMS = prose(
    "c3",
    "The insurer aims to make a decision within 10 working days of receiving all required documents.",
    locator='section "How long does it take?"',
)
LIMIT = prose("c4", "The benefit is 75% of monthly salary, up to a maximum of R50 000 per month.")
CAP = prose("c5", "The cover is the lower of 3 times annual salary and R5 million.")
EVIDENCE = {e.label: e for e in (WAITING, CLAIMS, LIMIT, CAP)}


def checks(ans, evidence=None, *, question="What applies?", scope=SCOPE, tool_calculations=()):
    return run_checks(question, ans, evidence if evidence is not None else EVIDENCE, scope, tool_calculations)


def names(issues) -> list[str]:
    return [issue.check for issue in issues]


# Status contract -------------------------------------------------------------------------


def test_answered_without_citation_fails_the_status_contract() -> None:
    issues = checks(answer("The waiting period is three months."))
    assert "schema" in names(issues)


def test_well_supported_answer_passes_every_check() -> None:
    ans = answer(
        "The insurer aims to decide within 10 working days of receiving all required documents.",
        [("c3", "aims to make a decision within 10 working days of receiving all required documents")],
    )
    issues = checks(ans)
    assert issues == []
    assert evidence_report(issues, repaired=False).passed


# 1. label retrieved ----------------------------------------------------------------------


def test_label_not_retrieved_fails() -> None:
    issues = checks(answer("Three months.", [("c9", "three months")]))
    assert names(issues).count("label_retrieved") == 1
    assert "c9" in issues[0].detail and "c1" in issues[0].detail


def test_label_with_brackets_and_capitals_is_the_same_label() -> None:
    assert checks(answer("The waiting period is three months.", [("[C1]", "the waiting period is three months")])) == []


# 2. quote verbatim -----------------------------------------------------------------------


def test_paraphrased_quote_fails() -> None:
    issues = checks(answer("Three months.", [("c1", "the waiting period lasts for three months")]))
    assert names(issues) == ["quote_verbatim"]
    assert "c1" in issues[0].detail


def test_quote_from_another_excerpt_names_the_right_label() -> None:
    issues = checks(answer("Ten working days.", [("c1", "within 10 working days")]))
    assert names(issues)[0] == "quote_verbatim"
    assert "c3" in issues[0].detail


def test_quote_normalises_whitespace_case_unicode_and_digit_grouping() -> None:
    ans = answer(
        "Up to R50 000 per month.",
        [("c4", "UP TO A MAXIMUM OF R50,000 per   month")],
    )
    assert checks(ans) == []


def test_quote_with_ellipsis_must_keep_order() -> None:
    good = answer("Three months, paid monthly.", [("c1", "the waiting period is three months ... paid monthly in arrears")])
    assert checks(good) == []
    bad = answer("Three months, paid monthly.", [("c1", "paid monthly ... the waiting period is three months")])
    assert names(checks(bad)) == ["quote_verbatim"]


def test_quote_from_a_single_table_row() -> None:
    evidence = table_evidence()
    good = answer("A scheme of 120 members has a free cover limit of R3 500 000.", [("c2.r3", "Members to: 249; Free cover limit (R): R 3 500 000")])
    assert checks(good, evidence, question="What is the free cover limit for 120 members?") == []
    cells = answer("The limit is R3 500 000.", [("c2.r3", "100 | 249 | R 3 500 000")])
    assert checks(cells, evidence) == []
    wrong_row = answer("The limit is R3 500 000.", [("c2.r2", "R 3 500 000")])
    issues = checks(wrong_row, evidence)
    assert "quote_verbatim" in names(issues)
    assert "c2.r3" in issues[0].detail


# 3. table row cited ----------------------------------------------------------------------


def test_citing_a_whole_table_fails() -> None:
    evidence = table_evidence()
    issues = checks(answer("See the table.", [("c2", "Band | Members from")]), evidence)
    assert names(issues) == ["table_row_cited"]
    assert "get_table" in issues[0].detail


# 4. numbers grounded ---------------------------------------------------------------------


def test_unsupported_number_fails() -> None:
    issues = checks(answer("The waiting period is six months.", [("c1", "the waiting period is three months")]))
    assert names(issues) == ["numbers_grounded"]
    assert "6" in issues[0].detail


def test_number_words_and_digits_are_the_same_number() -> None:
    assert checks(answer("The waiting period is 3 months.", [("c1", "the waiting period is three months")])) == []


def test_grouped_numbers_and_magnitudes_trace() -> None:
    ans = answer(
        "Cover is the lower of 3 times annual salary and R5 000 000 (R5,000,000).",
        [("c5", "the lower of 3 times annual salary and R5 million")],
    )
    assert checks(ans) == []


def test_number_in_caveats_must_trace_too() -> None:
    ans = answer("Three months.", [("c1", "the waiting period is three months")], caveats=["Claims older than 90 days are reviewed."])
    issues = checks(ans)
    assert names(issues) == ["numbers_grounded"]
    assert "caveats" in issues[0].detail


def test_number_from_an_uncited_excerpt_asks_for_the_citation() -> None:
    ans = answer("Three months, and decisions take 10 working days.", [("c1", "the waiting period is three months")])
    issues = checks(ans)
    assert names(issues) == ["numbers_grounded"]
    assert "c3" in issues[0].detail


def test_numbers_from_the_question_list_markers_and_labels_are_allowed() -> None:
    ans = answer(
        "1. For a scheme of 120 members (see c3):\n2. decisions take 10 working days.",
        [("c3", "within 10 working days")],
    )
    assert checks(ans, question="We have 120 members. How long?") == []


def test_numbers_in_the_source_title_and_locator_are_allowed() -> None:
    ans = answer("Under the 2026 guide, section 4, the waiting period is three months.", [("c1", "the waiting period is three months")])
    assert checks(ans) == []


def test_a_percentage_supports_its_fraction_form() -> None:
    ans = answer(
        "The benefit is 0.75 x R40 000 = R30 000 a month.",
        [("c4", "75% of monthly salary")],
        calculations=[("0.75 * 40000", "30000", "c4")],
    )
    assert checks(ans, question="Salary R40 000?") == []


# 5. calculations -------------------------------------------------------------------------


def test_correct_traced_calculation_passes() -> None:
    ans = answer(
        "75% of a salary of R40 000 is R30 000 a month, below the R50 000 maximum.",
        [("c4", "75% of monthly salary, up to a maximum of R50 000 per month")],
        calculations=[("0.75 * 40000", "30000", "c4")],
    )
    assert checks(ans, question="Salary R40 000. What is the benefit?") == []


def test_result_copied_with_its_expression_is_accepted() -> None:
    ans = answer("R30 000.", [("c4", "75% of monthly salary")], calculations=[("0.75 * 40000", "0.75 * 40000 = 30000", "c4")])
    assert checks(ans, question="Salary R40 000?") == []


def test_wrong_result_fails() -> None:
    ans = answer("R31 000.", [("c4", "75% of monthly salary")], calculations=[("0.75 * 40000", "31000", "c4")])
    issues = checks(ans, question="Salary R40 000?")
    assert "calculation_recomputed" in names(issues)
    assert "30000" in " ".join(issue.detail for issue in issues)


def test_untraced_operand_fails() -> None:
    ans = answer("R32 000.", [("c4", "75% of monthly salary")], calculations=[("0.8 * 40000", "32000", "c4")])
    issues = checks(ans, question="Salary R40 000?")
    assert "calculation_recomputed" in names(issues)
    assert "0.8" in " ".join(issue.detail for issue in issues)


def test_rule_label_must_be_present_and_retrieved() -> None:
    missing = answer("R30 000.", [("c4", "75% of monthly salary")], calculations=[("0.75 * 40000", "30000", "")])
    assert "calculation_recomputed" in names(checks(missing, question="Salary R40 000?"))
    unknown = answer("R30 000.", [("c4", "75% of monthly salary")], calculations=[("0.75 * 40000", "30000", "c8")])
    assert "calculation_recomputed" in names(checks(unknown, question="Salary R40 000?"))


def test_chained_calculation_uses_an_earlier_verified_result() -> None:
    ans = answer(
        "R30 000 a month, or R360 000 a year.",
        [("c4", "75% of monthly salary")],
        calculations=[("0.75 * 40000", "30000", "c4"), ("30000 * 12", "360000", "c4")],
    )
    assert checks(ans, question="Salary R40 000?") == []


def test_unparseable_calculation_fails() -> None:
    ans = answer("R30 000.", [("c4", "75% of monthly salary")], calculations=[("salary * 0.75", "30000", "c4")])
    assert "calculation_recomputed" in names(checks(ans))


# 6. declines -----------------------------------------------------------------------------


def test_correct_decline_needs_no_citation() -> None:
    ans = answer("The documents do not say.", status="not_found", reason="The documents do not state dental cover.")
    assert checks(ans) == []


def test_decline_without_reason_fails() -> None:
    assert names(checks(answer("No.", status="not_found"))) == ["decline_reason"]
    assert names(checks(answer("No.", status="refused"))) == ["decline_reason"]


def test_decline_after_hidden_arithmetic_fails() -> None:
    ans = answer("The documents do not say.", status="not_found", reason="The documents do not state it.")
    issues = checks(ans, tool_calculations=[evaluate("0.75 * 40000")])
    assert names(issues) == ["decline_reason"]
    assert "0.75 * 40000" in issues[0].detail


def test_decline_may_mention_numbers_from_any_retrieved_excerpt() -> None:
    ans = answer("The documents give a 10 working day target but no turnaround for this.", status="not_found", reason="The documents do not say.")
    assert checks(ans) == []


# 7. source status ------------------------------------------------------------------------


def test_superseded_source_fails_unless_scope_was_explicit() -> None:
    old = prose("c6", "The waiting period is six months.", status=DocumentStatus.SUPERSEDED, edition="2025")
    ans = answer("Six months.", [("c6", "the waiting period is six months")])
    assert names(checks(ans, {"c6": old})) == ["source_status"]
    assert checks(ans, {"c6": old}, scope=EXPLICIT) == []


def test_blocked_source_always_fails() -> None:
    blocked = prose("c7", "The waiting period is six months.", status=DocumentStatus.BLOCKED)
    ans = answer("Six months.", [("c7", "the waiting period is six months")])
    assert names(checks(ans, {"c7": blocked}, scope=EXPLICIT)) == ["source_status"]


# Repair message --------------------------------------------------------------------------


def test_repair_message_lists_each_issue() -> None:
    issues = checks(answer("Six months.", [("c9", "x")]))
    message = repair_message(issues)
    assert "label_retrieved" in message and "c9" in message
    assert "ALL the citations" in message and "rather than explain it" in message
    assert "Never mention drafts, checks" in message


# Self-reference --------------------------------------------------------------------------


def test_self_reference_in_the_answer_or_caveats_fails() -> None:
    ans = answer("The waiting period is three months. The earlier draft was wrong.", [("c1", "the waiting period is three months")])
    issues = checks(ans)
    assert names(issues) == ["schema"] and "earlier draft" in issues[0].detail
    caveat = answer("The waiting period is three months.", [("c1", "the waiting period is three months")],
                    caveats=["The first draft contradicted itself."])
    assert names(checks(caveat)) == ["schema"]


def test_self_reference_words() -> None:
    for text in ("The first draft contradicted itself", "my previous answer", "the evidence check failed", "after repair"):
        assert self_reference(text), text
    for text in ("Repairs to the building are covered.", "A draft of the policy", "The answer is three months."):
        assert self_reference(text) is None, text
