#!/usr/bin/env python3
"""
Give loot data the Minecraft 26.3 form next to the old one, so one file works on 1.21 to 26.3.

Minecraft 26.3 changed how loot functions and conditions are written, and it ignores the old keys
without any error: a loot table written for 1.21 to 26.2 loads on 26.3 with its functions and
conditions gone (stack counts, enchanting, damage, random chances). Read from the 26.3 jar's own
tables and code:

    old (1.21 to 26.2)                          26.3
    "functions": [ {...}, ... ]                 "modifier": {...}   (or a list when several)
    "conditions": [ {...}, ... ]                "condition": {...}  (several: minecraft:all_of)
    a function's "function": "minecraft:x"      "type": "minecraft:x"
    a function's "conditions": [...]            "condition": {...}
    a condition's "condition": "minecraft:x"    "type": "minecraft:x"

These apply on tables, pools and entries (and an entry's children). Older games never read
"modifier" or "condition" there, and 26.3 never reads "functions" or "conditions", so this script
keeps the old keys as they are and writes the 26.3 keys beside them, built from the old ones.
The old keys stay the source: edit those, then run this again to refresh the 26.3 copy. Delete one
and its 26.3 copy goes too: a 26.3 key with no old key beside it (or a table written in the 26.3
form only) is removed, so every version reads the same loot.

26.3 also renamed a few things inside them, which the 26.3 copy gets:
    condition block_state_property {block, properties}   -> match_block {blocks, state}
    condition or function "reference" {name}             -> the name alone, as a string
    damage_source_properties tag ids                     -> with a "#" ("#minecraft:is_fire")
    exploration_map destination: a structure tag id      -> "#" + the tag (26.3 reads a bare id as
                                                            one structure); 26.3 requires it, so a
                                                            missing one gets the old default
                                                            "#minecraft:on_treasure_maps"; the five
                                                            explorer map tags 26.3 renamed get their
                                                            new names; one that isn't a tag is reported
    exploration_map on a plain minecraft:map             -> 26.3 writes the map onto the item it is
                                                            given and no longer turns a minecraft:map
                                                            into a filled map, so a plain map stays
                                                            blank; vanilla names each map's own item
                                                            instead. The entry's name is shared by both
                                                            forms and those items don't exist before
                                                            26.3, so the 26.3 copy sets the item first
                                                            (set_item minecraft:buried_treasure_map for
                                                            #minecraft:on_treasure_maps, the item vanilla
                                                            uses for each map tag) and then keeps only
                                                            filled maps (filtered on minecraft:map_id,
                                                            on_fail discard), as vanilla 26.3 does; a
                                                            destination with no vanilla map item is
                                                            reported. So is an exploration_map on a
                                                            pool or table with a plain map under it
                                                            (only one on the map entry gets its item),
                                                            and one with a condition on a plain map
                                                            (when it fails, 1.21 to 26.2 give an empty
                                                            map, the 26.3 form nothing); neither is
                                                            rewritten
    tag entry "name": "x"                                -> "items": "#x" (beside "name")
    a type id with no namespace ("set_count")            -> "minecraft:set_count"
Things it cannot convert safely (value_check, the "sum" number provider) are reported.

What it looks at, under each path given (a mod repo, a data folder, a loot folder or one file):
    loot_table/      both forms side by side, as above
    item_modifier/   each function gets both forms in the same object ("function" and "type",
                     "conditions" and "condition"); the two games each read only their own keys.
                     A file that is a list is written as one minecraft:sequence object: 1.21 to
                     26.2 read a list file as a sequence, but 26.3 reads one function per file.
                     Both read the steps from "functions", so each step gets both forms there
    predicate/       the same for conditions; a predicate file that is a list can't hold both
                     (26.3 wants one object) and is reported as needing an overlay
    anything else under data/ with old-form functions or conditions in it (advancements,
                     enchantments...) is reported, not changed
Plural 1.20 folders (loot_tables...) are skipped: 1.20 never runs on 26.3. So are pack overlays
whose formats stop before 26.3's (121), which 26.3 never reads.

Two things that are the same on every version, which it reports so they can be looked at:
    - enchant_randomly / enchant_with_levels with no "options" draw from every enchantment in
      the game: curses, treasure-only ones (mending, soul speed, swift sneak...) and other mods'.
      Vanilla chests use "#minecraft:on_random_loot". --add-enchant-options adds that.
    - type ids with no namespace ("enchant_randomly") read as minecraft: on every version.

Usage:
    python loot_26_3.py [path ...]           # default: the current folder (a mod repo's root)
    python loot_26_3.py --dry-run [path ...] # change nothing, list what would change; exit 1 if
                                             # anything would (--check does the same)
    python loot_26_3.py --dry-run --diff     # also show each change
    python loot_26_3.py --add-enchant-options [path ...]

It only needs Python 3.9+, so it can be copied into any mod's scripts/ or run from here.
Only the 1.21 branches need it.
"""
import argparse
import codecs
import difflib
import json
import re
import sys
from pathlib import Path

