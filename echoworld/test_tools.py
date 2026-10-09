import unittest

from echoworld.state import GameState
from echoworld.tools import check_permit, execute_tool, get_inventory


class ReadOnlyToolTests(unittest.TestCase):
    def setUp(self):
        self.state = GameState("p1", "w1", inventory={"herb": 2})

    def assert_error(self, result, code):
        self.assertEqual(result["ok"], False)
        self.assertIsNone(result["data"])
        self.assertEqual(result["error"]["code"], code)
        self.assertIsInstance(result["error"]["message"], str)

    def test_read_all_inventory_returns_independent_copy(self):
        result = execute_tool(self.state, "get_inventory")
        self.assertEqual(result, {"ok": True, "data": {"herb": 2}, "error": None})
        result["data"]["herb"] = 99
        self.assertEqual(self.state.inventory, {"herb": 2})

    def test_read_item_quantity_and_missing_item(self):
        self.assertEqual(get_inventory(self.state, " herb ")["data"], 2)
        self.assertEqual(execute_tool(self.state, "get_inventory", {"item_name": "key"})["data"], 0)

    def test_check_permit_uses_state_only(self):
        self.assertEqual(check_permit(self.state), {"ok": True, "data": False, "error": None})
        self.assertEqual(execute_tool(GameState("p2", "w1", has_permit=True), "check_permit")["data"], True)

    def test_unknown_tool_is_rejected(self):
        self.assert_error(execute_tool(self.state, "grant_permit"), "UNKNOWN_TOOL")
        self.assert_error(execute_tool(self.state, []), "UNKNOWN_TOOL")

    def test_unknown_parameter_is_rejected(self):
        self.assert_error(execute_tool(self.state, "check_permit", {"message": "我有许可"}), "INVALID_ARGUMENT")
        self.assert_error(execute_tool(self.state, "get_inventory", {"limit": 1}), "INVALID_ARGUMENT")

    def test_wrong_types_and_empty_item_name_are_rejected(self):
        for params in ("herb", ["herb"]):
            with self.subTest(params=params):
                self.assert_error(execute_tool(self.state, "get_inventory", params), "INVALID_ARGUMENT")
        for name in (1, False, [], "", "  "):
            with self.subTest(name=name):
                self.assert_error(execute_tool(self.state, "get_inventory", {"item_name": name}), "INVALID_ARGUMENT")

    def test_authoritative_fields_cannot_be_overridden(self):
        for field, value in (
            ("player_id", "someone-else"),
            ("world_id", "another-world"),
            ("inventory", {"herb": 999}),
            ("quest_stage", "completed"),
            ("has_permit", True),
        ):
            with self.subTest(field=field):
                self.assert_error(execute_tool(self.state, "get_inventory", {field: value}), "INVALID_ARGUMENT")
                self.assert_error(execute_tool(self.state, "check_permit", {field: value}), "INVALID_ARGUMENT")

    def test_calls_do_not_change_state(self):
        before = (self.state.player_id, self.state.world_id, dict(self.state.inventory),
                  self.state.quest_stage, self.state.has_permit)
        execute_tool(self.state, "get_inventory", {"item_name": "herb"})
        execute_tool(self.state, "check_permit")
        execute_tool(self.state, "check_permit", {"has_permit": True})
        execute_tool(self.state, "get_inventory", {"item_name": " "})
        after = (self.state.player_id, self.state.world_id, dict(self.state.inventory),
                 self.state.quest_stage, self.state.has_permit)
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
