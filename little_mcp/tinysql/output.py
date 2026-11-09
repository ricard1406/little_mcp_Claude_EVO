"""Console text (Rust: output.rs, without the JSON of the browser server)."""

from .session import ResultSet, StatementResult
from .util import trim_right


def fit(text: str, width: int, filler: str) -> str:
    """Cuts or pads text to an exact width, in bytes, as the C++ and Rust versions count.

    A cut never splits a UTF-8 character: it moves back to the start of the
    character, and one more filler is added.
    """
    data = text.encode()
    end = min(len(data), width)
    while end < len(data) and data[end] & 0xC0 == 0x80:  # a byte in the middle of a character
        end -= 1
    return data[:end].decode() + filler * (width - end)


def _header_line(table: ResultSet) -> str:
    """The dashed header line of a console table, e.g. `ID-------- NOME----`."""
    return " ".join(fit(h, w, "-") for h, w in zip(table.headers, table.widths))


def render_text(result: StatementResult) -> str:
    """Formats one result for the console: an echo of the statement, then a table, a message or an error."""
    if result.silent:
        return ""
    out = f"> {result.sql}\n"
    if result.failed:
        return out + f"ERROR: {result.message}\n\n"
    if result.table is None:
        return out + f"{result.message}\n\n"
    out += trim_right(_header_line(result.table)) + "\n"
    for row in result.table.rows:
        out += trim_right(" ".join(fit(v, w, " ") for v, w in zip(row, result.table.widths))) + "\n"
    return out + f"({result.message})\n\n"