NEW_FORMAT = 121  # 26.3's data pack format

# Functions whose fields hold more loot data that has to be converted too.
NESTED_FUNCTION_FIELDS = {
    "sequence": {"functions": "function_list"},
    "filtered": {"on_pass": "function", "on_fail": "function"},
    "modify_contents": {"modifier": "function"},
    "set_contents": {"entries": "entry_list"},
}
# Conditions whose fields hold more conditions.
NESTED_CONDITION_FIELDS = {
    "inverted": {"term": "condition"},
    "all_of": {"terms": "condition_list"},
    "any_of": {"terms": "condition_list"},
}
# Gone in 26.3 with no one-to-one replacement: reported, copied as they are.
NOT_CONVERTED = {
    "minecraft:value_check": "26.3 split it into int_value_check and float_value_check",
    "minecraft:sum": "26.3 has no sum number provider (it has add)",
}
ENCHANT_FUNCTIONS = ("enchant_randomly", "enchant_with_levels")
ON_RANDOM_LOOT = "#minecraft:on_random_loot"
# exploration_map "destination": a structure tag id before 26.3, a structure, list or "#tag" in
# 26.3. Vanilla's map tags, and the ones 26.3 renamed (same contents, new name).
MAP_TAGS_RENAMED = {
    "minecraft:on_jungle_explorer_maps": "minecraft:on_jungle_pyramid_maps",
    "minecraft:on_ocean_explorer_maps": "minecraft:on_ocean_monument_maps",
    "minecraft:on_swamp_explorer_maps": "minecraft:on_swamp_hut_maps",
    "minecraft:on_trial_chambers_maps": "minecraft:on_buried_trial_chambers_maps",
    "minecraft:on_woodland_explorer_maps": "minecraft:on_woodland_mansion_maps",
}
# 26.3's map items, per destination tag (from vanilla 26.3's chests and cartographer trades).
MAP_ITEMS = {"minecraft:on_" + tag: "minecraft:" + item for tag, item in (
    ("treasure_maps", "buried_treasure_map"), ("ancient_city_maps", "buried_ancient_city_map"),
    ("buried_trial_chambers_maps", "buried_trial_chambers_map"), ("mineshaft_maps", "buried_mineshaft_map"),
    ("desert_pyramid_maps", "desert_pyramid_map"), ("jungle_pyramid_maps", "jungle_pyramid_map"),
    ("ocean_ruin_warm_maps", "warm_ocean_ruins_map"), ("woodland_mansion_maps", "woodland_mansion_map"),
    ("ocean_monument_maps", "ocean_monument_map"), ("swamp_hut_maps", "swamp_hut_map"),
    ("desert_village_maps", "desert_village_map"), ("plains_village_maps", "plains_village_map"),
    ("savanna_village_maps", "savanna_village_map"), ("snowy_village_maps", "snowy_village_map"),
    ("taiga_village_maps", "taiga_village_map"))}
KEEP_FILLED_MAPS = {"type": "minecraft:filtered", "item_filter": {"predicates": {"minecraft:map_id": {}}},
                    "on_fail": {"type": "minecraft:discard"}}
VANILLA_STRUCTURE_TAGS = {"minecraft:" + t for t in (
    "on_treasure_maps on_desert_village_maps on_plains_village_maps on_savanna_village_maps "
    "on_snowy_village_maps on_taiga_village_maps on_ancient_city_maps on_buried_trial_chambers_maps "
    "on_desert_pyramid_maps on_jungle_pyramid_maps on_mineshaft_maps on_ocean_monument_maps "
    "on_ocean_ruin_warm_maps on_swamp_hut_maps on_woodland_mansion_maps abandoned_camp mineshaft "
    "ocean_ruin ruined_portal shipwreck village cats_spawn_in cats_spawn_as_black dolphin_located "
    "eye_of_ender_located").split()}

# The 26.3 keys on tables, pools and entries, and the old key each is built from.
NEW_KEYS = {"modifier": "functions", "condition": "conditions"}
STALE = "26.3 keys with no old key"

KINDS = {"loot_table", "item_modifier", "predicate"}
OLD_KINDS = {"loot_tables", "item_modifiers", "predicates"}
SKIP_DIRS = {".git", ".gradle", "build", "out", "node_modules", ".idea"}


class Report:
    """What converting one file found: problems (fix by hand) and notes (worth a look)."""

    def __init__(self):
        self.problems = []
        self.notes = {}

    def problem(self, msg):
        if msg not in self.problems:
            self.problems.append(msg)

    def note(self, key):
        self.notes[key] = self.notes.get(key, 0) + 1


