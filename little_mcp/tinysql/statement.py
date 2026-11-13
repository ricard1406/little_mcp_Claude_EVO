"""The parsed form of every statement (Rust: statement.rs).

C++ uses a std::variant of structs and Rust an enum. Python uses one dataclass
per statement, and Session.run() picks the right method with a `match`.
"""

from dataclasses import dataclass
from enum import Enum


# --- One entry of a SELECT list -------------------------------------------------


@dataclass(frozen=True)
class Star:
    """`*`: every column of the table."""


@dataclass(frozen=True)
class ColumnItem:
    """A column name."""

    name: str


@dataclass(frozen=True)
class Literal:
    """A 'quoted text', printed as-is on every row."""

    text: str


SelectItem = Star | ColumnItem | Literal


# --- WHERE ------------------------------------------------------------------------


class Operator(Enum):
    """The operator of a WHERE test."""

    EQUAL = "="
    NOT_EQUAL = "!="
    LESS = "<"
    LESS_OR_EQUAL = "<="
    GREATER = ">"
    GREATER_OR_EQUAL = ">="
    LIKE = "LIKE"


@dataclass(frozen=True)
class Condition:
    """A `WHERE <column> <op> <value>` test."""

    column: str
    op: Operator
    value: str


# --- The statements ---------------------------------------------------------------


@dataclass(frozen=True)
class SelectStmt:
    """`SELECT <items> FROM <table> [WHERE <condition>]`."""

    items: list[SelectItem]
    table: str
    condition: Condition | None = None


@dataclass(frozen=True)
class InsertStmt:
    """`INSERT INTO <table> VALUES (<values>)`; the ID may be left out."""

    table: str
    values: list[str]


@dataclass(frozen=True)
class DeleteStmt:
    """`DELETE FROM <table> WHERE <condition>`. The WHERE is required."""

    table: str
    condition: Condition


@dataclass(frozen=True)
class Assignment:
    """One `<column> = <value>` of an UPDATE's SET list."""

    column: str
    value: str


@dataclass(frozen=True)
class UpdateStmt:
    """`UPDATE <table> SET <col> = <value>[, ...] WHERE <condition>`. The WHERE is required."""

    table: str
    assignments: list[Assignment]
    condition: Condition


@dataclass(frozen=True)
class ColumnFormatStmt:
    """`COLUMN <name> FORMAT A<width>`: the console display width of a column."""

    column: str
    width: int


@dataclass(frozen=True)
class ColumnDefinition:
    """One column of a CREATE TABLE or ALTER TABLE. `kind` is S1 for CHAR, N1 for NUMBER, D1 for DATE."""

    name: str
    kind: str
    width: int


@dataclass(frozen=True)
class CreateTableStmt:
    """`CREATE TABLE <table> (<column> <type>(<width>)[, ...])`."""

    table: str
    columns: list[ColumnDefinition]


@dataclass(frozen=True)
class AddColumnStmt:
    """`ALTER TABLE <table> ADD <column> <type>`: a new column at the end of every row. (Python only.)"""

    table: str
    column: ColumnDefinition


@dataclass(frozen=True)
class ModifyColumnStmt:
    """`ALTER TABLE <table> MODIFY <column> <type>`: a new width or type for a column. (Python only.)"""

    table: str
    column: ColumnDefinition


@dataclass(frozen=True)
class DescribeStmt:
    """`DESCRIBE` lists the tables; `DESCRIBE <table>` lists that table's columns."""

    table: str | None = None


@dataclass(frozen=True)
class Comment:
    """`REM ...`, `-- ...`, or an empty line: nothing to run."""


Statement = (
    SelectStmt
    | InsertStmt
    | UpdateStmt
    | DeleteStmt
    | CreateTableStmt
    | AddColumnStmt
    | ModifyColumnStmt
    | ColumnFormatStmt
    | DescribeStmt
    | Comment
)
