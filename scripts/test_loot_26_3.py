"""Tests for loot_26_3.py. Standard library only, like the script:

    python scripts/test_loot_26_3.py
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loot_26_3 as m  # noqa: E402

STEPS = [{"function": "minecraft:set_count", "count": 2,
          "conditions": [{"condition": "minecraft:random_chance", "chance": 0.5}]},
         {"function": "minecraft:enchant_randomly", "options": "#minecraft:on_random_loot"}]


def old_view(f):
    """What 1.21 to 26.2 read of a function written in both forms."""
    if isinstance(f, list):
        return [old_view(x) for x in f]
    if not isinstance(f, dict):
        return f
    out = {k: v for k, v in f.items() if k not in ("type", "condition")}
    if isinstance(out.get("functions"), list):
        out["functions"] = [old_view(x) for x in out["functions"]]
    return out


def new_view(f):
    """What 26.3 reads of it."""
    if not isinstance(f, dict):
        return f
    out = {k: v for k, v in f.items() if k not in ("function", "conditions")}
    if isinstance(out.get("functions"), list):
        out["functions"] = [new_view(x) for x in out["functions"]]
    return out


class ListItemModifier(unittest.TestCase):
    def setUp(self):
        m.R = m.Report()

    def test_a_list_becomes_one_sequence_both_read(self):
        out = m.convert_item_modifier(json.loads(json.dumps(STEPS)))
        self.assertIsInstance(out, dict)   # 26.3 reads one function per file
        # 1.21 to 26.2: the same sequence the list stood for
        self.assertEqual(old_view(out), {"function": "minecraft:sequence", "functions": STEPS})
        # 26.3: a sequence of the same steps in its own form, under the same "functions"
        self.assertEqual(new_view(out), {"type": "minecraft:sequence", "functions": [
            {"type": "minecraft:set_count", "count": 2,
             "condition": {"type": "minecraft:random_chance", "chance": 0.5}},
            {"type": "minecraft:enchant_randomly", "options": "#minecraft:on_random_loot"}]})
        self.assertIn("list written as one sequence", m.R.notes)

    def test_converting_again_changes_nothing(self):
        once = m.convert_item_modifier(json.loads(json.dumps(STEPS)))
        self.assertEqual(m.convert_item_modifier(json.loads(json.dumps(once))), once)

    def test_one_function_still_gets_both_forms_in_place(self):
        out = m.convert_item_modifier(json.loads(json.dumps(STEPS[0])))
        self.assertEqual(out["function"], "minecraft:set_count")
        self.assertEqual(out["type"], "minecraft:set_count")
        self.assertEqual(out["condition"], {"type": "minecraft:random_chance", "chance": 0.5})


MAP_OLD = {"function": "minecraft:exploration_map", "destination": "minecraft:on_treasure_maps",
           "decoration": "red_x", "zoom": 1, "skip_existing_chunks": False}
MAP_NEW = {"type": "minecraft:exploration_map", "destination": "#minecraft:on_treasure_maps",
           "decoration": "red_x", "zoom": 1, "skip_existing_chunks": False}
KEEP_FILLED = {"type": "minecraft:filtered", "item_filter": {"predicates": {"minecraft:map_id": {}}},
               "on_fail": {"type": "minecraft:discard"}}


def map_table(*functions, name="minecraft:map"):
    return {"pools": [{"rolls": 1, "entries": [{"type": "minecraft:item", "name": name,
                                                "functions": [dict(f) for f in functions]}]}]}


class TreasureMap(unittest.TestCase):
    """26.3 writes the map onto the item it is given: a plain minecraft:map stays blank."""

    def setUp(self):
        m.R = m.Report()

    def entry(self, table):
        return m.convert_table(json.loads(json.dumps(table)))["pools"][0]["entries"][0]

    def test_a_plain_map_gets_its_map_item_and_keeps_only_filled_maps(self):
        e = self.entry(map_table(MAP_OLD))
        self.assertEqual(e["name"], "minecraft:map")            # shared by both forms
        self.assertEqual(e["functions"], [MAP_OLD])              # the old form is untouched
        self.assertEqual(e["modifier"], [{"type": "minecraft:set_item", "item": "minecraft:buried_treasure_map"},
                                         MAP_NEW, KEEP_FILLED])

    def test_other_functions_keep_their_place(self):
        named = {"function": "minecraft:set_name", "name": "Treasure"}
        e = self.entry(map_table(MAP_OLD, named))
        self.assertEqual([f["type"] for f in e["modifier"]], ["minecraft:set_item", "minecraft:exploration_map",
                                                               "minecraft:filtered", "minecraft:set_name"])

    def test_converting_the_result_again_changes_nothing(self):
        once = m.convert_table(json.loads(json.dumps(map_table(MAP_OLD))))
        self.assertEqual(m.convert_table(json.loads(json.dumps(once))), once)

    def test_a_destination_with_no_vanilla_map_item_is_reported(self):
        e = self.entry(map_table(dict(MAP_OLD, destination="minecraft:on_treasure_maps_x")))
        self.assertNotIn("set_item", json.dumps(e["modifier"]))
        self.assertTrue(any("no map item" in p for p in m.R.problems), m.R.problems)

    def test_an_item_that_is_not_a_plain_map_is_left_alone(self):
        e = self.entry(map_table(MAP_OLD, name="minecraft:compass"))
        self.assertEqual(e["modifier"], MAP_NEW)


class CheckRun(unittest.TestCase):
    def run_main(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = m.main(list(args))
        return code, buf.getvalue()

    def test_check_reports_a_list_file_and_passes_once_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "data" / "test" / "item_modifier" / "steps.json"
            f.parent.mkdir(parents=True)
            f.write_text(json.dumps(STEPS, indent=2), encoding="utf-8")
            code, out = self.run_main("--check", tmp)
            self.assertEqual(code, 1, out)
            self.assertIn("would update", out)
            self.assertIn("1 list written as one sequence", out)
            code, out = self.run_main(tmp)
            self.assertEqual(code, 0, out)
            self.assertEqual(json.loads(f.read_text(encoding="utf-8"))["type"], "minecraft:sequence")
            code, out = self.run_main("--check", tmp)
            self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
