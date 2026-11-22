"""Rule-based privacy screen.

Pattern screening is best effort. It finds identifiers that have a recognisable
shape or a label: South African ID numbers, phone numbers, email addresses,
labelled account, card and reference numbers, labelled names and dates of birth,
and operator patterns. It cannot catch an unlabelled name ("my colleague Thandi"),
a free-text description of someone's health or circumstances, an identifier
written in words, or a format nobody wrote a pattern for. It reduces exposure; it
does not guarantee that no personal information gets through.

Screening fails closed: any internal error raises instead of returning unscreened text.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from functools import lru_cache

from kestrel.contracts import PrivacyPolicy, RedactionPattern, Screened
from kestrel.errors import ConfigurationError, KestrelError, PrivacyBlockedError
from kestrel.privacy.rules import BUILTIN_RULES, Rule, normalise, placeholder

MAX_PATTERN_CHARS = 300
MAX_OPERATOR_PATTERNS = 32
MAX_SCREEN_CHARS = 100_000


def compile_operator_patterns(patterns: Sequence[RedactionPattern]) -> tuple[tuple[str, re.Pattern[str]], ...]:
    """Validate and compile a bot's extra patterns.

    Raises:
        ConfigurationError: too many patterns, a pattern longer than the cap, one
            that does not compile, or one that matches the empty string.
    """
    return _compile(tuple((p.kind, p.pattern) for p in patterns))


@lru_cache(maxsize=64)
def _compile(items: tuple[tuple[str, str], ...]) -> tuple[tuple[str, re.Pattern[str]], ...]:
    if len(items) > MAX_OPERATOR_PATTERNS:
        raise ConfigurationError(f"privacy.extra_patterns has {len(items)} entries; at most {MAX_OPERATOR_PATTERNS} are allowed.")
    compiled = []
    for index, (kind, pattern) in enumerate(items):
        where = f"privacy.extra_patterns[{index}] ({kind!r})"
        if not pattern or len(pattern) > MAX_PATTERN_CHARS:
            raise ConfigurationError(f"{where}: the pattern must be 1 to {MAX_PATTERN_CHARS} characters long.")
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except (re.error, RecursionError, OverflowError) as exc:
            raise ConfigurationError(f"{where}: the pattern does not compile ({exc}). Fix the regular expression.") from None
        if regex.search("") is not None:
            raise ConfigurationError(f"{where}: the pattern matches the empty string, which would redact everything. Make it require at least one character.")
        compiled.append((kind, regex))
    return tuple(compiled)


class RuleBasedScreen:
    """`PrivacyScreen` built from deterministic rules plus each bot's operator patterns."""

    def __init__(self, rules: Sequence[Rule] = BUILTIN_RULES) -> None:
        self._rules = tuple(rules)

    def screen(self, text: str, policy: PrivacyPolicy) -> Screened:
        """Normalise `text`, then replace every match with a `[KIND]` placeholder.

        Raises:
            ConfigurationError: an operator pattern in the policy is invalid.
            PrivacyBlockedError: screening could not complete (fail closed).
        """
        if not isinstance(text, str):
            raise PrivacyBlockedError("The question could not be screened for personal information, so it was not sent.")
        if len(text) > MAX_SCREEN_CHARS:
            raise PrivacyBlockedError(f"The text is longer than {MAX_SCREEN_CHARS} characters and was not screened or sent. Shorten it.")
        operator = compile_operator_patterns(policy.extra_patterns)
        matched: set[str] = set()
        try:
            value = normalise(text)
            for rule in self._rules:
                value = rule.apply(value, matched)
            for kind, regex in operator:
                value = regex.sub(lambda _m, kind=kind: _mark(kind, matched), value)
        except KestrelError:
            raise
        except Exception as exc:
            raise PrivacyBlockedError(
                "The question could not be screened for personal information, so it was not sent. Try again or rephrase it.",
                detail=f"screen failed: {type(exc).__name__}",
            ) from None
        return Screened(text=value, matched=tuple(sorted(matched)))


def _mark(kind: str, matched: set[str]) -> str:
    matched.add(kind)
    return placeholder(kind)
