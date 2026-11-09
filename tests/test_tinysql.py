"""myTinySQL in Python: the same tests as the C++ and Rust versions, then what only Python has."""

import shutil
from pathlib import Path

import pytest

from little_mcp.tinysql import Database, Session, SqlError
from little_mcp.tinysql.database import empty_catalog
from little_mcp.tinysql.output import fit, render_text
from little_mcp.tinysql.parser import Quoted, Word, parse_statement, split_statements, tokenize
from little_mcp.tinysql.statement import ColumnItem, Literal, Operator, SelectStmt
from little_mcp.tinysql.util import matches_like, parse_integer, trim, trim_right

GOLDEN = Path(__file__).parent / "tinysql_golden"
SCRIPTS = sorted(p.stem for p in GOLDEN.glob("*.sql"))


def test_all_eight_golden_scripts_are_here():
    assert SCRIPTS == ["apostrophes", "create", "delete", "describe", "errors", "insert", "select", "update"]


@pytest.mark.parametrize("name", SCRIPTS)
def test_golden(name, tmp_path):
    """The same scripts and .expected files as C++ and Rust: the output must match byte for byte."""
    data = tmp_path / "data"
    shutil.copytree(GOLDEN / "data", data)
    results = Session(data).execute_script((GOLDEN / f"{name}.sql").read_bytes().decode())
    got = "".join(render_text(r) for r in results)
    assert got == (GOLDEN / f"{name}.expected").read_bytes().decode()


# --- The unit tests of the Rust version --------------------------------------------


def test_trims_only_ascii_whitespace():
    assert trim("  ab c \t\r\n") == "ab c"
    assert trim_right("  ab  ") == "  ab"
    assert trim(" x ") == " x "  # a no-break space stays


@pytest.mark.parametrize("text, pattern, expected", [
    ("Francesco", "%fran%", True),
    ("FRANCO", "%fran%", True),
    ("Luigi", "_uigi", True),
    ("Torino", "tor%", True),
    ("Casella", "%a%a%a%", False),
    ("Banana", "%a%a%a%", True),
    ("", "%", True),
    ("Roma", "Rom", False),
    ("ò", "_", False),  # _ is one byte, and ò takes two
])
def test_like_patterns(text, pattern, expected):
    assert matches_like(text, pattern) is expected


@pytest.mark.parametrize("text, expected", [
    (" 42 ", 42), ("+7", 7), ("-3", -3), ("++7", None), ("4 2", None), ("", None),
    ("99999999999999999999", None), ("٣", None),
])
def test_integers(text, expected):
    assert parse_integer(text) == expected


def test_apostrophes_stay_inside_words():
    assert tokenize("CITTA = L'Aquila")[2] == Word("L'Aquila")
    tokens = tokenize("'Barbera d'Asti' 'it''s'")
    assert tokens[0] == Quoted("Barbera d'Asti")
    assert tokens[1] == Quoted("it's")


def test_parses_a_select():
    stmt = parse_statement("select ID, 'x' from EMPLOY where ID <= 3;")
    assert isinstance(stmt, SelectStmt)
    assert stmt.items == [ColumnItem("ID"), Literal("x")]
    assert stmt.condition.op is Operator.LESS_OR_EQUAL


def test_splits_at_semicolons_outside_quotes():
    script = "REM skip; me\nSELECT 'a;b' FROM T; DESCRIBE\n\n-- and me"
    assert split_statements(script) == ["SELECT 'a;b' FROM T", "DESCRIBE"]


def test_reports_what_it_expected():
    with pytest.raises(SqlError, match="^expected a column name but found end of statement$"):
        parse_statement("SELECT NOME FROM EMPLOY WHERE")


def test_fit_pads_and_cuts():
    assert fit("ID", 5, "-") == "ID---"
    assert fit("Torino", 3, " ") == "Tor"
    assert fit("Città", 4, " ") == "Citt"  # "à" is 2 bytes and would not fit
    assert fit("Città", 5, " ") == "Citt "  # the cut moves back, a space fills in


# --- Widths are bytes ---------------------------------------------------------------


def test_a_curly_apostrophe_counts_three_bytes(tmp_path):
    session = Session(_fresh(tmp_path))
    session.run(parse_statement("CREATE TABLE T (NAME CHAR(10))"))
    assert session.execute("INSERT INTO T VALUES ('D’Annunz')").message == "1 row inserted into T"  # 10 bytes
    failed = session.execute("INSERT INTO T VALUES ('D’Annunzi')")  # 11 bytes in 9 letters
    assert failed.failed and "too long" in failed.message
    assert session.execute("SELECT * FROM T").table.rows == [["D’Annunz"]]


def test_a_line_break_cannot_split_a_row(tmp_path):
    session = Session(_fresh(tmp_path))
    session.run(parse_statement("CREATE TABLE T (NAME CHAR(20))"))
    failed = session.execute("INSERT INTO T VALUES ('two\nlines')")
    assert failed.failed and "line break" in failed.message
    assert session.execute("SELECT * FROM T").table.rows == []


