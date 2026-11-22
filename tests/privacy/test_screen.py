"""Rule-based privacy screen: each identifier kind, near-misses, amounts that must survive, operator patterns, fail-closed."""

from __future__ import annotations

import re

import pytest

from kestrel.contracts import PrivacyPolicy, PrivacyScreen, RedactionPattern
from kestrel.errors import ConfigurationError, PrivacyBlockedError
from kestrel.privacy import BUILTIN_RULES, RuleBasedScreen, is_valid_sa_id, normalise
from kestrel.privacy.rules import Rule

VALID_ID = "8001015009087"  # 1980-01-01, citizen, valid check digit (synthetic)
SCREEN = RuleBasedScreen()
POLICY = PrivacyPolicy()


def screen(text: str, policy: PrivacyPolicy = POLICY):
    return SCREEN.screen(text, policy)


def test_implements_the_protocol() -> None:
    assert isinstance(SCREEN, PrivacyScreen)


def test_sa_id_validation() -> None:
    assert is_valid_sa_id(VALID_ID)
    assert not is_valid_sa_id("8001015009088")  # wrong check digit
    assert not is_valid_sa_id("8013015009087")  # month 13
    assert not is_valid_sa_id("800101500908")  # 12 digits


@pytest.mark.parametrize(
    ("text", "kind", "expected"),
    [
        (f"My ID is {VALID_ID}.", "sa_id", "My ID is [SA_ID]."),
        ("ID 800101 5009 087 please", "sa_id", "ID [SA_ID] please"),
        ("Number 1234567890123 on file", "identifier", "Number [IDENTIFIER] on file"),
        ("Call me on +27 82 555 0123", "phone", "Call me on [PHONE]"),
        ("Her number is 082-555-0123.", "phone", "Her number is [PHONE]."),
        ("Office: (021) 555 0199", "phone", "Office: [PHONE]"),
        ("Write to jo.doe+cover@example.invalid today", "email", "Write to [EMAIL] today"),
        ("Bank account number: 6200 1234 567", "bank_account", "Bank account number: [BANK_ACCOUNT]"),
        ("card number 4111 1111 1111 1111", "card_number", "card number [CARD_NUMBER]"),
        ("My policy number is ABC-0000/0", "policy_number", "My policy number is [POLICY_NUMBER]"),
        ("Member no. M12345678 asked", "member_number", "Member no. [MEMBER_NUMBER] asked"),
        ("claim #: CL202611", "claim_number", "claim #: [CLAIM_NUMBER]"),
        ("employee number E4471", "employee_number", "employee number [EMPLOYEE_NUMBER]"),
        ("Passport no. A01234567", "passport_number", "Passport no. [PASSPORT_NUMBER]"),
        ("Member name: Sipho Dlamini", "name", "Member name: [NAME]"),
        ("my name is Thandi Nkosi and", "name", "my name is [NAME] and"),
        ("I spoke to Mrs. Sample Person", "name", "I spoke to Mrs. [NAME]"),
        ("Dr Naidoo phoned", "name", "Dr [NAME] phoned"),
        ("Date of birth: 1980-01-01", "date_of_birth", "Date of birth: [DATE_OF_BIRTH]"),
        ("DOB is 29 January 2000.", "date_of_birth", "DOB is [DATE_OF_BIRTH]."),
    ],
)
def test_each_identifier_kind_is_redacted(text: str, kind: str, expected: str) -> None:
    result = screen(text)
    assert kind in result.matched
    assert result.text == expected


@pytest.mark.parametrize(
    "text",
    [
        "Reference 123456789012 is short",  # 12 digits: not a 13-digit identifier
        "Call 082 555 012",  # 9 digits: too short for a phone number
        "021 555",  # too short
        "Email the team at example dot invalid",
        "How does the policy number appear on the form?",
        "Is a member number required to claim?",
        "What is the earliest date of birth eligible under the rule?",
        "The member name is required on the form.",
        "Name is required",
        "MS Teams link",
        "Account for 1 200 members",
        "Card limits differ by plan",
        "born in 1980",
        "@support on the intranet",
    ],
)
def test_near_misses_are_left_alone(text: str) -> None:
    result = screen(text)
    assert result.matched == ()
    assert result.text == text


@pytest.mark.parametrize(
    "text",
    [
        "What is the funeral benefit of R50 000?",
        "A scheme with 1 200 members",
        "Does it apply to 320 members?",
        "Is the free cover limit R25 000 000?",
        "R1 000 000 000 000 total",
        "Members aged 65 and over",
        "A member who is 42 years old",
        "75% of salary, or 2.5 times annual salary",
        "Under the 2025 guide, from 2026-01-01",
        "What does Option B cover?",
        "Within 30 working days",
        "Section 4.2 and table 3",
    ],
)
def test_quantities_and_amounts_are_not_redacted(text: str) -> None:
    result = screen(text)
    assert result.matched == ()
    assert result.text == text


def test_zero_width_characters_inside_an_id_are_caught() -> None:
    obfuscated = "ID: 800101​5009‍087 and e­mail x＠example.invalid"
    result = screen(obfuscated)
    assert result.matched == ("email", "sa_id")
    assert result.text == "ID: [SA_ID] and email [EMAIL]"


def test_normalise_keeps_newlines_and_folds_width() -> None:
    assert normalise("ａ​\nb\tc\x07") == "a\nb\tc"


def test_operator_patterns_redact_with_their_kind() -> None:
    policy = PrivacyPolicy(extra_patterns=(RedactionPattern(kind="staff_code", pattern=r"\bKST-\d{4}\b"),))
    result = screen("Ticket from kst-1234 today", policy)
    assert result.matched == ("staff_code",)
    assert result.text == "Ticket from [STAFF_CODE] today"


@pytest.mark.parametrize(
    ("pattern", "message"),
    [("a*", "empty string"), ("x" * 301, "1 to 300"), ("(unclosed", "does not compile")],
)
def test_bad_operator_patterns_are_configuration_errors(pattern: str, message: str) -> None:
    bad = RedactionPattern.model_construct(kind="bad", pattern=pattern)  # bypass the contract's own compile check
    policy = PrivacyPolicy.model_construct(extra_patterns=(bad,))
    with pytest.raises(ConfigurationError, match=message):
        screen("anything", policy)


def test_too_many_operator_patterns() -> None:
    many = tuple(RedactionPattern(kind=f"k{i}", pattern=rf"\bZ{i}Q\b") for i in range(33))
    with pytest.raises(ConfigurationError, match="at most 32"):
        screen("x", PrivacyPolicy(extra_patterns=many))


def test_internal_failure_raises_instead_of_returning_text() -> None:
    def explode(match: re.Match[str], matched: set[str]) -> str:
        raise RuntimeError("boom")

    broken = RuleBasedScreen((*BUILTIN_RULES, Rule("broken", re.compile("question"), explode)))
    with pytest.raises(PrivacyBlockedError) as info:
        broken.screen("a question", POLICY)
    assert "not sent" in info.value.user_message
    with pytest.raises(PrivacyBlockedError):
        SCREEN.screen(None, POLICY)  # type: ignore[arg-type]
    with pytest.raises(PrivacyBlockedError, match="longer than"):
        SCREEN.screen("x" * 100_001, POLICY)


def test_several_kinds_in_one_question_are_listed_sorted_without_values() -> None:
    result = screen(f"Member name: Jo Smith, ID {VALID_ID}, phone 0825550123. Is R50 000 covered?")
    assert result.matched == ("name", "phone", "sa_id")
    assert VALID_ID not in result.text and "0825550123" not in result.text and "R50 000" in result.text
