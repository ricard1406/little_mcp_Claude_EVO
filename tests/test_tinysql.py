"""myTinySQL in Python: the same tests as the C++ and Rust versions, then what only Python has."""

import shutil
from pathlib import Path

import pytest

from little_mcp.tinysql import Database, Session, SqlError
from little_mcp.tinysql.database import empty_catalog, only_inserts
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


@pytest.mark.parametrize("script, expected", [
    ("INSERT INTO T VALUES ('a')", True),
    ("insert into T values ('a');\nINSERT INTO T VALUES ('b; DELETE FROM T WHERE ID = 1')", True),
    ("INSERT INTO T VALUES ('a'); DELETE FROM T WHERE ID = 1", False),
    ("INSERT INTO T VALUES ('a')\nDELETE FROM T WHERE ID = 1", False),
    ("CREATE TABLE T (ID NUMBER(10))", False),
    ("SELECT * FROM T", False),
    ("INSERT INTO", False),
    ("REM nothing here", False),
    ("", False),
])
def test_only_inserts(script, expected):
    assert only_inserts(script) is expected


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


# --- DATE (Python only) ------------------------------------------------------------------

from datetime import datetime, timezone  # noqa: E402

from little_mcp.tinysql.dates import check_date, today  # noqa: E402

# 23:30 in London on 13 November: already the 14th in Rome, still the 13th in New York.
LATE_EVENING_UTC = datetime(2026, 11, 13, 23, 30, tzinfo=timezone.utc)


def test_today_depends_on_the_time_zone():
    assert today("Europe/Rome", LATE_EVENING_UTC) == "2026-11-14"
    assert today("America/New_York", LATE_EVENING_UTC) == "2026-11-13"
    assert today("UTC", LATE_EVENING_UTC) == "2026-11-13"
    with pytest.raises(SqlError, match="unknown time zone 'Mars/Olympus'"):
        today("Mars/Olympus", LATE_EVENING_UTC)


@pytest.mark.parametrize("value, stored", [
    ("2026-11-13", "2026-11-13"), (" 2024-02-29 ", "2024-02-29"), ("", ""), ("today", "2026-11-14"),
])
def test_dates_that_are_accepted(value, stored):
    assert check_date(value, "D", "Europe/Rome", LATE_EVENING_UTC) == stored


@pytest.mark.parametrize("value, message", [
    ("2026-02-30", "not a real day"), ("2025-02-29", "not a real day"), ("2026-13-01", "not a real day"),
    ("13/11/2026", "write YYYY-MM-DD"), ("2026-1-5", "write YYYY-MM-DD"), ("20261113", "write YYYY-MM-DD"),
    ("tomorrow", "write YYYY-MM-DD"),
])
def test_dates_that_are_refused(value, message):
    with pytest.raises(SqlError, match=message):
        check_date(value, "D")


@pytest.fixture
def dated(tmp_path):
    session = Session(_fresh(tmp_path), timezone="Europe/Rome", clock=lambda: LATE_EVENING_UTC)
    session.run(parse_statement("CREATE TABLE TASKS (ID NUMBER(10), WHAT CHAR(20), DUE DATE)"))
    for what, due in [("old", "2026-11-01"), ("now", "TODAY"), ("later", "2026-12-24"), ("someday", "")]:
        assert not session.execute(f"INSERT INTO TASKS VALUES ('{what}', '{due}')").failed
    return session


def rows(session, sql):
    result = session.execute(sql)
    assert not result.failed, result.message
    return result.table.rows


def test_a_date_column_is_ten_bytes_and_today_is_romes(dated):
    assert rows(dated, "DESCRIBE TASKS")[2] == ["DUE", "D1", "30", "39", "10"]
    assert rows(dated, "SELECT WHAT,DUE FROM TASKS WHERE ID = 2") == [["now", "2026-11-14"]]


