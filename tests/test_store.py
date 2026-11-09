import pytest

from little_mcp.store import Inventory, InventoryError


def test_demo_data_is_seeded(inventory):
    items = {r["item"]: r["quantity"] for r in inventory.list_items("fruits")}
    assert items["ORANGE"] == 3
    assert len(inventory.list_items("VEGGIE")) == 4


def test_find_item_is_case_insensitive_and_partial(inventory):
    assert inventory.find_item("oran") == [{"table": "FRUITS", "item": "ORANGE", "quantity": 3}]
    assert inventory.find_item("kiwi") == []


def test_adjust_and_set(inventory):
    assert inventory.adjust_quantity("FRUITS", "apples", 4) == {
        "table": "FRUITS", "item": "APPLES", "before": 2, "after": 6}
    assert inventory.set_quantity("FRUITS", "APPLES", 1)["after"] == 1
    assert inventory.find_item("APPLES")[0]["quantity"] == 1


def test_cannot_go_negative(inventory):
    with pytest.raises(InventoryError, match="Only 1 BANANA"):
        inventory.adjust_quantity("FRUITS", "BANANA", -2)


def test_unknown_table_and_item(inventory):
    with pytest.raises(InventoryError, match="Unknown table"):
        inventory.list_items("USERS")
    with pytest.raises(InventoryError, match="not in FRUITS"):
        inventory.adjust_quantity("FRUITS", "KIWI", 1)


def test_injection_is_just_a_name(inventory):
    # The item goes in as a query parameter, never into the SQL text.
    assert inventory.find_item("x' OR '1'='1") == []
    with pytest.raises(InventoryError):
        inventory.set_quantity("FRUITS", "APPLES'; DROP TABLE FRUITS; --", 1)
    assert len(inventory.list_items("FRUITS")) == 5


def test_tinysql_is_the_default(monkeypatch, tmp_path):
    import importlib

    from little_mcp import config

    # Your own .env may choose another database, so it is ignored here.
    monkeypatch.delenv("DB_BACKEND", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: None)
    try:
        assert importlib.reload(config).DB_BACKEND == "tinysql"
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_a_stock_change_is_not_lost_between_two_servers(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    one, two = Inventory("tinysql", tinysql_dir=tmp_path / "db"), Inventory("tinysql", tinysql_dir=tmp_path / "db")
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda i: (one if i % 2 else two).adjust_quantity("FRUITS", "APPLES", 1), range(40)))
    assert one.find_item("APPLES")[0]["quantity"] == 42


def test_a_number_too_long_for_its_column_is_a_clear_error(tmp_path):
    inventory = Inventory("tinysql", tinysql_dir=tmp_path / "db")
    with pytest.raises(InventoryError, match="too long"):
        inventory.set_quantity("FRUITS", "APPLES", 10**12)
