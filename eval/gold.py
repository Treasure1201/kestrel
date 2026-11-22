"""Gold answers: the file format, validation and loading.

A gold file is YAML with a top-level `questions` mapping keyed by question id. Each
entry says what a correct answer must contain, which conditions it must keep, what it
must never say, and which document section should support it. Facts, conditions and
forbidden statements are lists of acceptable variants, matched after the number and
text normalisation in `eval.normalise`. See `bots/kestrel-products/eval/gold.yaml`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from eval.normalise import variant_pattern

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GOLD = REPO_ROOT / "bots" / "kestrel-products" / "eval" / "gold.yaml"
REWORDED = REPO_ROOT / "eval" / "reworded.yaml"


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _check_variants(variants: tuple[str, ...]) -> tuple[str, ...]:
    if not variants:
        raise ValueError("at least one variant is required")
    for variant in variants:
        variant_pattern(variant)  # raises on an empty phrase or a bad regex
    return variants


class Requirement(_Strict):
    """A fact or condition: satisfied when any variant appears."""

    label: str = Field(min_length=1)
    any: tuple[str, ...]

    @field_validator("any")
    @classmethod
    def _variants(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _check_variants(value)


class Forbidden(_Strict):
    """A statement that must not appear.

    A clause that also contains one of `unless_clause_has` is not counted, so an answer
    may mention the superseded value when it says so in the same clause ("the 2025 guide
    gave R20 million"). Context markers are matched like variants.
    """

    label: str = Field(min_length=1)
    any: tuple[str, ...]
    unless_clause_has: tuple[str, ...] = ()

    @field_validator("any")
    @classmethod
    def _variants(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _check_variants(value)

    @field_validator("unless_clause_has")
    @classmethod
    def _markers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for marker in value:
            variant_pattern(marker)
        return value


class ExpectedSource(_Strict):
    """A document and a fragment of the section locator that should support the answer."""

    document: str = Field(min_length=1)
    """Filename, or a fragment of it."""
    locator: str = Field(min_length=1)
    """Fragment of the section locator, as the parser writes it."""


def _as_requirements(value: object) -> object:
    """Accept `[variant, ...]` lists as shorthand for `{label: <first>, any: [...]}`."""
    if not isinstance(value, list):
        return value
    out = []
    for item in value:
        if isinstance(item, list):
            out.append({"label": str(item[0]) if item else "", "any": item})
        elif isinstance(item, str):
            out.append({"label": item, "any": [item]})
        else:
            out.append(item)
    return out


class GoldEntry(_Strict):
    """Gold answer for one question."""

    question: str = Field(min_length=1)
    status: Literal["answered", "not_found"]
    must_include: tuple[Requirement, ...] = ()
    conditions: tuple[Requirement, ...] = ()
    must_not_include: tuple[Forbidden, ...] = ()
    sources: tuple[ExpectedSource, ...] = ()
    allowed_statuses: tuple[Literal["current", "superseded"], ...] = ("current",)
    """Document statuses an answer may cite; `superseded` only for questions naming that edition."""
    calculated: bool = False
    """True when a required figure is derived by arithmetic and is not printed in the source."""
    paraphrase_of: str | None = None
    note: str = ""

    @field_validator("must_include", "conditions", mode="before")
    @classmethod
    def _shorthand(cls, value: object) -> object:
        return _as_requirements(value)

    @model_validator(mode="after")
    def _consistent(self) -> GoldEntry:
        if self.status == "answered" and not self.must_include:
            raise ValueError("an answerable question needs at least one must_include fact")
        if self.status == "answered" and not self.sources:
            raise ValueError("an answerable question needs an expected source")
        if self.status == "not_found" and (self.must_include or self.conditions):
            raise ValueError("a not_found question has no facts or conditions to include")
        return self


class GoldFile(_Strict):
    """A whole gold file."""

    version: int = 1
    bot: str
    question_set: Literal["test", "reworded"]
    written_before_first_run: bool | None = None
    questions: dict[str, GoldEntry]

    @field_validator("questions")
    @classmethod
    def _ids(cls, value: dict[str, GoldEntry]) -> dict[str, GoldEntry]:
        for qid in value:
            if not re.fullmatch(r"Q[1-9][0-9]*", qid):
                raise ValueError(f"question id {qid!r} must look like Q1")
        return value


def load_gold(path: Path) -> GoldFile:
    """Read and validate a gold file; raises ValueError with the field name on a bad file."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: a gold file is a YAML mapping")
    # Keys starting with `x-` hold YAML anchors shared by entries; they are not fields.
    data = {key: value for key, value in data.items() if not str(key).startswith("x-")}
    return GoldFile.model_validate(data)


def gold_path_for(set_name: str) -> Path:
    """The gold file of a question set."""
    return DEFAULT_GOLD if set_name == "test" else REWORDED
