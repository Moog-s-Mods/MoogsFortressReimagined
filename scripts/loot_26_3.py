#!/usr/bin/env python3
"""
Give loot tables the Minecraft 26.3 form next to the old one, so one file works on 1.21 to 26.3.

Minecraft 26.3 changed how loot tables are written, and it ignores the old keys without any
error: a table written for 1.21 to 26.2 loads on 26.3 with its functions and conditions gone
(stack counts, enchanting, damage, random chances). Read from the 26.3 jar's own tables and code:

    old (1.21 to 26.2)                          26.3
    "functions": [ {...}, ... ]                 "modifier": {...}   (or a list when several)
    "conditions": [ {...}, ... ]                "condition": {...}  (several: minecraft:all_of)
    a function's "function": "minecraft:x"      "type": "minecraft:x"
    a function's "conditions": [...]            "condition": {...}
    a condition's "condition": "minecraft:x"    "type": "minecraft:x"

These apply on tables, pools and entries (and an entry's children). Older games never read
"modifier" or "condition" there, and 26.3 never reads "functions" or "conditions", so this script
keeps the old keys as they are and writes the 26.3 keys beside them, built from the old ones.
The old keys stay the source: edit those, then run this again to refresh the 26.3 copy.

26.3 also renamed a few things inside them, which the 26.3 copy gets:
    condition block_state_property {block, properties}   -> match_block {blocks, state}
    condition or function "reference" {name}             -> the name alone, as a string
    damage_source_properties tag ids                     -> with a "#" ("#minecraft:is_fire")
    exploration_map without a destination                -> "#minecraft:on_treasure_maps", the old
                                                            default (26.3 requires it)
    tag entry "name": "x"                                -> "items": "#x" (beside "name")
Things it cannot convert safely (value_check, the "sum" number provider) are reported.

Usage (from the project root):
    python scripts/loot_26_3.py                 # every loot_table folder under src/main/resources/data
    python scripts/loot_26_3.py <folder|file> ...
    python scripts/loot_26_3.py --check [...]   # change nothing; exit 1 if any table needs this script

It works on any folder of loot table JSON, so it can be pointed at another mod's tables.
Only the 1.21 branches need it: 1.20 never runs on 26.3.
"""
import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

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

problems = []  # (file, message), filled while converting
current_file = ""


def problem(msg):
    if (current_file, msg) not in problems:
        problems.append((current_file, msg))


