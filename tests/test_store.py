import pytest

from little_mcp.store import InventoryError


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
