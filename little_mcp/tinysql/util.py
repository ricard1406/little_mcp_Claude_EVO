"""The SqlError type and small string helpers used everywhere (Rust: util.rs).

A note on bytes and characters: myTinySQL's files are fixed-width, and widths
are counted in bytes, as in C++ and Rust. `len("è")` is 1 in Python, but "è"
takes 2 bytes on disk, so anything about widths goes through byte_len().
"""

import re

SPACES = " \t\r\n"
_INTEGER = re.compile(r"-?[0-9]+")
_I64_MIN, _I64_MAX = -(2**63), 2**63 - 1


class SqlError(Exception):
    """A problem with one statement: bad syntax, unknown table, value too long...

    Rust returns it in an `Err` and passes it on with `?`. Python raises it, and
    Session.execute() turns it into a failed result.
    """


def trim_right(text: str) -> str:
    """Removes trailing spaces, tabs and line ends (and no other Unicode space)."""
    return text.rstrip(SPACES)


def trim(text: str) -> str:
    """Removes leading and trailing spaces, tabs and line ends."""
    return text.strip(SPACES)


def is_blank(line: bytes) -> bool:
    """Tells whether a line holds only spaces, tabs and line ends (or nothing)."""
    return not line.strip(SPACES.encode())


def text_of(data: bytes) -> str:
    """Turns bytes read from a file into text. Invalid UTF-8 becomes � instead of failing."""
    return data.decode("utf-8", "replace")


def byte_len(text: str) -> int:
    """The number of bytes `text` takes in a data file."""
    return len(text.encode())


def ascii_upper(text: str) -> str:
    """Upper-cases only a-z, like Rust's to_ascii_uppercase. (str.upper() would turn ß into SS.)"""
    return text.encode().upper().decode()


def same_name(a: str, b: str) -> bool:
    """Compares two table or column names, ignoring the case of ASCII letters."""
    return ascii_upper(a) == ascii_upper(b)


def matches_like(text: str, pattern: str) -> bool:
    """Matches text against an SQL LIKE pattern, ignoring the case of ASCII letters.

    % stands for any text (also none) and _ for exactly one byte; every other
    character must match itself. Bytes are compared, so _ does not match an
    accented letter such as ò, which takes 2 bytes in UTF-8; use % there.
    """
    text_bytes, pattern_bytes = text.encode().lower(), pattern.encode().lower()
    t = p = 0
    # Where the last % was, and where in the text it started matching: on a
    # mismatch we come back there and let that % swallow one more byte.
    last_percent = None
    resume_text = 0

    while t < len(text_bytes):
        c = pattern_bytes[p] if p < len(pattern_bytes) else None
        if c == ord("%"):
            last_percent = p
            p += 1
            resume_text = t
        elif c is not None and (c == ord("_") or c == text_bytes[t]):
            p += 1
            t += 1
        elif last_percent is not None:
            p = last_percent + 1
            resume_text += 1
            t = resume_text
        else:
            return False

    while p < len(pattern_bytes) and pattern_bytes[p] == ord("%"):
        p += 1  # a trailing % also matches the empty rest
    return p == len(pattern_bytes)


def parse_integer(text: str) -> int | None:
    """Parses a whole string as a base-10 integer, or returns None.

    Surrounding whitespace and one leading + are allowed. Python's integers
    never overflow, so the range of Rust's i64 is checked by hand: the three
    versions must agree on which values count as numbers.
    """
    text = trim(text).removeprefix("+")
    if not _INTEGER.fullmatch(text):
        return None
    value = int(text)
    return value if _I64_MIN <= value <= _I64_MAX else None
