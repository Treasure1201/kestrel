"""Safe decimal arithmetic for the `calculate` tool and for recomputing reported calculations.

Accepted: numbers (with digit grouping such as `50 000` or `50,000`, and an optional
`R` currency prefix), `+ - * /`, parentheses, percentages (`75%` is 0.75, `75% of
50000` is `0.75 * 50000`), and the functions `min`, `max` and `round`. Nothing else:
no names, attributes, subscripts, powers, comparisons or other calls.

Inside the parentheses of `min`, `max` and `round` a comma always separates the
arguments (`min(100,200)` is 100), so digit grouping there must use spaces
(`max(1 000, 2 000)`). A grouped value written with commas inside a call leaves a
part such as `000`, which is refused with that advice rather than read as zero.

Arithmetic uses `Decimal` with 28 significant digits, so `0.1 + 0.2` is exactly
`0.3`. Only the final result is rounded: to two decimal places, half away from zero
(`ROUND_HALF_UP`), with trailing zeros removed. `round(x, n)` uses the same rule.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Context, Decimal, DecimalException, InvalidOperation, localcontext

MAX_EXPRESSION_CHARS = 300
MAX_NODES = 120
MAX_MAGNITUDE = Decimal("1e15")
RESULT_PLACES = 2
_PRECISION = 28

_GROUPED = re.compile(r"(?<=\d)[ ,    '](?=\d{3}(?!\d))")
_CURRENCY = re.compile(r"(?<![A-Za-z_])(?:ZAR|R)\s?(?=\d)")
_FUNCTION_OPEN = re.compile(r"\b(?:min|max|round)\s*\($")
_ARGUMENT_COMMA = "\x00"
_TIMES = re.compile(r"(?<=[\d)])\s*[xX×]\s*(?=[\d(])")
_PERCENT_OF = re.compile(r"%\s*of\b", re.IGNORECASE)
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


class CalculationError(ValueError):
    """The expression is not allowed or cannot be evaluated. The message says why."""


@dataclass(frozen=True)
class Calculation:
    """A successful evaluation: the rounded result plus every operand and intermediate value."""

    expression: str
    value: Decimal
    """The result rounded to `RESULT_PLACES` (half up)."""
    exact: Decimal
    """The result before final rounding."""
    operands: tuple[Decimal, ...] = field(default=())
    """Every number literal in the expression, percentages already divided by 100."""
    steps: tuple[Decimal, ...] = field(default=())
    """Every intermediate result."""

    @property
    def text(self) -> str:
        """The result as returned to the model, for example `37500` or `1234.57`."""
        return format_decimal(self.value)


def format_decimal(value: Decimal) -> str:
    """Plain notation without exponent or trailing zeros: `37500`, `0.75`, `-3.5`."""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


def _protect_argument_commas(text: str) -> str:
    """Mark every comma inside a min/max/round argument list, so digit-group removal cannot take it."""
    stack: list[bool] = []
    out: list[str] = []
    for char in text:
        if char == "(":
            stack.append(bool(_FUNCTION_OPEN.search("".join(out) + char)) or any(stack))
        elif char == ")" and stack:
            stack.pop()
        out.append(_ARGUMENT_COMMA if char == "," and stack and stack[-1] else char)
    return "".join(out)


def normalise_expression(expression: str) -> str:
    """Rewrite human notation (`R1 000`, `1,000`, `75%`, `3 x 4`, `75% of`) into plain arithmetic."""
    text = expression.replace("−", "-").replace("÷", "/")
    text = _CURRENCY.sub("", text)
    text = _protect_argument_commas(text)
    previous = None
    while previous != text:
        previous = text
        text = _GROUPED.sub("", text)
    text = text.replace(_ARGUMENT_COMMA, ",")
    text = _TIMES.sub(" * ", text)
    text = _PERCENT_OF.sub("% *", text)
    return _PERCENT.sub(lambda m: f"({m.group(1)} / 100)", text)


def _round(value: Decimal, places: int) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


class _Evaluator:
    def __init__(self, source: str) -> None:
        self.source = source
        self.operands: list[Decimal] = []
        self.steps: list[Decimal] = []
        self.percent_spans: set[tuple[int, int]] = set()

    def _check(self, value: Decimal) -> Decimal:
        if not value.is_finite() or abs(value) > MAX_MAGNITUDE:
            raise CalculationError(f"a value is outside the calculator's range (at most {format_decimal(MAX_MAGNITUDE)}).")
        return value

    def _step(self, value: Decimal) -> Decimal:
        self.steps.append(self._check(value))
        return value

    def evaluate(self, node: ast.AST) -> Decimal:
        if isinstance(node, ast.Expression):
            return self.evaluate(node.body)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            segment = ast.get_source_segment(self.source, node) or repr(node.value)
            if re.match(r"0\d", segment):
                raise CalculationError(
                    f"'{segment}' is not a number. Inside min, max and round a comma separates the values, so write "
                    "thousands with spaces (25 000) or without grouping (25000)."
                )
            value = self._check(Decimal(segment))
            self.operands.append(value)
            return value
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            return self._binary(node)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            value = self.evaluate(node.operand)
            return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.Call):
            return self._call(node)
        raise CalculationError(f"'{_describe(node)}' is not allowed. Use numbers, + - * /, parentheses, %, min, max and round.")

    def _binary(self, node: ast.BinOp) -> Decimal:
        left, right = self.evaluate(node.left), self.evaluate(node.right)
        if isinstance(node.op, ast.Div) and isinstance(node.right, ast.Constant) and right == 100 and _is_percent(node):
            # `75%` becomes `(75 / 100)`: record the rate itself as the operand, as it is written in documents.
            self.operands[-2:] = [left, left / 100]
        if isinstance(node.op, ast.Add):
            return self._step(left + right)
        if isinstance(node.op, ast.Sub):
            return self._step(left - right)
        if isinstance(node.op, ast.Mult):
            return self._step(left * right)
        if right == 0:
            raise CalculationError("division by zero.")
        return self._step(left / right)

    def _call(self, node: ast.Call) -> Decimal:
        name = node.func.id if isinstance(node.func, ast.Name) else None
        if name not in {"min", "max", "round"} or node.keywords:
            raise CalculationError(f"'{_describe(node.func)}' is not an allowed function. Only min, max and round are.")
        if name == "round":
            if not 1 <= len(node.args) <= 2:
                raise CalculationError("round takes a value and optionally a number of decimal places.")
            value = self.evaluate(node.args[0])
            places = Decimal(0)
            if len(node.args) == 2:
                count = len(self.operands)
                places = self.evaluate(node.args[1])
                del self.operands[count:]  # decimal places are a format choice, not a sourced number
            if places != places.to_integral_value() or not 0 <= places <= 10:
                raise CalculationError("round's decimal places must be a whole number from 0 to 10.")
            return self._step(_round(value, int(places)))
        if not node.args:
            raise CalculationError(f"{name} needs at least one value.")
        values = [self.evaluate(arg) for arg in node.args]
        return self._step(min(values) if name == "min" else max(values))


def _is_percent(node: ast.BinOp) -> bool:
    return isinstance(node.left, ast.Constant) and isinstance(node.right, ast.Constant) and node.right.value == 100


def _describe(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"attribute .{node.attr}"
    if isinstance(node, ast.BinOp):
        return type(node.op).__name__.lower()
    return type(node).__name__.lower()


def evaluate(expression: str) -> Calculation:
    """Evaluate one expression safely.

    Raises:
        CalculationError: the expression is empty, too long, uses anything outside the
            allowed grammar, divides by zero or leaves the allowed range.
    """
    if not isinstance(expression, str) or not expression.strip():
        raise CalculationError("the expression is empty.")
    if len(expression) > MAX_EXPRESSION_CHARS:
        raise CalculationError(f"the expression is longer than {MAX_EXPRESSION_CHARS} characters.")
    source = normalise_expression(expression.strip())
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise CalculationError(f"the expression is not valid arithmetic ({exc.msg}).") from None
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        raise CalculationError("the expression is too complex; split it into steps.")
    evaluator = _Evaluator(source)
    with localcontext(Context(prec=_PRECISION, rounding=ROUND_HALF_UP)):
        try:
            exact = evaluator.evaluate(tree)
            value = _round(exact, RESULT_PLACES)
        except CalculationError:
            raise
        except (DecimalException, InvalidOperation, ValueError, OverflowError, RecursionError) as exc:
            raise CalculationError(f"the expression could not be evaluated ({type(exc).__name__}).") from None
    return Calculation(
        expression=expression.strip(),
        value=value,
        exact=exact,
        operands=tuple(evaluator.operands),
        steps=tuple(evaluator.steps),
    )
