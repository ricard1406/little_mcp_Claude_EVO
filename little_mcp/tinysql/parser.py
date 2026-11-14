"""The tokenizer and recursive-descent parser for the SQL dialect (Rust: parser.rs).

The grammar:

    SELECT <item>[,<item>...] FROM <table> [WHERE <col> <op> <value>]
    INSERT INTO <table> VALUES (<value>[,<value>...])
    UPDATE <table> SET <col> = <value>[,<col> = <value>...] WHERE <col> <op> <value>
    DELETE FROM <table> WHERE <col> <op> <value>
    CREATE TABLE <table> (<col> <type>[,...])
    ALTER TABLE <table> ADD <col> <type>                      (Python only)
    ALTER TABLE <table> MODIFY <col> <type>                   (Python only)
    COLUMN <col> FORMAT A<width>
    DESCRIBE [<table>]
    REM <comment>    -- <comment>

<type> is CHAR(<width>), NUMBER(<width>) or DATE (Python only).
<op> is one of = != <> < <= > >= or LIKE (with % and _ wildcards). Keywords are
case-insensitive. A trailing ; or @ is ignored.

A ' between two letters or digits is an apostrophe, part of the word, as in
d'Asti or L'Aquila: it never starts or ends a quoted string. '' inside quotes
still stands for one quote.
"""

import re
from dataclasses import dataclass
from typing import NoReturn

from .dates import DATE_KIND, DATE_WIDTH
from .statement import (
    AddColumnStmt,
    Assignment,
    ColumnDefinition,
    ColumnFormatStmt,
    ColumnItem,
    Comment,
    Condition,
    CreateTableStmt,
    DeleteStmt,
    DescribeStmt,
    InsertStmt,
    Literal,
    ModifyColumnStmt,
    Operator,
    SelectItem,
    SelectStmt,
    Star,
    Statement,
    UpdateStmt,
)
from .util import SqlError, ascii_upper, parse_integer, same_name, trim


@dataclass(frozen=True)
class Word:
    """A keyword, name or bare value, e.g. SELECT, EMPLOY, Torino, 42."""

    text: str

    def describe(self) -> str:
        return f'"{self.text}"'


@dataclass(frozen=True)
class Quoted:
    """A 'quoted string', without its quotes."""

    text: str

    def describe(self) -> str:
        return f"'{self.text}'"


@dataclass(frozen=True)
class Symbol:
    """Punctuation or an operator: ( ) , * ; @ = != <> < <= > >=."""

    text: str

    def describe(self) -> str:
        return f'"{self.text}"'


@dataclass(frozen=True)
class End:
    """The end of the statement."""

    text: str = ""

    def describe(self) -> str:
        return "end of statement"


Token = Word | Quoted | Symbol | End

_DELIMITERS = " \t\r\n(),*;@=<>!'"
_COMPARISONS = {
    "=": Operator.EQUAL,
    "!=": Operator.NOT_EQUAL,
    "<>": Operator.NOT_EQUAL,
    "<": Operator.LESS,
    "<=": Operator.LESS_OR_EQUAL,
    ">": Operator.GREATER,
    ">=": Operator.GREATER_OR_EQUAL,
}


def _is_word_char(c: str) -> bool:
    """A character that can stand next to an apostrophe: an ASCII letter or digit, or any non-ASCII letter."""
    return c.isalnum() if c.isascii() else True


def _is_apostrophe(text: str, i: int) -> bool:
    """Tells whether the ' at position i is an apostrophe inside a word, as in d'Asti or dell'800."""
    return 0 < i < len(text) - 1 and text[i] == "'" and _is_word_char(text[i - 1]) and _is_word_char(text[i + 1])


