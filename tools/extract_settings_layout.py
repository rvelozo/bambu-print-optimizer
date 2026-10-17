"""Extract Bambu Studio's Process settings layout for the app's setting picker.

Reads two files from the Bambu Studio source at the version the app slices with:

    src/slic3r/GUI/Tab.cpp          TabPrint::build() and TabFilament::build(): pages,
                                    sections, settings in order
    src/libslic3r/PrintConfig.cpp   each setting's label, unit, tooltip, type, choices

and writes slicer_optimizer/settings_layout.json. Every line of the Process tab
is included, as Bambu Studio shows them with Develop mode on, and the Filament
tab's lines, to name filament settings.

    python3 tools/extract_settings_layout.py Tab.cpp PrintConfig.cpp VERSION > slicer_optimizer/settings_layout.json

Get the sources with, for example:
    curl -O https://raw.githubusercontent.com/bambulab/BambuStudio/v02.08.04.57/src/slic3r/GUI/Tab.cpp
"""

from __future__ import annotations

import json
import re
import sys

STRING = r'"(?:[^"\\]|\\.)*"'
TEXT = rf'(?:L\(\s*(?:{STRING}\s*)+\)|(?:{STRING}\s*)+)'  # L("a" "b") or "a" "b"
SKIP_GROUPS = {"G-code output", "Post-processing scripts", "Notes"}  # free text and scripts: nothing to try


def _text(expr: str) -> str:
    parts = re.findall(STRING, expr)
    return "".join(json.loads(p) for p in parts)


def _strip_comments(code: str) -> str:
    out, in_if0 = [], 0
    for line in code.splitlines():
        s = line.strip()
        if s.startswith("#if 0") or s.startswith("#ifdef HAS_PRESSURE_EQUALIZER"):
            in_if0 += 1
            continue
        if in_if0 and s.startswith("#endif"):
            in_if0 -= 1
            continue
        if in_if0 or s.startswith("//"):
            continue
        out.append(re.sub(r";\s*//.*$", ";", line))
    return "\n".join(out)


def layout(tab_cpp: str, tab: str = "TabPrint") -> list[dict]:
    """Pages, sections and setting keys of a tab's build(), in order."""
    start = tab_cpp.index(f"void {tab}::build()")
    end = tab_cpp.index(f"\nvoid {tab}::", start + 10)
    code = _strip_comments(tab_cpp[start:end])
    pages: list[dict] = []
    line_label = None
    token = re.compile(rf'add_options_page\(\s*({TEXT})|new_optgroup\(\s*({TEXT})|'
                       rf'append_single_option_line\(\s*"([\w]+)"|(?:Line\s+)?\bline\s*=\s*\{{\s*({TEXT})|'
                       rf'line\.append_option\(\s*optgroup->get_option\(\s*"([\w]+)"|'
                       rf'Option\s+option\s*=\s*optgroup->get_option\(\s*"([\w]+)"|'
                       rf'auto\s+(\w+)\s*=\s*optgroup->get_option\(\s*"([\w]+)"|'
                       rf'line\.append_option\(\s*(\w+)\s*\)|'
                       rf'append_single_option_line\(\s*option\s*[,)]')
    option_key = None  # Option option = optgroup->get_option("filament_type"); ... append_single_option_line(option)
    named: dict[str, str] = {}  # auto opt_speed = optgroup->get_option("..."); ... line.append_option(opt_speed)
    for m in token.finditer(code):
        page, group, single, line, in_line, opt_var, var, var_key, var_used = m.groups()
        if opt_var:
            option_key = opt_var
            continue
        if var:
            named[var] = var_key
            continue
        if var_used:
            if var_used in named:
                pages[-1]["groups"][-1]["options"].append({"key": named[var_used], "line": line_label})
            continue
        if not any(m.groups()):  # append_single_option_line(option)
            if option_key and pages and pages[-1]["groups"]:
                pages[-1]["groups"][-1]["options"].append({"key": option_key})
            option_key = None
            continue
        if page:
            pages.append({"title": _text(page), "groups": []})
        elif group:
            pages[-1]["groups"].append({"title": _text(group), "options": []})
        elif single:
            pages[-1]["groups"][-1]["options"].append({"key": single})
        elif line:
            line_label = _text(line)
        elif in_line:
            pages[-1]["groups"][-1]["options"].append({"key": in_line, "line": line_label})
    for p in pages:
        p["groups"] = [g for g in p["groups"] if g["title"] not in SKIP_GROUPS and g["options"]]
    return [p for p in pages if p["groups"]]