def short(type_id):
    return type_id.split(":", 1)[1] if isinstance(type_id, str) and ":" in type_id else type_id


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
            problem(f"{t}: {NOT_CONVERTED[t]}; copied as it is, fix it by hand")
        return {k: plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [plain(x) for x in v]
    return v


def new_condition(c):
    """One old-form condition -> 26.3 form."""
    if isinstance(c, str):  # already a reference to a predicate file
        return c
    if isinstance(c, list):  # a list in a condition's place means all of them
        return new_condition_list(c)
    ctype = c.get("condition", c.get("type"))
    sid = short(ctype)
    if sid == "reference":
        return c["name"]
    if ctype in NOT_CONVERTED:
        problem(f"{ctype}: {NOT_CONVERTED[ctype]}; copied as it is, fix it by hand")
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
    ftype = f.get("function", f.get("type"))
    sid = short(ftype)
    if sid == "reference":
        if f.get("conditions"):
            problem(f"a reference function with conditions ({f['name']}): 26.3 references carry "
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
    if sid == "exploration_map" and "destination" not in out:
        out["destination"] = "#minecraft:on_treasure_maps"
    return out


def new_function_list(funcs):
    """An old "functions" list -> the 26.3 "modifier" value (None when empty)."""
    funcs = [new_function(f) for f in funcs]
    if not funcs:
        return None
    if len(funcs) == 1:
        return funcs[0]
    return funcs


def with_new_keys(obj, extra=None):
    """Return obj with "modifier"/"condition" (re)built from "functions"/"conditions", each right
    after its old key, and any key in `extra` ({new key: (after key, value)}) likewise.
    A 26.3 key with no old key to come from is kept as it is (a hand-written 26.3 table)."""
    extra = extra or {}
    from_old = {"modifier": "functions" in obj, "condition": "conditions" in obj}
    if not any(from_old.values()) and not extra:
        return obj
    out = {}
    for k, v in obj.items():
        if from_old.get(k) or k in extra:
            continue  # rebuilt below, after the key it comes from
        out[k] = v
        if k == "functions":
            m = new_function_list(v)
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
    extra = {}
    if short(e.get("type")) == "tag" and "name" in e:
        extra["items"] = ("name", tagged(e["name"]))
    e = with_new_keys(e, extra)
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

    t = drop_old(with_new_keys(t))
    if "pools" in t:
        pools = []
        for p in t["pools"]:
            p = dict(p)
            if "entries" in p:
                p["entries"] = [convert_entry(dict(e), only_new) for e in p["entries"]]
            pools.append(drop_old(with_new_keys(p)))
        t["pools"] = pools
    return t


# --- Writing the change into the file's own text ---------------------------------------------
# Tables are often laid out by hand. Rather than writing the whole file again, the new keys are
# put into the original text right after the key they come from, in the same layout as that key
# (on its own line if it is on its own line, on one line if its value is on one line), and nothing
# else in the file changes.

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


def render_like(text, sample, v, unit):
    """Write v in the layout of `sample` (the old key's value node)."""
    old = text[sample.start:sample.end]
    if "\n" not in old:
        return compact(v, padded=len(old) > 1 and old[1] == " ")
    base = text[line_start(text, sample.start):sample.start]
    base = base[:len(base) - len(base.lstrip(" "))]
    return json.dumps(v, indent=unit, ensure_ascii=False).replace("\n", "\n" + base)


def plan_edits(text, node, want, unit, edits):
    """Collect (start, end, new text) edits that turn node's text into `want`."""
    if isinstance(want, dict) and isinstance(node.items, list) and text[node.start] == "{":
        have = {k: (ks, n) for k, ks, n in node.items}
        if any(k not in want for k in have):  # a key would go: write the whole object again
            edits.append((node.start, node.end, render_like(text, node, want, unit)))
            return
        prev = None
        for k, v in want.items():
            if k in have:
                plan_edits(text, have[k][1], v, unit, edits)
                prev = k
                continue
            if prev is None:  # a new first key: not something this script makes
                edits.append((node.start, node.end, render_like(text, node, want, unit)))
                return
            pks, pnode = have[prev]
            before = text[line_start(text, pks):pks]
            if before.strip() == "":  # the old key starts its own line: so does the new one
                sep = ",\n" + before
            else:
                sep = ", "
            edits.append((pnode.end, pnode.end, f"{sep}{json.dumps(k)}: {render_like(text, pnode, v, unit)}"))
        return
    if isinstance(want, list) and isinstance(node.items, list) and text[node.start] == "[" \
            and len(want) == len(node.items):
        for n, v in zip(node.items, want):
            plan_edits(text, n, v, unit, edits)
        return
    if json.loads(text[node.start:node.end]) != want:
        edits.append((node.start, node.end, render_like(text, node, want, unit)))


def write_like(text, want):
    """The file's text with the edits that make it `want`, keeping its layout and line endings."""
    crlf = "\r\n" in text
    t = text.replace("\r\n", "\n")
    unit = 2
    for line in t.splitlines()[1:]:
        stripped = line.lstrip(" ")
        if stripped and len(stripped) < len(line):
            unit = len(line) - len(stripped)
            break
    edits = []
    plan_edits(t, parse(t), want, unit, edits)
    for start, end, new in sorted(edits, reverse=True):
        t = t[:start] + new + t[end:]
    if json.loads(t) != want:
        raise RuntimeError("the edited text does not read back as intended")
    return t.replace("\n", "\r\n") if crlf else t


def default_folders():
    data = PROJECT_ROOT / "src" / "main" / "resources" / "data"
    return sorted(p for p in data.glob("*/loot_table") if p.is_dir())


def main():
    global current_file
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("paths", nargs="*", help="loot table folders or files (default: every "
                    "src/main/resources/data/*/loot_table)")
    ap.add_argument("--check", action="store_true", help="change nothing; exit 1 if a table needs updating")
    a = ap.parse_args()

    paths = [Path(p) for p in a.paths] or default_folders()
    files = []
    for p in paths:
        if p.is_dir():
            files += sorted(p.rglob("*.json"))
        elif p.is_file():
            files.append(p)
        else:
            print(f"not found: {p}", file=sys.stderr)
            return 2

    changed = same = 0
    for f in files:
        rel = f.resolve().relative_to(PROJECT_ROOT) if f.resolve().is_relative_to(PROJECT_ROOT) else f
        current_file = str(rel)
        text = f.read_bytes().decode("utf-8-sig")
        data = json.loads(text)
        if not isinstance(data, dict):
            continue
        new = write_like(text, convert_table(data))
        if new == text:
            same += 1
            continue
        changed += 1
        if a.check:
            print(f"needs the 26.3 form: {rel}")
        else:
            f.write_bytes(new.encode("utf-8"))
            print(f"updated {rel}")

    for file, msg in problems:
        print(f"CHECK BY HAND {file}: {msg}")
    verb = "need updating" if a.check else "updated"
    print(f"{len(files)} files, {changed} {verb}, {same} already right"
          + (f", {len(problems)} to check by hand" if problems else ""))
    return 1 if (a.check and changed) or problems else 0


if __name__ == "__main__":
    sys.exit(main())