R = Report()  # the current file's report
ADD_ENCHANT_OPTIONS = False
DATA_ROOT = None  # the data/ folder the current file is in, if any


def short(type_id):
    return type_id.split(":", 1)[1] if isinstance(type_id, str) and ":" in type_id else type_id


def namespaced(type_id):
    if isinstance(type_id, str) and ":" not in type_id and not type_id.startswith("#"):
        R.note("ids without a namespace")
        return "minecraft:" + type_id
    return type_id


def tagged(v):
    return v if v.startswith("#") else "#" + v


def plain(v):
    """A value copied into the 26.3 form: whole-number floats written as ints, as 26.3's own tables
    are (both read the same), and anything 26.3 has dropped reported."""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, dict):
        t = v.get("type")
        if t in NOT_CONVERTED:
            R.problem(f"{t}: {NOT_CONVERTED[t]}; copied as it is, fix it by hand")
        return {k: plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [plain(x) for x in v]
    return v


# --- Old form -> 26.3 form ---------------------------------------------------------------------

def new_condition(c):
    """One old-form condition -> 26.3 form."""
    if isinstance(c, str):  # already a reference to a predicate file
        return c
    if isinstance(c, list):  # a list in a condition's place means all of them
        return new_condition_list(c)
    ctype = namespaced(c.get("condition", c.get("type")))
    sid = short(ctype)
    if sid == "reference":
        return c["name"]
    if ctype in NOT_CONVERTED:
        R.problem(f"{ctype}: {NOT_CONVERTED[ctype]}; copied as it is, fix it by hand")
    nested = NESTED_CONDITION_FIELDS.get(sid, {})
    out = {"type": ctype}
    for k, v in c.items():
        if k in ("condition", "type"):
            continue
        kind = nested.get(k)
        if kind == "condition":
            out[k] = new_condition(v)
        elif kind == "condition_list":
            out[k] = [new_condition(x) for x in v]
        else:
            out[k] = plain(v)
    if sid == "block_state_property":
        renamed = {"type": "minecraft:match_block", "blocks": out.pop("block")}
        if "properties" in out:
            renamed["state"] = out.pop("properties")
        del out["type"]
        renamed.update(out)
        out = renamed
    elif sid == "damage_source_properties":
        for tag in out.get("predicate", {}).get("tags", []):
            if isinstance(tag, dict) and isinstance(tag.get("id"), str):
                tag["id"] = tagged(tag["id"])
    return out


def new_condition_list(conds):
    """An old "conditions" list -> one 26.3 "condition" (None when empty)."""
    conds = [new_condition(c) for c in conds]
    if not conds:
        return None
    if len(conds) == 1:
        return conds[0]
    return {"type": "minecraft:all_of", "terms": conds}


def new_function(f):
    """One old-form function -> 26.3 form."""
    if isinstance(f, str):  # already a reference to an item_modifier file
        return f
    if isinstance(f, list):
        return new_function_list(f)
    ftype = namespaced(f.get("function", f.get("type")))
    sid = short(ftype)
    if sid == "reference":
        if f.get("conditions"):
            R.problem(f"a reference function with conditions ({f['name']}): 26.3 references carry "
                      f"no conditions; the conditions were left out of the 26.3 form")
        return f["name"]
    nested = NESTED_FUNCTION_FIELDS.get(sid, {})
    out = {"type": ftype}
    for k, v in f.items():
        if k in ("function", "type"):
            continue
        if k == "conditions":
            cond = new_condition_list(v)
            if cond is not None:
                out["condition"] = cond
            continue
        kind = nested.get(k)
        if kind == "function":
            out[k] = new_function(v)
        elif kind == "function_list":
            out[k] = [new_function(x) for x in v] if isinstance(v, list) else new_function(v)
        elif kind == "entry_list":
            out[k] = [convert_entry(dict(e), only_new=True) for e in v]
        else:
            out[k] = plain(v)
    if sid == "exploration_map":
        out["destination"] = map_destination(out.get("destination", "minecraft:on_treasure_maps"))
    return out


def map_destination(v):
    """An old exploration_map destination (always a structure tag) in 26.3's form."""
    if not isinstance(v, str):
        return v
    tag = namespaced(v.lstrip("#"))
    tag = MAP_TAGS_RENAMED.get(tag, tag)
    if tag in VANILLA_STRUCTURE_TAGS:
        return "#" + tag
    ns, _, path = tag.partition(":")
    if DATA_ROOT and (DATA_ROOT / ns / "tags" / "worldgen" / "structure" / (path + ".json")).is_file():
        return "#" + tag
    fix = ""
    if tag == "minecraft:buried_treasure":
        fix = (" To fix: change the old form's destination to \"minecraft:on_treasure_maps\" (vanilla's "
               "buried treasure map tag) and run this again; the 26.3 form then gets "
               "\"#minecraft:on_treasure_maps\".")
    R.problem(f"exploration_map destination \"{v}\" is not a structure tag here: 1.21 to 26.2 read it "
              f"as a tag, so that map finds nothing there, while 26.3 reads it as the structure "
              f"\"{tag}\" itself. The 26.3 form keeps \"{v}\" for now." + (fix or
              " To fix: put a structure tag in the old form's destination (vanilla's map tags are "
              "minecraft:on_*_maps) and run this again."))
    return v


def new_function_list(funcs):
    """An old "functions" list -> the 26.3 "modifier" value (None when empty)."""
    funcs = [new_function(f) for f in funcs]
    if not funcs:
        return None
    if len(funcs) == 1:
        return funcs[0]
    return funcs


def check_enchant_options(funcs):
    """Note (and with --add-enchant-options fill in) enchant functions with no options.
    Works on the old form, so both forms get the change."""
    out = []
    for f in funcs:
        if isinstance(f, dict) and short(f.get("function", f.get("type"))) in ENCHANT_FUNCTIONS \
                and "options" not in f:
            R.note("enchant functions without options")
            if ADD_ENCHANT_OPTIONS:
                f = {**f, "options": ON_RANDOM_LOOT}
        out.append(f)
    return out


def map_item_for(destination):
    """The 26.3 map item vanilla uses for an exploration_map destination, or None."""
    if not isinstance(destination, str):
        return None
    tag = namespaced(destination.lstrip("#"))
    if tag.startswith("minecraft:on_abandoned_camp_"):
        return "minecraft:abandoned_camp_map"
    return MAP_ITEMS.get(tag)


def with_map_steps(modifier):
    """A plain minecraft:map's 26.3 modifier: each exploration_map gets its map item set before it
    and only filled maps kept after it (26.3 writes the map onto the item it is given)."""
    steps = modifier if isinstance(modifier, list) else [modifier]
    out, has_item = [], False
    for f in steps:
        kind = short(f.get("type")) if isinstance(f, dict) else None
        if kind == "set_item":
            has_item = True
        if kind == "exploration_map" and not has_item:
            item = map_item_for(f.get("destination"))
            if item is None:
                R.problem(f"exploration_map on minecraft:map to {f.get('destination')}: on 26.3 a plain map "
                          f"stays blank, and vanilla has no map item for that destination; put a set_item "
                          f"with the map item to use before the exploration_map in the 26.3 form by hand")
                out.append(f)
                continue
            if "condition" in f:
                R.problem(f"exploration_map with a condition on minecraft:map to {f.get('destination')}: when "
                          f"the condition fails, 1.21 to 26.2 give a plain empty map, but the 26.3 form gives "
                          f"nothing (it sets the map item first and keeps only filled maps); left that way. "
                          f"If the empty map isn't meant to drop, put the condition on the entry in the old "
                          f"form instead and run this again")
            out += [{"type": "minecraft:set_item", "item": item}, f, json.loads(json.dumps(KEEP_FILLED_MAPS))]
            has_item = True
            continue
        out.append(f)
    return out if len(out) > 1 else out[0]


def is_plain_map(e):
    return isinstance(e, dict) and short(e.get("type")) == "item" and e.get("name") in ("map", "minecraft:map")


def holds_plain_map(entries):
    """Is a plain minecraft:map among these entries, their children or a table written inline?"""
    for e in entries if isinstance(entries, list) else ():
        if not isinstance(e, dict):
            continue
        value = e.get("value") if short(e.get("type")) == "loot_table" else None
        pools = value.get("pools") if isinstance(value, dict) else None
        if is_plain_map(e) or holds_plain_map(e.get("children")) or any(
                isinstance(p, dict) and holds_plain_map(p.get("entries")) for p in pools or ()):
            return True
    return False


def check_map_above(funcs, where, entry_lists):
    """Report an exploration_map on a pool or table with a plain minecraft:map under it: only one
    on the map entry itself gets its 26.3 map item, so this map would stay blank on 26.3."""
    if isinstance(funcs, list) and any(
            isinstance(f, dict) and short(f.get("function", f.get("type"))) == "exploration_map" for f in funcs) \
            and any(holds_plain_map(x) for x in entry_lists):
        R.problem(f"exploration_map on the {where}, with a plain minecraft:map under it: 26.3 writes the map "
                  f"onto the item it is given, so that map stays blank there, and only an exploration_map on "
                  f"the map entry itself gets its 26.3 map item; the 26.3 form copies it as it is. To fix: "
                  f"move the exploration_map onto the minecraft:map entry in the old form and run this again")


def with_new_keys(obj, extra=None, plain_map=False, gone=()):
    """Return obj with "modifier"/"condition" (re)built from "functions"/"conditions", each right
    after its old key, and any key in `extra` ({new key: (after key, value)}) likewise.
    A 26.3 key whose old key is gone (deleted from the old form, or never there: a table written
    in the 26.3 form only) is removed, so every version reads the same; `gone` names more keys to
    remove that way (a tag entry's "items" with no "name").
    plain_map: the object is a minecraft:map entry, whose 26.3 form gets the map steps."""
    extra = extra or {}
    stale = [k for k, old in NEW_KEYS.items() if k in obj and old not in obj]
    stale += [k for k in gone if k in obj]
    for _ in stale:
        R.note(STALE)
    if not any(old in obj for old in NEW_KEYS.values()) and not extra and not stale:
        return obj
    out = {}
    for k, v in obj.items():
        if k in NEW_KEYS or k in extra or k in stale:
            continue  # rebuilt below, after the key it comes from, or removed
        if k == "functions":
            v = check_enchant_options(v)
        out[k] = v
        if k == "functions":
            m = new_function_list(v)
            if m is not None and plain_map:
                m = with_map_steps(m)
            if m is not None:
                out["modifier"] = m
        elif k == "conditions":
            c = new_condition_list(v)
            if c is not None:
                out["condition"] = c
        for nk, (after, nv) in extra.items():
            if k == after:
                out[nk] = nv
    return out


def convert_entry(e, only_new=False):
    """Old entry -> entry with both forms. only_new=True (inside a part only 26.3 reads, such as a
    set_contents entry list) leaves the 26.3 form alone."""
    if "children" in e:
        e["children"] = [convert_entry(dict(c), only_new) for c in e["children"]]
    if short(e.get("type")) == "loot_table" and isinstance(e.get("value"), dict):
        e["value"] = convert_table(dict(e["value"]), only_new)  # a table written inline
    extra, gone = {}, ()
    if short(e.get("type")) == "tag":
        if "name" in e:
            extra["items"] = ("name", tagged(e["name"]))
        else:
            gone = ("items",)
    plain_map = short(e.get("type")) == "item" and short(namespaced(e.get("name") or "")) == "map"
    e = with_new_keys(e, extra, plain_map=plain_map, gone=gone)
    if only_new:
        for k in ("functions", "conditions"):
            e.pop(k, None)
        if "items" in extra:
            e.pop("name", None)
    return e


def convert_table(t, only_new=False):
    def drop_old(o):
        if only_new:
            for k in ("functions", "conditions"):
                o.pop(k, None)
        return o

    check_map_above(t.get("functions"), "table", [p.get("entries") for p in t.get("pools") or ()])
    t = drop_old(with_new_keys(t))
    if "pools" in t:
        pools = []
        for p in t["pools"]:
            p = dict(p)
            check_map_above(p.get("functions"), "pool", [p.get("entries")])
            if "entries" in p:
                p["entries"] = [convert_entry(dict(e), only_new) for e in p["entries"]]
            pools.append(drop_old(with_new_keys(p)))
        t["pools"] = pools
    return t


# --- Item modifier and predicate files: both forms in one object --------------------------------

CONFLICT = object()
# new keys go right after the old key they come from
FOLLOWS = {"type": ("function", "condition"), "condition": ("conditions",), "blocks": ("block",),
           "state": ("properties",)}


def merge(old, new):
    """One value both games can read: old and new keys side by side. CONFLICT when the same key
    would need a different value in each (only an overlay can do that)."""
    if isinstance(old, dict) and isinstance(new, dict):
        out = {}
        later = [k for k in new if k not in old]
        for k, v in old.items():
            if k in new:
                m = merge(v, new[k])
                if m is CONFLICT:
                    return CONFLICT
                out[k] = m
            else:
                out[k] = v
            for nk in list(later):
                if k in FOLLOWS.get(nk, ()):
                    out[nk] = new[nk]
                    later.remove(nk)
        for nk in later:
            out[nk] = new[nk]
        return out
    if isinstance(old, list) and isinstance(new, list):
        if len(old) != len(new):
            return CONFLICT
        parts = [merge(a, b) for a, b in zip(old, new)]
        return CONFLICT if any(p is CONFLICT for p in parts) else parts
    return old if old == new else CONFLICT


def convert_item_modifier(d):
    if isinstance(d, list):
        # 1.21 to 26.2 read a list file as a sequence; 26.3 reads an item modifier file with the
        # one-function codec only (the list form is for inline use), so it refuses a list. Written
        # as the sequence it stands for, one object serves both: "function" and "type" side by
        # side, and the steps under "functions", the key both read, each in both forms.
        R.note("list written as one sequence")
        d = {"function": "minecraft:sequence", "functions": check_enchant_options(d)}
    if isinstance(d, dict):
        d = check_enchant_options([d])[0]
        return merge(d, new_function(d))
    return d


def convert_predicate(d):
    if isinstance(d, list):
        R.problem("the predicate file is a list: 26.3 wants one condition (minecraft:all_of), "
                  "1.21 to 26.2 read the list; that needs an overlay, so it was left as it is")
        return d
    if isinstance(d, dict):
        return merge(d, new_condition(d))
    return d


# --- Finding the files ---------------------------------------------------------------------------

def formats_cover(entry, fmt):
    """Does a pack.mcmeta overlay entry apply to data pack format `fmt`? None if it can't tell."""
    lo = hi = None
    f = entry.get("formats")
    if isinstance(f, int):
        lo = hi = f
    elif isinstance(f, list) and f:
        lo, hi = f[0], f[-1]
    elif isinstance(f, dict):
        lo, hi = f.get("min_inclusive"), f.get("max_inclusive")
    lo = entry.get("min_format", lo)
    hi = entry.get("max_format", hi)
    lo = lo[0] if isinstance(lo, list) else lo
    hi = hi[0] if isinstance(hi, list) else hi
    if lo is None or hi is None:
        return None
    return lo <= fmt <= hi


def skipped_overlays(resources):
    """Overlay folders under a resources root that 26.3 never reads."""
    meta = resources / "pack.mcmeta"
    out = set()
    if not meta.is_file():
        return out
    try:
        entries = json.loads(meta.read_text(encoding="utf-8-sig")).get("overlays", {}).get("entries", [])
    except (ValueError, AttributeError):
        return out
    for e in entries:
        if isinstance(e, dict) and e.get("directory") and formats_cover(e, NEW_FORMAT) is False:
            out.add((resources / e["directory"]).resolve())
    return out


def kind_of(path):
    """loot_table / item_modifier / predicate, 'old' for 1.20 folders, 'other' for other data, or
    None for files that aren't data at all."""
    parts = path.parts
    for name in reversed(parts[:-1]):
        if name in KINDS:
            return name
        if name in OLD_KINDS:
            return "old"
    return "other" if "data" in parts[:-1] else None


def sniff(path):
    """A JSON file outside any data/ layout (a copied folder, say) counts as a loot table when it
    has pools."""
    try:
        d = json.loads(path.read_bytes().decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError, OSError):
        return None
    return "loot_table" if isinstance(d, dict) and isinstance(d.get("pools"), list) else None


def data_root(path):
    for p in path.parents:
        if p.name == "data":
            return p
    return None


def collect(paths):
    files, skipped = [], {"1.20 folders": 0, "overlays 26.3 never reads": 0}
    seen = set()
    for p in paths:
        p = Path(p)
        if p.is_file():
            cands, root = [p], p.parent
        elif p.is_dir():
            root = p / "src" / "main" / "resources" if (p / "src" / "main" / "resources").is_dir() else p
            cands = sorted(x for x in root.rglob("*.json") if not SKIP_DIRS & set(x.relative_to(root).parts))
        else:
            raise SystemExit(f"not found: {p}")
        skip = skipped_overlays(root)
        for f in cands:
            r = f.resolve()
            if r in seen:
                continue
            seen.add(r)
            if any(s in r.parents for s in skip):
                skipped["overlays 26.3 never reads"] += 1
                continue
            k = kind_of(r) or sniff(r)
            if k == "old":
                skipped["1.20 folders"] += 1
            elif k:
                files.append((f, k))
    return files, skipped


OLD_DISPATCH = re.compile(r'"(function|condition)"\s*:\s*"')


def convert_file(text, kind):
    """(new data or None if nothing to do, CONFLICT handled) for one file's text."""
    data = json.loads(text)
    if kind == "loot_table":
        return convert_table(data) if isinstance(data, dict) else data
    if kind == "item_modifier":
        return convert_item_modifier(data)
    if kind == "predicate":
        return convert_predicate(data)
    if OLD_DISPATCH.search(text):
        R.problem("has old-form functions or conditions (\"function\"/\"condition\" ids), which "
                  "26.3 ignores; not a kind this script converts, so look at it by hand")
    return None  # other data is only looked at, never written


# --- Writing the change into the file's own text ---------------------------------------------
# Files are often laid out by hand. Rather than writing the whole file again, the new keys are
# put into the original text right after the key they come from, in the same layout as that key
# (on its own line if it is on its own line, on one line if its value is on one line, indented with
# the file's tabs or spaces), and nothing else in the file changes, a byte order mark included. A
# 26.3 key that goes is cut out with the comma before it.

class Node:
    __slots__ = ("start", "end", "items")

    def __init__(self, start, end, items=None):
        self.start, self.end, self.items = start, end, items  # items: [(key, key_start, Node)] or [Node]


def parse(text):
    """A JSON parser that keeps where each value starts and ends in the text."""
    ws = " \t\r\n"

    def skip(i):
        while i < len(text) and text[i] in ws:
            i += 1
        return i

    def string_end(i):
        i += 1
        while text[i] != '"':
            i += 2 if text[i] == "\\" else 1
        return i + 1

    def value(i):
        i = skip(i)
        c = text[i]
        if c == "{":
            items, j = [], skip(i + 1)
            if text[j] == "}":
                return Node(i, j + 1, items)
            while True:
                j = skip(j)
                k_end = string_end(j)
                key = json.loads(text[j:k_end])
                colon = skip(k_end)
                child = value(colon + 1)
                items.append((key, j, child))
                j = skip(child.end)
                if text[j] == "}":
                    return Node(i, j + 1, items)
                j += 1  # comma
        if c == "[":
            items, j = [], skip(i + 1)
            if text[j] == "]":
                return Node(i, j + 1, items)
            while True:
                child = value(j)
                items.append(child)
                j = skip(child.end)
                if text[j] == "]":
                    return Node(i, j + 1, items)
                j += 1
        if c == '"':
            return Node(i, string_end(i))
        j = i
        while j < len(text) and text[j] not in ws + ",]}":
            j += 1
        return Node(i, j)

    return value(0)


def compact(v, padded):
    """One-line JSON; padded=True writes { "a": 1 } and [ 1 ] like the hand-written tables."""
    o, c = ("{ ", " }") if padded else ("{", "}")
    lo, lc = ("[ ", " ]") if padded else ("[", "]")
    if isinstance(v, dict):
        if not v:
            return "{}"
        return o + ", ".join(f"{json.dumps(k)}: {compact(x, padded)}" for k, x in v.items()) + c
    if isinstance(v, list):
        if not v:
            return "[]"
        return lo + ", ".join(compact(x, padded) for x in v) + lc
    return json.dumps(v, ensure_ascii=False)


def line_start(text, i):
    return text.rfind("\n", 0, i) + 1


def render_like(text, sample, v, unit, nl):
    """Write v in the layout of `sample` (the old key's value node)."""
    old = text[sample.start:sample.end]
    if "\n" not in old:
        return compact(v, padded=len(old) > 1 and old[1] == " ")
    base = text[line_start(text, sample.start):sample.start]
    base = base[:len(base) - len(base.lstrip(" \t"))]
    return json.dumps(v, indent=unit, ensure_ascii=False).replace("\n", nl + base)


def plan_edits(text, node, want, unit, nl, edits):
    """Collect (start, end, new text) edits that turn node's text into `want`."""
    if isinstance(want, dict) and isinstance(node.items, list) and text[node.start] == "{":
        have = {k: (ks, n) for k, ks, n in node.items}
        if not want or next(iter(want)) not in have:
            # every key goes, or a new first key: write the whole object again
            if want or have:
                edits.append((node.start, node.end, render_like(text, node, want, unit, nl)))
            return
        items = node.items
        lead = 0
        while items[lead][0] not in want:  # the first key of `want` is in the text, so this stops
            lead += 1
        if lead:  # the first keys go: the first one kept moves up into their place
            edits.append((items[0][1], items[lead][1], ""))
        for i in range(lead + 1, len(items)):
            if items[i][0] not in want:  # a key goes, with the comma before it
                edits.append((items[i - 1][2].end, items[i][2].end, ""))
        prev, pending = None, []

        def flush():
            if pending:
                pks, pnode = have[prev]
                before = text[line_start(text, pks):pks]
                # the old key starts its own line: so do the new ones
                sep = "," + nl + before if before.strip() == "" else ", "
                edits.append((pnode.end, pnode.end, "".join(
                    f"{sep}{json.dumps(k)}: {render_like(text, pnode, v, unit, nl)}" for k, v in pending)))
                pending.clear()

        for k, v in want.items():
            if k in have:
                flush()
                plan_edits(text, have[k][1], v, unit, nl, edits)
                prev = k
            else:
                pending.append((k, v))
        flush()
        return
    if isinstance(want, list) and isinstance(node.items, list) and text[node.start] == "[" \
            and len(want) == len(node.items):
        for n, v in zip(node.items, want):
            plan_edits(text, n, v, unit, nl, edits)
        return
    if json.loads(text[node.start:node.end]) != want:
        edits.append((node.start, node.end, render_like(text, node, want, unit, nl)))


def write_like(text, want):
    """The file's text with the edits that make it `want`. Everything else, line endings
    included, stays byte for byte; new lines use the file's usual line ending, and new blocks its
    indent (tabs or spaces, from the first indented line)."""
    nl = "\r\n" if text.count("\r\n") * 2 > text.count("\n") else "\n"
    unit = "  "
    for line in text.splitlines()[1:]:
        stripped = line.lstrip(" \t")
        if stripped and len(stripped) < len(line):
            unit = line[:len(line) - len(stripped)]
            break
    edits = []
    plan_edits(text, parse(text), want, unit, nl, edits)
    t = text
    for start, end, new in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        t = t[:start] + new + t[end:]
    if json.loads(t) != want:
        raise RuntimeError("the edited text does not read back as intended")
    return t


def key_changes(old, new, out):
    """Count the keys `new` adds ("modifier") and drops ("modifier removed") next to `old`, by
    name (not looking inside added or dropped values)."""
    if isinstance(old, dict) and isinstance(new, dict):
        for k, v in new.items():
            if k in old:
                key_changes(old[k], v, out)
            else:
                out[k] = out.get(k, 0) + 1
        for k in old:
            if k not in new:
                out[k + " removed"] = out.get(k + " removed", 0) + 1
    elif isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
        for a, b in zip(old, new):
            key_changes(a, b, out)
    return out


# --- Command line ---------------------------------------------------------------------------------

def shown(f):
    try:
        return str(f.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(f)


def main(argv=None):
    global R, ADD_ENCHANT_OPTIONS, DATA_ROOT
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="See the top of this file for what changes and why.")
    ap.add_argument("paths", nargs="*", default=["."],
                    help="mod repos, data folders, loot folders or files (default: the current folder)")
    ap.add_argument("-n", "--dry-run", "--check", dest="dry", action="store_true",
                    help="change nothing; list what would change; exit 1 if anything would")
    ap.add_argument("--diff", action="store_true", help="with --dry-run, show each change")
    ap.add_argument("--add-enchant-options", action="store_true",
                    help=f'give enchant functions with no options "options": "{ON_RANDOM_LOOT}"')
    a = ap.parse_args(argv)
    ADD_ENCHANT_OPTIONS = a.add_enchant_options

    files, skipped = collect(a.paths)
    changed = 0
    problems = []
    notes_total = {}
    for f, kind in files:
        R = Report()
        DATA_ROOT = data_root(f.resolve())
        raw = f.read_bytes()
        bom = codecs.BOM_UTF8 if raw.startswith(codecs.BOM_UTF8) else b""  # written back as it was
        text = raw.decode("utf-8-sig")
        try:
            want = convert_file(text, kind)
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            R.problem(f"could not read it as {kind}: {e!r}")
            want = None
        if want is CONFLICT:
            R.problem("the two forms need different values for the same key, which only an overlay "
                      "can hold; left as it is")
            want = None
        new = text if want is None else write_like(text, want)
        for k, n in R.notes.items():
            notes_total[k] = notes_total.get(k, 0) + n
        problems += [(f, p) for p in R.problems]
        note = ""
        if R.notes:
            note = "  [" + ", ".join(f"{n} {k}" for k, n in R.notes.items()) + "]"
        if new == text:
            if a.dry and R.notes:
                print(f"already right: {shown(f)}{note}")
            continue
        changed += 1
        keys = key_changes(json.loads(text), want, {})
        what = ", ".join(f"{k} x{n}" for k, n in keys.items()) or "values changed"
        if a.dry:
            print(f"would update {shown(f)}: {what}{note}")
            if a.diff:
                sys.stdout.writelines(difflib.unified_diff(
                    text.replace("\r\n", "\n").splitlines(True), new.replace("\r\n", "\n").splitlines(True),
                    fromfile=shown(f), tofile=shown(f) + " (26.3 form added)"))
        else:
            f.write_bytes(bom + new.encode("utf-8"))
            print(f"updated {shown(f)}: {what}{note}")

    for f, p in problems:
        print(f"CHECK BY HAND {shown(f)}: {p}")
    kinds = {}
    for _, k in files:
        kinds[k] = kinds.get(k, 0) + 1
    print("looked at " + (", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) or "no data files")
          + "".join(f"; skipped {n} in {k}" for k, n in skipped.items() if n))
    verb = "would change" if a.dry else "changed"
    print(f"{changed} {verb}, {len(files) - changed} left as they are"
          + (f", {len(problems)} to check by hand" if problems else ""))
    if notes_total.get("enchant functions without options"):
        n = notes_total["enchant functions without options"]
        print(f"note: {n} enchant functions have no options, so they can roll any enchantment "
              f"(curses, treasure ones, other mods'); the same on every version. "
              + ("They now have " + ON_RANDOM_LOOT + "." if ADD_ENCHANT_OPTIONS
                 else "--add-enchant-options gives them " + ON_RANDOM_LOOT + ", as vanilla chests have."))
    if notes_total.get(STALE):
        print(f"note: {notes_total[STALE]} 26.3 keys (modifier, condition, a tag entry's items) had no old key "
              f"beside them, deleted from the old form or never written there, so "
              f"{'they would be' if a.dry else 'they were'} removed and every version reads the same. "
              f"To keep one, write it in the old form and run this again.")
    if notes_total.get("ids without a namespace"):
        print(f"note: {notes_total['ids without a namespace']} type ids have no namespace; every "
              f"version reads them as minecraft:, and the 26.3 form writes it out.")
    return 1 if problems or (a.dry and changed) else 0


if __name__ == "__main__":
    sys.exit(main())