def test_dates_compare_in_time_order_and_empty_means_no_date(dated):
    assert rows(dated, "SELECT WHAT FROM TASKS WHERE DUE < TODAY") == [["old"]]
    assert rows(dated, "SELECT WHAT FROM TASKS WHERE DUE >= '2026-11-14'") == [["now"], ["later"]]
    assert rows(dated, "SELECT WHAT FROM TASKS WHERE DUE != '2026-11-01'") == [["now"], ["later"]]
    assert rows(dated, "SELECT WHAT FROM TASKS WHERE DUE = ''") == [["someday"]]
    assert rows(dated, "SELECT WHAT FROM TASKS WHERE DUE LIKE '2026-11%'") == [["old"], ["now"]]


def test_a_wrong_date_is_an_error_not_an_empty_answer(dated):
    for sql in ["SELECT * FROM TASKS WHERE DUE = 13/11/2026",
                "INSERT INTO TASKS VALUES ('x', '2026-02-30')",
                "UPDATE TASKS SET DUE = 'next week' WHERE ID = 1",
                "DELETE FROM TASKS WHERE DUE < 'soon'"]:
        result = dated.execute(sql)
        assert result.failed and "DUE" in result.message, sql
    assert len(rows(dated, "SELECT * FROM TASKS")) == 4


def test_update_and_delete_with_dates(dated):
    assert dated.execute("UPDATE TASKS SET DUE = TODAY WHERE WHAT = someday").message == "1 row updated in TASKS"
    assert dated.execute("DELETE FROM TASKS WHERE DUE < TODAY").message == "1 row deleted from TASKS"
    assert rows(dated, "SELECT WHAT FROM TASKS WHERE DUE = TODAY") == [["now"], ["someday"]]


def test_date_has_no_width():
    with pytest.raises(SqlError, match="DATE has no width"):
        parse_statement("CREATE TABLE T (D DATE(10))")


# --- ALTER TABLE (Python only) --------------------------------------------------------------


@pytest.fixture
def books(tmp_path):
    session = Session(_fresh(tmp_path))
    session.execute("CREATE TABLE BOOKS (ID NUMBER(10), TITLE CHAR(20), YEAR NUMBER(4), AUTHOR CHAR(20))")
    session.execute("INSERT INTO BOOKS VALUES ('Pinocchio', '1883', 'Carlo Collodi')")
    session.execute("INSERT INTO BOOKS VALUES ('Cuore', '1886', 'Edmondo De Amicis')")
    return session


def test_a_value_too_long_fits_after_modify(books):
    """What happened on the test machine: YEAR NUMBER(4) cannot hold a period."""
    failed = books.execute("INSERT INTO BOOKS VALUES ('I Promessi Sposi', '1840-1842', 'Alessandro Manzoni')")
    assert failed.failed and "too long for column YEAR" in failed.message
    assert books.execute("ALTER TABLE BOOKS MODIFY YEAR CHAR(20)").message == (
        "column YEAR of BOOKS is now CHAR(20) (row length 70)")
    assert not books.execute("INSERT INTO BOOKS VALUES ('I Promessi Sposi', '1840-1842', 'Alessandro Manzoni')").failed
    assert rows(books, "SELECT * FROM BOOKS") == [
        ["1", "Pinocchio", "1883", "Carlo Collodi"],
        ["2", "Cuore", "1886", "Edmondo De Amicis"],
        ["3", "I Promessi Sposi", "1840-1842", "Alessandro Manzoni"],
    ]
    # The columns after YEAR moved along, in the catalogue too.
    assert rows(books, "DESCRIBE BOOKS") == [
        ["ID", "N1", "0", "9", "10"], ["TITLE", "S1", "10", "29", "20"],
        ["YEAR", "S1", "30", "49", "20"], ["AUTHOR", "S1", "50", "69", "20"]]