def definitions(config_cpp: str) -> dict[str, dict]:
    """Label, unit, tooltip, type, limits and choices of each setting."""
    code = _strip_comments(config_cpp)
    starts = [(m.start(), m.group(2), m.group(3), m.group(1)) for m in
              re.finditer(r'(?:auto\s+(\w+)\s*=\s*)?def\s*=\s*this->add(?:_nullable)?\(\s*"(\w+)"\s*,\s*(co\w+)\s*\)', code)]
    out: dict[str, dict] = {}
    var_key: dict[str, str] = {}   # auto def_top_fill_pattern = def = this->add("top_surface_pattern", ...)
    copies: dict[str, str] = {}    # def->enum_values = def_top_fill_pattern->enum_values
    for i, (pos, key, ctype, var) in enumerate(starts):
        if var:
            var_key[var] = key
        block = code[pos:starts[i + 1][0] if i + 1 < len(starts) else len(code)]
        d: dict = {"type": ctype}
        for field in ("label", "full_label", "tooltip", "sidetext", "category"):
            m = re.search(rf"def->{field}\s*=\s*({TEXT})\s*;", block)
            if m:
                d[field] = _text(m.group(1))
        m = re.search(r"def->mode\s*=\s*com(\w+)", block)
        if m:
            d["mode"] = m.group(1).lower()
        for field in ("min", "max"):
            m = re.search(rf"def->{field}\s*=\s*(-?[\d.]+)", block)
            if m:
                d[field] = float(m.group(1))
        values = [json.loads(v) for v in re.findall(rf"def->enum_values\.(?:push|emplace)_back\(\s*({STRING})\s*\)", block)]
        labels = [_text(v) for v in re.findall(rf"def->enum_labels\.(?:push|emplace)_back\(\s*({TEXT})\s*\)", block)]
        m = re.search(r"def->enum_values\s*=\s*(\w+)->enum_values", block)
        if m and not values:
            copies[key] = m.group(1)
        if values:
            d["choices"] = [[v, labels[j] if j < len(labels) else v] for j, v in enumerate(values)]
        out.setdefault(key, d)  # the first definition wins
    for key, var in copies.items():
        src = out.get(var_key.get(var, ""), {})
        if src.get("choices") and not out[key].get("choices"):
            out[key]["choices"] = src["choices"]
    return out


def main(tab_path: str, config_path: str, version: str) -> None:
    tab_cpp = open(tab_path, encoding="utf-8").read()
    pages = layout(tab_cpp)
    filament_pages = layout(tab_cpp, "TabFilament")
    defs = definitions(open(config_path, encoding="utf-8").read())
    keys = lambda ps: {o["key"] for p in ps for g in p["groups"] for o in g["options"]}
    # filament settings the tab builds outside the page code (the auxiliary fan, the overrides page): named only
    region = tab_cpp[tab_cpp.index("void TabFilament::"):tab_cpp.index("void TabPrinter::build()")]
    named = {k for k in re.findall(r'"(\w+)"', _strip_comments(region)) if k in defs}
    used = keys(pages) | keys(filament_pages) | named
    missing = sorted(k for k in used if k not in defs)
    json.dump({"bambu_studio": version, "source": "BambuStudio src/slic3r/GUI/Tab.cpp (TabPrint::build, "
               "TabFilament::build) and src/libslic3r/PrintConfig.cpp", "pages": pages,
               "filament_pages": filament_pages,
               "settings": {k: defs[k] for k in sorted(used) if k in defs}, "undefined": missing},
              sys.stdout, indent=1, ensure_ascii=False)
    sys.stdout.write("\n")
    for name, ps in (("Process", pages), ("Filament", filament_pages)):
        print(f"{name}: {len(ps)} pages, {sum(len(p['groups']) for p in ps)} sections, {len(keys(ps))} settings",
              file=sys.stderr)
    print(f"{len(missing)} without a definition", file=sys.stderr)


if __name__ == "__main__":
    main(*sys.argv[1:4])
