"""A safe arithmetic evaluator.

It parses the expression with Python's `ast` module and only allows numbers
and arithmetic operators, so input like "__import__('os')" is rejected
instead of executed.
"""

import ast
import operator

_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}

MAX_EXPONENT = 1000
MAX_LENGTH = 200


def evaluate(expression: str) -> float | int:
    """Evaluate an arithmetic expression such as "15 * 3 + 7" or "(2 + 3) ** 2"."""
    expression = expression.strip()
    if not expression:
        raise ValueError("The expression is empty.")
    if len(expression) > MAX_LENGTH:
        raise ValueError(f"The expression is longer than {MAX_LENGTH} characters.")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        raise ValueError(f"Not a valid arithmetic expression: {expression!r}") from None
    result = _eval(tree.body)
    # Hide floating-point noise: 1061.3999999999999 -> 1061.4
    return round(result, 10) if isinstance(result, float) else result


def _eval(node: ast.AST) -> float | int:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_EXPONENT:
            raise ValueError(f"Exponents above {MAX_EXPONENT} are not allowed.")
        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
            raise ValueError("Division by zero is not allowed.")
        return _BINARY[type(node.op)](left, right)
    raise ValueError("Only numbers and + - * / // % ** ( ) are allowed.")
