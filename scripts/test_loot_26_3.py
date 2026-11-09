"""Tests for loot_26_3.py. Standard library only, like the script:

    python scripts/test_loot_26_3.py
"""
import codecs
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

RC = {"condition": "minecraft:random_chance", "chance": 0.5}
RC_NEW = {"type": "minecraft:random_chance", "chance": 0.5}
NOT_RC = {"condition": "minecraft:inverted", "term": RC}
NOT_RC_NEW = {"type": "minecraft:inverted", "term": RC_NEW}
UNIFORM = {"type": "minecraft:uniform", "min": 1, "max": 3}
SC = {"function": "minecraft:set_count", "count": UNIFORM}
SC_NEW = {"type": "minecraft:set_count", "count": UNIFORM}


def copy(o):
    return json.loads(json.dumps(o))


def item(name, **keys):
    return {"type": "minecraft:item", "name": name, **keys}


def table(*entries, **pool_keys):
    return {"pools": [{"rolls": 1, **pool_keys, "entries": list(entries)}]}


def all_of(*terms):
    return {"type": "minecraft:all_of", "terms": list(terms)}


def run_main(*args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = m.main(list(args))
    return code, buf.getvalue()


def old_view(f):
    """What 1.21 to 26.2 read of a function written in both forms."""
    if isinstance(f, list):
        return [old_view(x) for x in f]
    if not isinstance(f, dict):
        return f
    out = {k: v for k, v in f.items() if k not in ("type", "condition")}
    for k in ("functions", "on_pass", "on_fail"):
        if k in out:
            out[k] = old_view(out[k])
    return out


def new_view(f):
    """What 26.3 reads of it."""
    if isinstance(f, list):
        return [new_view(x) for x in f]
    if not isinstance(f, dict):
        return f
    out = {k: v for k, v in f.items() if k not in ("function", "conditions")}
    for k in ("functions", "on_pass", "on_fail"):
        if k in out:
            out[k] = new_view(out[k])
    return out


def view(t, game):
    """What one game reads of a loot table written in both forms: "old" (1.21 to 26.2) or "new" (26.3)."""
    drop = {"old": ("modifier", "condition"), "new": ("functions", "conditions")}[game]

    def entry(e):
        out = {k: v for k, v in e.items() if k not in drop}
        if m.short(e.get("type")) == "tag":
            if game == "old":
                out.pop("items", None)
            elif "items" in e:
                out.pop("name", None)
        if "children" in out:
            out["children"] = [entry(c) for c in out["children"]]
        if m.short(e.get("type")) == "loot_table" and isinstance(out.get("value"), dict):
            out["value"] = tbl(out["value"])
        return out

    def tbl(t):
        out = {k: v for k, v in t.items() if k not in drop}
        if "pools" in out:
            out["pools"] = [{k: ([entry(e) for e in v] if k == "entries" else v)
                             for k, v in p.items() if k not in drop} for p in out["pools"]]
        return out

    return tbl(t)


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


class ItemModifierAndPredicate(unittest.TestCase):
    """Files that hold both forms in one object."""

    def setUp(self):
        m.R = m.Report()

    def test_several_conditions_on_a_function_become_one(self):
        before = dict(SC, conditions=[RC, RC])
        out = m.convert_item_modifier(copy(before))
        self.assertEqual(list(out), ["function", "type", "count", "conditions", "condition"])
        self.assertEqual(old_view(out), before)
        self.assertEqual(new_view(out), dict(SC_NEW, condition=all_of(RC_NEW, RC_NEW)))

    def test_functions_nested_in_filtered_and_sequence_get_both_forms(self):
        before = {"function": "minecraft:filtered", "item_filter": {"items": "minecraft:map"},
                  "on_pass": {"function": "minecraft:sequence", "functions": [SC, dict(SC, conditions=[RC])]}}
        out = m.convert_item_modifier(copy(before))
        self.assertEqual(old_view(out), before)
        self.assertEqual(new_view(out), {
            "type": "minecraft:filtered", "item_filter": {"items": "minecraft:map"},
            "on_pass": {"type": "minecraft:sequence", "functions": [SC_NEW, dict(SC_NEW, condition=RC_NEW)]}})
        self.assertEqual(m.convert_item_modifier(copy(out)), out)

    def test_a_list_holding_a_reference_needs_an_overlay(self):
        # 26.3 writes a reference as its id alone, so one object can't hold both forms
        out = m.convert_item_modifier([copy(SC), {"function": "minecraft:reference", "name": "t:x"}])
        self.assertIs(out, m.CONFLICT)

    def test_a_predicate_gets_both_forms_down_through_its_terms(self):
        out = m.convert_predicate(copy({"condition": "minecraft:all_of", "terms": [RC, NOT_RC]}))
        both = dict(RC, type="minecraft:random_chance")
        self.assertEqual(out, {"condition": "minecraft:all_of", "type": "minecraft:all_of", "terms": [
            both, {"condition": "minecraft:inverted", "type": "minecraft:inverted", "term": both}]})
        self.assertEqual(m.R.problems, [])

    def test_a_block_state_predicate_gets_its_new_names_beside_the_old(self):
        out = m.convert_predicate({"condition": "minecraft:block_state_property", "block": "minecraft:chest",
                                   "properties": {"type": "single"}})
        self.assertEqual(list(out.items()), [
            ("condition", "minecraft:block_state_property"), ("type", "minecraft:match_block"),
            ("block", "minecraft:chest"), ("blocks", "minecraft:chest"),
            ("properties", {"type": "single"}), ("state", {"type": "single"})])

    def test_a_predicate_list_is_reported_and_left_alone(self):
        self.assertEqual(m.convert_predicate([copy(RC), copy(RC)]), [RC, RC])
        self.assertTrue(any("needs an overlay" in p for p in m.R.problems), m.R.problems)


class TableCase(unittest.TestCase):
    def setUp(self):
        m.R = m.Report()

    def convert(self, t):
        """Convert a loot table; 1.21 to 26.2 must read the same as before, and a second run must
        change nothing."""
        out = m.convert_table(copy(t))
        self.assertEqual(view(out, "old"), view(t, "old"))
        self.assertEqual(m.convert_table(copy(out)), out)
        return out


class TableForms(TableCase):
    def test_several_conditions_become_one_all_of(self):
        e = self.convert(table(item("minecraft:diamond", conditions=[RC, NOT_RC])))["pools"][0]["entries"][0]
        self.assertEqual(e["condition"], all_of(RC_NEW, NOT_RC_NEW))

    def test_one_condition_and_one_function_are_written_alone(self):
        e = self.convert(table(item("minecraft:diamond", conditions=[RC], functions=[SC])))["pools"][0]["entries"][0]
        self.assertEqual(e["condition"], RC_NEW)
        self.assertEqual(e["modifier"], SC_NEW)

    def test_several_functions_become_a_list_in_order(self):
        damage = {"function": "minecraft:set_damage", "damage": 0.5, "conditions": [RC, RC]}
        e = self.convert(table(item("minecraft:emerald", functions=[SC, damage])))["pools"][0]["entries"][0]
        self.assertEqual(e["modifier"], [SC_NEW, {"type": "minecraft:set_damage", "damage": 0.5,
                                                  "condition": all_of(RC_NEW, RC_NEW)}])

    def test_nested_alternatives_groups_and_sequences(self):
        before = {"type": "minecraft:chest", "pools": [{"rolls": 1, "entries": [
            {"type": "minecraft:alternatives", "conditions": [RC], "children": [
                item("minecraft:diamond", conditions=[RC, NOT_RC], functions=[SC]),
                {"type": "minecraft:group", "children": [
                    item("minecraft:emerald", functions=[
                        SC, {"function": "minecraft:set_damage", "damage": 0.5, "conditions": [RC, RC]}]),
                    {"type": "minecraft:sequence", "children": [
                        item("minecraft:stick", conditions=[
                            {"condition": "minecraft:any_of", "terms": [RC, NOT_RC]}])]}]}]}]}]}
        out = self.convert(before)
        self.assertEqual(view(out, "new"), {"type": "minecraft:chest", "pools": [{"rolls": 1, "entries": [
            {"type": "minecraft:alternatives", "condition": RC_NEW, "children": [
                item("minecraft:diamond", condition=all_of(RC_NEW, NOT_RC_NEW), modifier=SC_NEW),
                {"type": "minecraft:group", "children": [
                    item("minecraft:emerald", modifier=[
                        SC_NEW, {"type": "minecraft:set_damage", "damage": 0.5, "condition": all_of(RC_NEW, RC_NEW)}]),
                    {"type": "minecraft:sequence", "children": [
                        item("minecraft:stick", condition={"type": "minecraft:any_of",
                                                           "terms": [RC_NEW, NOT_RC_NEW]})]}]}]}]}]})
        self.assertEqual(m.R.problems, [])

    def test_pool_and_table_keys_each_go_right_after_their_old_key(self):
        before = {"type": "minecraft:chest", "functions": [SC], "pools": [
            {"rolls": {"min": 1, "max": 2}, "conditions": [RC, RC], "functions": [SC, SC],
             "entries": [item("minecraft:apple")]}]}
        out = self.convert(before)
        self.assertEqual(list(out), ["type", "functions", "modifier", "pools"])
        self.assertEqual(list(out["pools"][0]), ["rolls", "conditions", "condition", "functions", "modifier", "entries"])
        self.assertEqual(view(out, "new"), {"type": "minecraft:chest", "modifier": SC_NEW, "pools": [
            {"rolls": {"min": 1, "max": 2}, "condition": all_of(RC_NEW, RC_NEW), "modifier": [SC_NEW, SC_NEW],
             "entries": [item("minecraft:apple")]}]})

    def test_empty_lists_add_nothing(self):
        before = {"pools": [{"rolls": 1, "conditions": [], "functions": [], "entries": [
            item("minecraft:apple", functions=[], conditions=[])]}], "functions": []}
        self.assertEqual(self.convert(before), before)

    def test_renamed_parts(self):
        before = table(
            {"type": "minecraft:tag", "name": "minecraft:arrows", "expand": True, "conditions": [
                {"condition": "minecraft:block_state_property", "block": "minecraft:chest",
                 "properties": {"type": "single"}}]},
            {"type": "minecraft:loot_table", "value": table(item("minecraft:stone", functions=[SC]))},
            item("minecraft:bundle", functions=[
                {"function": "minecraft:set_contents", "component": "minecraft:bundle_contents",
                 "entries": [item("minecraft:dirt", functions=[SC], conditions=[RC])]}]),
            item("minecraft:stone", conditions=[
                {"condition": "minecraft:damage_source_properties",
                 "predicate": {"tags": [{"id": "minecraft:is_fire", "expected": True}]}}]))
        out = self.convert(before)
        self.assertEqual(list(out["pools"][0]["entries"][0]),
                         ["type", "name", "items", "expand", "conditions", "condition"])
        self.assertEqual(view(out, "new"), table(
            {"type": "minecraft:tag", "items": "#minecraft:arrows", "expand": True, "condition": {
                "type": "minecraft:match_block", "blocks": "minecraft:chest", "state": {"type": "single"}}},
            {"type": "minecraft:loot_table", "value": table(item("minecraft:stone", modifier=SC_NEW))},
            # set_contents is only read by 26.3 in the 26.3 form, so its entries are 26.3 only
            item("minecraft:bundle", modifier={
                "type": "minecraft:set_contents", "component": "minecraft:bundle_contents",
                "entries": [item("minecraft:dirt", modifier=SC_NEW, condition=RC_NEW)]}),
            item("minecraft:stone", condition={
                "type": "minecraft:damage_source_properties",
                "predicate": {"tags": [{"id": "#minecraft:is_fire", "expected": True}]}})))
        self.assertEqual(m.R.problems, [])

    def test_references_become_their_id(self):
        out = self.convert(table(item("minecraft:paper", functions=[
            {"function": "minecraft:reference", "name": "t:mod"},
            {"function": "minecraft:reference", "name": "t:mod2", "conditions": [RC]}]),
            conditions=[{"condition": "minecraft:reference", "name": "t:pred"}]))
        self.assertEqual(out["pools"][0]["condition"], "t:pred")
        self.assertEqual(out["pools"][0]["entries"][0]["modifier"], ["t:mod", "t:mod2"])
        # a 26.3 reference can't carry conditions
        self.assertEqual(len(m.R.problems), 1)
        self.assertIn("t:mod2", m.R.problems[0])

    def test_ids_without_a_namespace_get_one(self):
        out = self.convert({"pools": [{"rolls": 1, "entries": [{"type": "item", "name": "apple", "functions": [
            {"function": "set_count", "count": 2, "conditions": [{"condition": "random_chance", "chance": 0.5}]}]}]}]})
        self.assertEqual(out["pools"][0]["entries"][0]["modifier"],
                         {"type": "minecraft:set_count", "count": 2, "condition": RC_NEW})
        self.assertIn("ids without a namespace", m.R.notes)

    def test_whole_number_floats_are_written_as_ints(self):
        out = self.convert({"pools": [{"rolls": 1.0, "entries": [item("minecraft:apple", functions=[
            {"function": "minecraft:set_count", "count": 2.0,
             "conditions": [{"condition": "minecraft:random_chance", "chance": 1.0}]}])]}]})
        modifier = out["pools"][0]["entries"][0]["modifier"]
        self.assertIs(type(modifier["count"]), int)
        self.assertIs(type(modifier["condition"]["chance"]), int)
        self.assertIs(type(out["pools"][0]["rolls"]), float)  # shared by both forms: left alone

    def test_value_check_and_sum_are_reported(self):
        self.convert(table(item("minecraft:stone", conditions=[
            {"condition": "minecraft:value_check", "value": 1, "range": 1}], functions=[
            {"function": "minecraft:set_count", "count": {"type": "minecraft:sum", "summands": [1, 2]}}])))
        self.assertTrue(any("value_check" in p for p in m.R.problems), m.R.problems)
        self.assertTrue(any("minecraft:sum" in p for p in m.R.problems), m.R.problems)

    def test_an_edited_old_form_refreshes_the_copy(self):
        once = self.convert(table(item("minecraft:apple", functions=[SC])))
        once["pools"][0]["entries"][0]["functions"][0]["count"] = 7
        e = self.convert(once)["pools"][0]["entries"][0]
        self.assertEqual(e["modifier"], {"type": "minecraft:set_count", "count": 7})


class StaleCopies(TableCase):
    """Deleting an old key takes its 26.3 copy with it, so every version reads the same."""

    def test_deleted_functions_take_the_modifier_with_them(self):
        once = self.convert(table(item("minecraft:apple", conditions=[RC], functions=[SC])))
        del once["pools"][0]["entries"][0]["functions"]
        e = self.convert(once)["pools"][0]["entries"][0]
        self.assertEqual(e, item("minecraft:apple", conditions=[RC], condition=RC_NEW))
        self.assertIn(m.STALE, m.R.notes)

    def test_deleted_conditions_take_the_condition_with_them(self):
        once = self.convert(table(item("minecraft:apple", conditions=[RC], functions=[SC])))
        del once["pools"][0]["entries"][0]["conditions"]
        e = self.convert(once)["pools"][0]["entries"][0]
        self.assertEqual(e, item("minecraft:apple", functions=[SC], modifier=SC_NEW))

    def test_pool_and_table_copies_go_too(self):
        once = self.convert({"functions": [SC], "pools": [
            {"rolls": 1, "conditions": [RC], "functions": [SC], "entries": [item("minecraft:apple")]}]})
        del once["functions"], once["pools"][0]["functions"], once["pools"][0]["conditions"]
        self.assertEqual(self.convert(once), table(item("minecraft:apple")))

    def test_a_table_in_the_26_3_form_only_loses_it(self):
        out = self.convert(table(item("minecraft:apple", modifier={"type": "minecraft:set_count", "count": 3},
                                      condition=RC_NEW)))
        self.assertEqual(out, table(item("minecraft:apple")))
        self.assertEqual(m.R.notes[m.STALE], 2)

    def test_a_tag_entry_without_a_name_loses_its_items(self):
        out = self.convert(table({"type": "minecraft:tag", "items": "#minecraft:arrows", "expand": True}))
        self.assertEqual(out, table({"type": "minecraft:tag", "expand": True}))

    def test_the_key_is_cut_out_of_the_text_with_its_comma(self):
        text = (
            '{\n'
            '  "pools": [\n'
            '    {\n'
            '      "rolls": 1,\n'
            '      "modifier": { "type": "minecraft:set_count", "count": 3 },\n'
            '      "entries": [\n'
            '        { "type": "minecraft:item", "name": "minecraft:apple", "condition": { "type": "minecraft:random_chance", "chance": 0.5 } },\n'  # noqa: E501
            '        {\n'
            '          "modifier": { "type": "minecraft:set_count", "count": 2 },\n'
            '          "type": "minecraft:item",\n'
            '          "name": "minecraft:bread"\n'
            '        }\n'
            '      ]\n'
            '    }\n'
            '  ]\n'
            '}\n')
        self.assertEqual(m.write_like(text, m.convert_table(json.loads(text))), (
            '{\n'
            '  "pools": [\n'
            '    {\n'
            '      "rolls": 1,\n'
            '      "entries": [\n'
            '        { "type": "minecraft:item", "name": "minecraft:apple" },\n'
            '        {\n'
            '          "type": "minecraft:item",\n'
            '          "name": "minecraft:bread"\n'
            '        }\n'
            '      ]\n'
            '    }\n'
            '  ]\n'
            '}\n'))

    def test_check_reports_a_stale_copy_and_passes_once_it_is_gone(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "data" / "t" / "loot_table" / "x.json"
            f.parent.mkdir(parents=True)
            once = m.convert_table(table(item("minecraft:apple", functions=[copy(SC)])))
            del once["pools"][0]["entries"][0]["functions"]
            f.write_text(json.dumps(once, indent=2), encoding="utf-8")
            code, out = run_main("--check", tmp)
            self.assertEqual(code, 1, out)
            self.assertIn("modifier removed x1", out)
            code, out = run_main(tmp)
            self.assertEqual(code, 0, out)
            self.assertEqual(json.loads(f.read_text(encoding="utf-8")), table(item("minecraft:apple")))
            code, out = run_main("--check", tmp)
            self.assertEqual(code, 0, out)


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

    def test_no_destination_means_buried_treasure(self):
        e = self.entry(map_table({"function": "minecraft:exploration_map", "decoration": "red_x", "zoom": 1}))
        self.assertEqual(e["modifier"][0], {"type": "minecraft:set_item", "item": "minecraft:buried_treasure_map"})
        self.assertEqual(e["modifier"][1]["destination"], "#minecraft:on_treasure_maps")
        self.assertEqual(m.R.problems, [])

    def test_a_renamed_explorer_map_tag_gets_its_new_name_and_item(self):
        e = self.entry(map_table(dict(MAP_OLD, destination="minecraft:on_woodland_explorer_maps")))
        self.assertEqual(e["modifier"][0], {"type": "minecraft:set_item", "item": "minecraft:woodland_mansion_map"})
        self.assertEqual(e["modifier"][1]["destination"], "#minecraft:on_woodland_mansion_maps")

    def test_a_map_on_the_pool_is_reported_and_copied_as_it_is(self):
        out = m.convert_table(copy({"pools": [{"rolls": 1, "functions": [MAP_OLD],
                                               "entries": [item("minecraft:map")]}]}))
        self.assertEqual(out["pools"][0]["modifier"], MAP_NEW)
        self.assertEqual(out["pools"][0]["entries"], [item("minecraft:map")])
        self.assertTrue(any("exploration_map on the pool" in p for p in m.R.problems), m.R.problems)

    def test_a_map_on_the_table_is_reported_with_the_map_deep_in_a_group(self):
        m.convert_table(copy({"functions": [MAP_OLD], "pools": [{"rolls": 1, "entries": [
            {"type": "minecraft:group", "children": [{"type": "item", "name": "map"}]}]}]}))
        self.assertTrue(any("exploration_map on the table" in p for p in m.R.problems), m.R.problems)

    def test_a_map_on_the_pool_over_other_items_is_not_reported(self):
        m.convert_table(copy({"pools": [{"rolls": 1, "functions": [MAP_OLD],
                                         "entries": [item("minecraft:compass")]}]}))
        self.assertEqual(m.R.problems, [])

    def test_a_map_with_a_condition_is_reported(self):
        e = self.entry(map_table(dict(MAP_OLD, conditions=[RC])))
        self.assertTrue(any("with a condition" in p for p in m.R.problems), m.R.problems)
        self.assertEqual(e["modifier"][1], dict(MAP_NEW, condition=RC_NEW))  # not rewritten


# A table laid out by hand: some entries on one line, some over several.
HAND = (
    '{\n'
    '  "type": "minecraft:chest",\n'
    '  "pools": [\n'
    '    {\n'
    '      "rolls": 1,\n'
    '      "entries": [\n'
    '        { "type": "minecraft:item", "name": "minecraft:apple", "functions": [ { "function": "minecraft:set_count", "count": 2 } ] },\n'  # noqa: E501
    '        {\n'
    '          "type": "minecraft:item",\n'
    '          "name": "minecraft:bread",\n'
    '          "conditions": [\n'
    '            { "condition": "minecraft:random_chance", "chance": 0.5 }\n'
    '          ],\n'
    '          "weight": 2\n'
    '        }\n'
    '      ]\n'
    '    }\n'
    '  ]\n'
    '}\n')
HAND_DONE = (
    '{\n'
    '  "type": "minecraft:chest",\n'
    '  "pools": [\n'
    '    {\n'
    '      "rolls": 1,\n'
    '      "entries": [\n'
    '        { "type": "minecraft:item", "name": "minecraft:apple", "functions": [ { "function": "minecraft:set_count", "count": 2 } ], "modifier": { "type": "minecraft:set_count", "count": 2 } },\n'  # noqa: E501
    '        {\n'
    '          "type": "minecraft:item",\n'
    '          "name": "minecraft:bread",\n'
    '          "conditions": [\n'
    '            { "condition": "minecraft:random_chance", "chance": 0.5 }\n'
    '          ],\n'
    '          "condition": {\n'
    '            "type": "minecraft:random_chance",\n'
    '            "chance": 0.5\n'
    '          },\n'
    '          "weight": 2\n'
    '        }\n'
    '      ]\n'
    '    }\n'
    '  ]\n'
    '}\n')
NESTED = {"type": "minecraft:chest", "pools": [{"rolls": 1, "entries": [
    {"type": "minecraft:alternatives", "conditions": [RC], "children": [
        item("minecraft:diamond", conditions=[RC, NOT_RC], functions=[SC]),
        {"type": "minecraft:group", "children": [item("minecraft:emerald", functions=[
            SC, {"function": "minecraft:set_damage", "damage": 0.5, "conditions": [RC, RC]}])]}]}]}]}


class Layout(unittest.TestCase):
    """The new keys go into the file's own text; nothing else changes."""

    def setUp(self):
        m.R = m.Report()

    def written(self, text):
        return m.write_like(text, m.convert_table(json.loads(text)))

    def test_a_hand_laid_out_file_keeps_its_layout(self):
        self.assertEqual(self.written(HAND), HAND_DONE)

    def test_windows_line_endings_stay(self):
        self.assertEqual(self.written(HAND.replace("\n", "\r\n")), HAND_DONE.replace("\n", "\r\n"))

    def test_each_indent_is_kept(self):
        want = m.convert_table(copy(NESTED))
        for indent in (2, 4, "\t"):
            with self.subTest(indent=indent):
                self.assertEqual(self.written(json.dumps(NESTED, indent=indent)), json.dumps(want, indent=indent))

    def test_a_one_line_file_stays_on_one_line(self):
        want = m.convert_table(copy(NESTED))
        self.assertEqual(self.written(json.dumps(NESTED)), json.dumps(want))
        minified = self.written(json.dumps(NESTED, separators=(",", ":")))
        self.assertNotIn("\n", minified)
        self.assertEqual(json.loads(minified), want)

    def test_non_ascii_text_and_escapes_stay(self):
        before = table(item("minecraft:paper", functions=[
            {"function": "minecraft:set_name", "name": {"text": "Café \"Ünïcode\" \\ ✓"}, "target": "item_name"}]))
        text = json.dumps(before, indent=2, ensure_ascii=False)
        self.assertEqual(self.written(text),
                         json.dumps(m.convert_table(copy(before)), indent=2, ensure_ascii=False))

    def test_duplicate_keys_read_as_the_game_reads_them(self):
        # the last one wins, as in the game, and both forms read the same count
        text = ('{"pools":[{"rolls":1,"entries":[{"type":"minecraft:item","name":"minecraft:apple",'
                '"functions":[{"function":"minecraft:set_count","count":1}],'
                '"functions":[{"function":"minecraft:set_count","count":5}]}]}]}')
        e = json.loads(self.written(text))["pools"][0]["entries"][0]
        self.assertEqual(e["modifier"], {"type": "minecraft:set_count", "count": 5})

    def test_a_byte_order_mark_is_written_back_and_never_added(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "data" / "t" / "loot_table"
            folder.mkdir(parents=True)
            with_bom, without = folder / "bom.json", folder / "plain.json"
            with_bom.write_bytes(codecs.BOM_UTF8 + HAND.replace("\n", "\r\n").encode("utf-8"))
            without.write_bytes(HAND.encode("utf-8"))
            code, out = run_main(tmp)
            self.assertEqual(code, 0, out)
            self.assertEqual(with_bom.read_bytes(), codecs.BOM_UTF8 + HAND_DONE.replace("\n", "\r\n").encode("utf-8"))
            self.assertEqual(without.read_bytes(), HAND_DONE.encode("utf-8"))


class CheckRun(unittest.TestCase):
    def test_check_reports_a_list_file_and_passes_once_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "data" / "test" / "item_modifier" / "steps.json"
            f.parent.mkdir(parents=True)
            f.write_text(json.dumps(STEPS, indent=2), encoding="utf-8")
            code, out = run_main("--check", tmp)
            self.assertEqual(code, 1, out)
            self.assertIn("would update", out)
            self.assertIn("1 list written as one sequence", out)
            code, out = run_main(tmp)
            self.assertEqual(code, 0, out)
            self.assertEqual(json.loads(f.read_text(encoding="utf-8"))["type"], "minecraft:sequence")
            code, out = run_main("--check", tmp)
            self.assertEqual(code, 0, out)

    def test_1_20_folders_and_overlays_26_3_never_reads_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pack.mcmeta").write_text(json.dumps({"pack": {"description": ""}, "overlays": {"entries": [
                {"formats": [48, 57], "directory": "old_list"},
                {"min_format": 71, "max_format": 107, "directory": "old_range"},
                {"formats": {"min_inclusive": 48, "max_inclusive": 121}, "directory": "every"}]}}), encoding="utf-8")
            files = {where: root / where / "data" / "t" / kind / "x.json" for where, kind in (
                ("old_list", "loot_table"), ("old_range", "loot_table"), ("every", "loot_table"),
                ("main", "loot_table"), ("1.20", "loot_tables"))}
            for f in files.values():
                f.parent.mkdir(parents=True)
                f.write_text(HAND, encoding="utf-8")
            code, out = run_main(tmp)
            self.assertEqual(code, 0, out)
            self.assertIn("skipped 1 in 1.20 folders; skipped 2 in overlays 26.3 never reads", out)
            for where, f in files.items():
                with self.subTest(where=where):
                    self.assertEqual(f.read_text(encoding="utf-8"),
                                     HAND_DONE if where in ("every", "main") else HAND)

    def test_a_file_that_is_not_json_is_reported_and_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "data" / "t" / "loot_table" / "x.json"
            f.parent.mkdir(parents=True)
            broken = '{"pools": [ {"rolls": 1, "entries": [], } ] }'
            f.write_text(broken, encoding="utf-8")
            code, out = run_main(tmp)
            self.assertEqual(code, 1, out)
            self.assertIn("CHECK BY HAND", out)
            self.assertIn("could not read it as loot_table", out)
            self.assertEqual(f.read_text(encoding="utf-8"), broken)

    def test_other_data_with_old_form_conditions_is_only_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "data" / "t" / "enchantment" / "x.json"
            f.parent.mkdir(parents=True)
            text = json.dumps({"effects": {"minecraft:damage": [
                {"effect": {"type": "minecraft:add", "value": 1}, "requirements": RC}]}}, indent=2)
            f.write_text(text, encoding="utf-8")
            code, out = run_main(tmp)
            self.assertEqual(code, 1, out)
            self.assertIn("not a kind this script converts", out)
            self.assertEqual(f.read_text(encoding="utf-8"), text)


if __name__ == "__main__":
    unittest.main()