def test_add_a_column(books):
    assert books.execute("ALTER TABLE BOOKS ADD COLUMN (READ DATE)").message == (
        "column READ added to BOOKS (row length 64)")
    assert rows(books, "SELECT TITLE,READ FROM BOOKS") == [["Pinocchio", ""], ["Cuore", ""]]
    books.execute("UPDATE BOOKS SET READ = '2026-11-13' WHERE ID = 1")
    assert rows(books, "SELECT TITLE FROM BOOKS WHERE READ = '2026-11-13'") == [["Pinocchio"]]
    assert not books.execute("INSERT INTO BOOKS VALUES ('Malombra', '1881', 'Antonio Fogazzaro', TODAY)").failed
    assert rows(books, "DESCRIBE")[-1] == ["BOOKS", "5", "64"]


def test_narrowing_refuses_a_value_that_would_not_fit(books):
    before = rows(books, "SELECT * FROM BOOKS")
    failed = books.execute("ALTER TABLE BOOKS MODIFY AUTHOR CHAR(14)")
    assert failed.failed and "'Edmondo De Amicis' is too long" in failed.message and "ID 2" in failed.message
    assert rows(books, "SELECT * FROM BOOKS") == before
    assert not books.execute("ALTER TABLE BOOKS MODIFY AUTHOR CHAR(17)").failed
    assert rows(books, "SELECT * FROM BOOKS") == before


def test_changing_to_date_checks_every_value(books):
    failed = books.execute("ALTER TABLE BOOKS MODIFY YEAR DATE")
    assert failed.failed and "'1883' is not a date" in failed.message


@pytest.mark.parametrize("sql, message", [
    ("ALTER TABLE BOOKS ADD TITLE CHAR(5)", "appears twice"),
    ("ALTER TABLE BOOKS ADD ID NUMBER(10)", "appears twice"),
    ("ALTER TABLE BOOKS ADD 9LIVES CHAR(5)", "invalid column name"),
    ("ALTER TABLE BOOKS MODIFY PAGES NUMBER(5)", "unknown column 'PAGES'"),
    ("ALTER TABLE BOOKS ADD BIG CHAR(99999)", "the limit is 99999"),
    ("ALTER TABLE SYSTEM_TABLE ADD X CHAR(1)", "SYSTEM_TABLE cannot be changed"),
    ("ALTER TABLE NOPE ADD X CHAR(1)", "unknown table 'NOPE'"),
    ("ALTER TABLE BOOKS DROP COLUMN YEAR", "cannot DROP yet"),
    ("ALTER TABLE BOOKS RENAME TO B", "cannot RENAME yet"),
    ("ALTER TABLE BOOKS", "expected ADD or MODIFY"),
    ("ALTER TABLE BOOKS ADD (X CHAR(1)", "expected ')'"),
])
def test_alter_errors_change_nothing(books, sql, message):
    before = {p.name: p.read_bytes() for p in books.data_dir.iterdir()}
    result = books.execute(sql)
    assert result.failed and message in result.message, result.message
    assert {p.name: p.read_bytes() for p in books.data_dir.iterdir()} == before


def test_alter_is_a_change_for_the_tools(db):
    db.write("CREATE TABLE T (ID NUMBER(10), N CHAR(5))")
    with pytest.raises(SqlError, match="does not run ALTER TABLE"):
        db.read("ALTER TABLE T ADD X CHAR(1)")
    with pytest.raises(SqlError, match="does not run CREATE TABLE"):
        db.read("CREATE TABLE U (A CHAR(1))")
    assert db.write("ALTER TABLE T MODIFY N CHAR(9)")["messages"] == ["column N of T is now CHAR(9) (row length 19)"]
    assert only_inserts("ALTER TABLE T ADD X CHAR(1)") is False


def test_an_id_added_later_would_not_be_first(tmp_path):
    session = Session(_fresh(tmp_path))
    session.execute("CREATE TABLE T (NAME CHAR(5))")
    result = session.execute("ALTER TABLE T ADD ID NUMBER(10)")
    assert result.failed and "must be the first" in result.message
