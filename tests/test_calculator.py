import pytest

from little_mcp.calculator import evaluate


@pytest.mark.parametrize("expr, expected", [
    ("15 * 3 + 7", 52),
    ("(2 + 3) ** 2", 25),
    ("-4 + 10 / 4", -1.5),
    ("7 // 2", 3),
    ("7 % 4", 3),
    ("1.5e3 - 500", 1000.0),
])
def test_arithmetic(expr, expected):
    assert evaluate(expr) == expected


@pytest.mark.parametrize("expr", [
    "__import__('os').system('echo hi')",
    "abs(-3)",
    "x + 1",
    "'a' * 3",
    "[1, 2]",
    "2 ** 100000",
    "1 / 0",
    "",
    "1 +",
])
def test_rejects_unsafe_or_invalid(expr):
    with pytest.raises(ValueError):
        evaluate(expr)