def tokenize(sql: str) -> list[Token]:
    """Splits a statement into tokens, always ending with one End. Fails on an unterminated string or a lone !."""
    tokens: list[Token] = []
    i = 0
    while i < len(sql):
        c = sql[i]
        if c in " \t\r\n":
            i += 1
        elif c == "'":
            # 'quoted string', with '' standing for one quote; an apostrophe (d'Asti) does not end it
            text: list[str] = []
            after_word = i > 0 and _is_word_char(sql[i - 1])  # e.g. Foscari' in 'Ca' Foscari'
            i += 1
            while True:
                if i >= len(sql):
                    raise SqlError(
                        "unterminated quoted string (an apostrophe at the end of a word, "
                        "as in Ca' Foscari, is written twice inside quotes: 'Ca'' Foscari')"
                        if after_word
                        else "unterminated quoted string"
                    )
                if sql[i] == "'":
                    if sql[i + 1 : i + 2] == "'":
                        text.append("'")
                        i += 2
                        continue
                    if not _is_apostrophe(sql, i):
                        i += 1
                        break
                text.append(sql[i])
                i += 1
            tokens.append(Quoted("".join(text)))
        elif c in "(),*;@":
            tokens.append(Symbol(c))
            i += 1
        elif c in "=<>!":
            if sql[i : i + 2] in ("<=", ">=", "!=", "<>"):
                tokens.append(Symbol(sql[i : i + 2]))
                i += 2
            elif c == "!":
                raise SqlError("unexpected '!'")
            else:
                tokens.append(Symbol(c))
                i += 1
        else:
            # A word runs to the next delimiter; an apostrophe inside it (L'Aquila) is part of the word.
            stop = i
            while stop < len(sql) and (sql[stop] not in _DELIMITERS or _is_apostrophe(sql, stop)):
                stop += 1
            tokens.append(Word(sql[i:stop]))
            i = stop
    tokens.append(End())
    return tokens