def _fresh(tmp_path) -> Path:
    data = tmp_path / "db"
    data.mkdir()
    (data / "SYSTEM_TABLE").write_bytes(empty_catalog())
    return data


# --- Database: creation, policy, all-or-nothing --------------------------------------


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "db")


def test_a_new_catalog_is_the_same_as_the_original_one():
    original = (GOLDEN / "data" / "SYSTEM_TABLE").read_bytes().split(b"\n")
    assert empty_catalog().split(b"\n")[:9] == original[:9]


def test_read_runs_select_and_describe(db):
    db.write("CREATE TABLE T (ID NUMBER(10), NAME CHAR(10))\nINSERT INTO T VALUES ('Ada')")
    answer = db.read("SELECT NAME FROM T WHERE ID = 1")["results"][0]
    assert answer["columns"] == ["NAME"] and answer["rows"] == [["Ada"]]
    assert ["T", "2", "20"] in db.read("DESCRIBE")["results"][0]["rows"]


@pytest.mark.parametrize("script", [
    "DELETE FROM T WHERE ID = 1",
    "UPDATE T SET NAME = x WHERE ID = 1",
    "INSERT INTO T VALUES ('x')",
    "CREATE TABLE U (A CHAR(1))",
    "SELECT * FROM T; DELETE FROM T WHERE ID = 1",
    "SELECT * FROM T\nDELETE FROM T WHERE ID = 1",
    "COLUMN NAME FORMAT A5",
])
def test_read_refuses_everything_else_before_running_anything(db, script):
    db.write("CREATE TABLE T (ID NUMBER(10), NAME CHAR(10))\nINSERT INTO T VALUES ('Ada')")
    with pytest.raises(SqlError):
        db.read(script)
    assert db.read("SELECT * FROM T")["results"][0]["rows"] == [["1", "Ada"]]


def test_a_write_answers_briefly(db):
    answer = db.write("CREATE TABLE T (ID NUMBER(10), N CHAR(5))\nINSERT INTO T VALUES ('Ada')")
    assert answer == {"done": 2, "messages": ["table T created with 2 columns (row length 15)",
                                              "1 row inserted into T with ID 1"]}


def test_write_refuses_reads(db):
    with pytest.raises(SqlError, match="tiny_sql_read"):
        db.write("DESCRIBE")


@pytest.mark.parametrize("script", [
    "INSERT INTO SYSTEM_TABLE VALUES (99,1,X,Y,S1,0,1,2,2)",
    "insert into system_table values (99,1,X,Y,S1,0,1,2,2)",
    "UPDATE SYSTEM_TABLE SET TYPE = S1 WHERE ID = 1",
])
def test_the_catalog_is_protected(db, script):
    before = (db.directory / "SYSTEM_TABLE").read_bytes()
    with pytest.raises(SqlError, match="cannot be changed"):
        db.write(script)
    assert (db.directory / "SYSTEM_TABLE").read_bytes() == before


def test_a_failed_script_changes_nothing(db):
    db.write("CREATE TABLE T (ID NUMBER(10), NAME CHAR(5))\nINSERT INTO T VALUES ('Ada')")
    with pytest.raises(SqlError, match=r"Statement 3 of 3.*too long.*Nothing was changed"):
        db.write("INSERT INTO T VALUES ('Bob')\nCREATE TABLE U (A CHAR(1))\nINSERT INTO T VALUES ('Too long')")
    assert db.read("SELECT * FROM T")["results"][0]["rows"] == [["1", "Ada"]]
    assert not (db.directory / "U_DATA").exists()
    assert "U" not in [row[0] for row in db.read("DESCRIBE")["results"][0]["rows"]]


def test_a_syntax_error_stops_the_script_before_it_starts(db):
    with pytest.raises(SqlError, match="one whole statement per line"):
        db.write("CREATE TABLE T (ID NUMBER(10))\nINSERT INTO")
    assert not (db.directory / "T_DATA").exists()


def test_a_big_result_is_cut_for_the_model(db):
    db.write("CREATE TABLE T (ID NUMBER(10), N CHAR(1))\n" + "INSERT INTO T VALUES (x)\n" * 201)
    answer = db.read("SELECT * FROM T")["results"][0]
    assert len(answer["rows"]) == 200 and "WHERE" in answer["note"]


def test_two_connections_take_turns(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    first, second = Database(tmp_path / "db"), Database(tmp_path / "db")
    first.write("CREATE TABLE T (ID NUMBER(10), N CHAR(1))")
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda i: (first if i % 2 else second).write("INSERT INTO T VALUES (x)"), range(40)))
    ids = [row[0] for row in first.read("SELECT * FROM T")["results"][0]["rows"]]
    assert sorted(map(int, ids)) == list(range(1, 41))
