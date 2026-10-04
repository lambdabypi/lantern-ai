"""Calculator tool: safe arithmetic so the model never does maths itself.

Parses the expression with `ast` and evaluates only numbers, arithmetic operators and
a small set of math functions. No names, attributes or eval.
"""

from __future__ import annotations

import ast
import math
import operator
from typing import Any

from app.tools.registry import Tool, ToolContext

MAX_EXPRESSION_CHARS = 300
MAX_EXPONENT = 1000
MAX_INT_BITS = 4096

_BINARY = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCTIONS = {
    "sqrt": math.sqrt, "abs": abs, "round": round, "min": min, "max": max,
    "floor": math.floor, "ceil": math.ceil, "log": math.log, "log10": math.log10, "exp": math.exp,
}
_CONSTANTS = {"pi": math.pi, "e": math.e}


class CalculatorError(ValueError):
    pass


def evaluate(expression: str) -> int | float:
    """Evaluate an arithmetic expression, e.g. "12 * 7" or "sqrt(2) * 100 / 3"."""
    if not isinstance(expression, str) or not expression.strip():
        raise CalculatorError("Expression must be a non-empty string.")
    if len(expression) > MAX_EXPRESSION_CHARS:
        raise CalculatorError(f"Expression is longer than {MAX_EXPRESSION_CHARS} characters.")
    try:
        tree = ast.parse(expression.replace("^", "**"), mode="eval")
    except SyntaxError as e:
        raise CalculatorError(f"Not a valid arithmetic expression: {e.msg}.") from e
    result = _eval(tree.body)
    if isinstance(result, float):
        if math.isnan(result) or math.isinf(result):
            raise CalculatorError("Result is not a finite number.")
        result = round(result, 12)
    return result


def _eval(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.Name) and node.id in _CONSTANTS:
        return _CONSTANTS[node.id]
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_EXPONENT:
            raise CalculatorError(f"Exponent larger than {MAX_EXPONENT} isn't allowed.")
        try:
            value = _BINARY[type(node.op)](left, right)
        except ZeroDivisionError as e:
            raise CalculatorError("Division by zero.") from e
        except OverflowError as e:
            raise CalculatorError("Result is too large.") from e
        if isinstance(value, int) and value.bit_length() > MAX_INT_BITS:
            raise CalculatorError("Result is too large.")
        return value
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCTIONS
            and not node.keywords):
        args = [_eval(a) for a in node.args]
        try:
            return _FUNCTIONS[node.func.id](*args)
        except (TypeError, ValueError) as e:
            raise CalculatorError(f"{node.func.id}: {e}") from e
    raise CalculatorError(f"Unsupported element: {ast.dump(node)[:60]}")


def _handler(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    expression = args["expression"]
    return {"expression": expression, "result": evaluate(expression)}


CALCULATOR = Tool(
    name="calculator",
    description=(
        "Evaluate an arithmetic expression exactly. Use this for ANY arithmetic, including simple sums, "
        "percentages and unit conversions; never calculate in your head. Supports + - * / // % ** "
        "(or ^), parentheses, pi, e and sqrt, abs, round, min, max, floor, ceil, log, log10, exp. "
        "Example: {\"expression\": \"0.15 * 80\"}."
    ),
    parameters={
        "type": "object",
        "properties": {"expression": {"type": "string", "description": "The arithmetic expression."}},
        "required": ["expression"],
    },
    handler=_handler,
    timeout_s=5.0,
)