class Parser:
    """A recursive-descent parser: one method per statement type, reading the tokens left to right."""

    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos = 0

    def parse(self) -> Statement:
        """Parses the whole statement, choosing the method from its first word."""
        token = self.peek()
        if not isinstance(token, Word):
            self.fail("a command")
        methods = {
            "SELECT": self.parse_select,
            "INSERT": self.parse_insert,
            "UPDATE": self.parse_update,
            "DELETE": self.parse_delete,
            "CREATE": self.parse_create_table,
            "ALTER": self.parse_alter_table,
            "COLUMN": self.parse_column,
            "DESCRIBE": self.parse_describe,
        }
        method = methods.get(ascii_upper(token.text))
        if method is None:
            raise SqlError(f'unknown command "{token.text}"')
        return method()

    # --- Reading tokens -----------------------------------------------------------

    def peek(self) -> Token:
        return self.tokens[self.pos]

    def peek_word(self) -> str | None:
        """The next token's text if it is a word, without consuming it."""
        token = self.peek()
        return token.text if isinstance(token, Word) else None

    def next_text(self) -> str:
        """Consumes the next token and returns its text (the End token is never consumed)."""
        token = self.peek()
        if not isinstance(token, End):
            self.pos += 1
        return token.text

    def fail(self, expected: str) -> NoReturn:
        """Reports a syntax error at the current token: "expected ... but found ..."."""
        raise SqlError(f"expected {expected} but found {self.peek().describe()}")

    def accept_keyword(self, keyword: str) -> bool:
        word = self.peek_word()
        if word is not None and same_name(word, keyword):
            self.pos += 1
            return True
        return False

    def expect_keyword(self, keyword: str) -> None:
        if not self.accept_keyword(keyword):
            self.fail(keyword)

    def accept_symbol(self, symbol: str) -> bool:
        if self.peek() == Symbol(symbol):
            self.pos += 1
            return True
        return False

    def expect_symbol(self, symbol: str) -> None:
        if not self.accept_symbol(symbol):
            self.fail(f"'{symbol}'")

    def expect_word(self, what: str) -> str:
        """Consumes a word (a name) that must come next; `what` is for the error message."""
        if not isinstance(self.peek(), Word):
            self.fail(what)
        return self.next_text()

    def expect_value(self) -> str:
        """Consumes a value that must come next: a word or a quoted string."""
        if not isinstance(self.peek(), (Word, Quoted)):
            self.fail("a value")
        return self.next_text()

    def expect_end(self) -> None:
        """Checks that the statement ends here, after any trailing ; or @."""
        while self.accept_symbol(";") or self.accept_symbol("@"):
            pass
        if not isinstance(self.peek(), End):
            self.fail("end of statement")

    # --- One method per statement -----------------------------------------------------

    def parse_condition(self) -> Condition:
        """Parses <column> <op> <value>, after the WHERE keyword; <op> is a comparison or LIKE."""
        column = self.expect_word("a column name")
        if self.accept_keyword("LIKE"):
            op = Operator.LIKE
        else:
            token = self.peek()
            op = _COMPARISONS.get(token.text) if isinstance(token, Symbol) else None
            if op is None:
                self.fail("a comparison (= != < <= > >= or LIKE)")
            self.pos += 1
        value = self.expect_value()

        word = self.peek_word()
        if word is not None:
            if same_name(word, "AND") or same_name(word, "OR"):
                raise SqlError(f"WHERE takes one condition: {ascii_upper(word)} is not supported")
            words = value
            while (word := self.peek_word()) is not None:
                words += " " + word
                self.pos += 1
            raise SqlError(f"put a value with spaces in quotes, e.g. {column} = '{words}'")
        return Condition(column, op, value)

    def parse_select(self) -> SelectStmt:
        self.expect_keyword("SELECT")
        items: list[SelectItem] = []
        while True:
            if self.accept_symbol("*"):
                items.append(Star())
            elif isinstance(self.peek(), Quoted):
                items.append(Literal(self.next_text()))
            else:
                items.append(ColumnItem(self.expect_word("a column name")))
            if not self.accept_symbol(","):
                break

        self.expect_keyword("FROM")
        table = self.expect_word("a table name")
        condition = self.parse_condition() if self.accept_keyword("WHERE") else None
        self.expect_end()
        return SelectStmt(items, table, condition)

    def parse_insert(self) -> InsertStmt:
        self.expect_keyword("INSERT")
        self.expect_keyword("INTO")
        table = self.expect_word("a table name")
        self.expect_keyword("VALUES")
        self.expect_symbol("(")
        values: list[str] = []
        while True:
            # A value is a 'quoted string', or one or more bare words joined by single spaces.
            if isinstance(self.peek(), Quoted):
                values.append(self.next_text())
            else:
                words: list[str] = []
                while (word := self.peek_word()) is not None:
                    words.append(word)
                    self.pos += 1
                values.append(" ".join(words))
            if not self.accept_symbol(","):
                break
        self.expect_symbol(")")
        self.expect_end()
        return InsertStmt(table, values)

    def parse_update(self) -> UpdateStmt:
        self.expect_keyword("UPDATE")
        table = self.expect_word("a table name")
        self.expect_keyword("SET")
        assignments: list[Assignment] = []
        while True:
            column = self.expect_word("a column name")
            self.expect_symbol("=")
            value = self.expect_value()
            word = self.peek_word()
            if word is not None and not same_name(word, "WHERE"):
                raise SqlError(f"put a value with spaces in quotes, e.g. {column} = '{value} {word}'")
            assignments.append(Assignment(column, value))
            if not self.accept_symbol(","):
                break
        if not self.accept_keyword("WHERE"):
            self.fail("WHERE (UPDATE always needs a condition, e.g. WHERE ID = 3)")
        condition = self.parse_condition()
        self.expect_end()
        return UpdateStmt(table, assignments, condition)

    def parse_delete(self) -> DeleteStmt:
        self.expect_keyword("DELETE")
        self.expect_keyword("FROM")
        table = self.expect_word("a table name")
        if not self.accept_keyword("WHERE"):
            self.fail("WHERE (DELETE always needs a condition, e.g. WHERE ID = 3)")
        condition = self.parse_condition()
        self.expect_end()
        return DeleteStmt(table, condition)

    def parse_create_table(self) -> CreateTableStmt:
        self.expect_keyword("CREATE")
        self.expect_keyword("TABLE")
        table = self.expect_word("a table name")
        self.expect_symbol("(")
        columns: list[ColumnDefinition] = []
        while True:
            columns.append(self.parse_column_definition())
            if not self.accept_symbol(","):
                break
        self.expect_symbol(")")
        self.expect_end()
        return CreateTableStmt(table, columns)

    def parse_column_definition(self) -> ColumnDefinition:
        """Parses `<col> CHAR(<width>)`, `<col> NUMBER(<width>)` or `<col> DATE`."""
        name = self.expect_word("a column name")
        kind = ascii_upper(self.expect_word("a type (CHAR, NUMBER or DATE)"))
        if kind == "DATE":
            if self.peek() == Symbol("("):
                raise SqlError(f"DATE has no width (it is always YYYY-MM-DD): write {name} DATE")
            return ColumnDefinition(name, DATE_KIND, DATE_WIDTH)
        if kind not in ("CHAR", "NUMBER"):
            # The C++ and Rust versions say "use CHAR(<width>) or NUMBER(<width>)": they have no DATE.
            raise SqlError(f'unknown type "{kind}" for column {name}: use CHAR(<width>), NUMBER(<width>) or DATE')
        self.expect_symbol("(")
        width_text = self.expect_word("a width")
        width = parse_integer(width_text)
        if width is None or width < 1:
            raise SqlError(f'invalid width "{width_text}" for column {name}: expected a positive number')
        if self.peek() == Symbol(","):  # NUMBER(10,2), as in Oracle (Python only: a clearer message)
            raise SqlError(
                f"{kind} takes one width, e.g. {name} {kind}({width}): a NUMBER holds decimals like "
                "1338.80 without a fixed number of decimal places, and the width counts every character"
            )
        self.expect_symbol(")")
        return ColumnDefinition(name, "S1" if kind == "CHAR" else "N1", width)

    def parse_alter_table(self) -> AddColumnStmt | ModifyColumnStmt:
        """Parses `ALTER TABLE <table> ADD|MODIFY [COLUMN] <col> <type>`, with optional ( ) as in Oracle."""
        self.expect_keyword("ALTER")
        self.expect_keyword("TABLE")
        table = self.expect_word("a table name")
        if self.accept_keyword("ADD"):
            statement = AddColumnStmt
        elif self.accept_keyword("MODIFY"):
            statement = ModifyColumnStmt
        else:
            word = self.peek_word()
            if word is not None and (same_name(word, "DROP") or same_name(word, "RENAME")):
                raise SqlError(f"ALTER TABLE cannot {ascii_upper(word)} yet: it can ADD a column or MODIFY one")
            self.fail("ADD or MODIFY")
        self.accept_keyword("COLUMN")
        in_brackets = self.accept_symbol("(")
        column = self.parse_column_definition()
        if in_brackets:
            self.expect_symbol(")")
        self.expect_end()
        return statement(table, column)

    def parse_column(self) -> ColumnFormatStmt:
        self.expect_keyword("COLUMN")
        column = self.expect_word("a column name")
        self.expect_keyword("FORMAT")
        fmt = self.expect_word("a format such as A16")
        width = parse_integer(fmt[1:]) if fmt[:1] in ("A", "a") else None
        if width is None or width < 1:
            raise SqlError(f'invalid format "{fmt}", expected A<width> such as A16')
        self.expect_end()
        return ColumnFormatStmt(column, width)

    def parse_describe(self) -> DescribeStmt:
        self.expect_keyword("DESCRIBE")
        table = self.next_text() if self.peek_word() is not None else None
        self.expect_end()
        return DescribeStmt(table)


def _is_comment(sql: str) -> bool:
    """Tells whether a line is a comment or empty: an empty line, -- ..., or REM ...."""
    sql = trim(sql)
    if not sql or sql.startswith("--"):
        return True
    return same_name(re.split(r"[ \t]", sql, maxsplit=1)[0], "REM")


def parse_statement(sql: str) -> Statement:
    """Parses one statement; a trailing ; is allowed. Raises SqlError on a syntax error."""
    if _is_comment(sql):
        return Comment()
    return Parser(tokenize(sql)).parse()


def split_statements(script: str) -> list[str]:
    """Splits a script into statements: one per line, and ; outside quotes also ends one.

    Returns the statements in order, trimmed; comment lines and empty pieces are left out.
    """
    statements: list[str] = []

    def add(piece: str) -> None:
        piece = trim(piece)
        if piece:
            statements.append(piece)

    for line in script.split("\n"):
        if _is_comment(line):
            continue
        in_quotes = False
        start = 0
        for i, c in enumerate(line):
            if c == "'" and not _is_apostrophe(line, i):
                in_quotes = not in_quotes
            if c == ";" and not in_quotes:
                add(line[start:i])
                start = i + 1
        add(line[start:])
    return statements
