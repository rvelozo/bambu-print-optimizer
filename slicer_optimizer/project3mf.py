"""Read and write the settings inside a Bambu Studio .3mf project.

Print-wide settings live in Metadata/project_settings.config (JSON, one key per
setting, ~600 keys). Per-object and per-part overrides live in
Metadata/model_settings.config (XML, <metadata key=... value=...> under each
<object> and <part>). Writing a variant edits only those two entries; every
other zip entry is copied with its content, name, date and compression intact.
"""

from __future__ import annotations

import copy
import io
import json
import os
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

PROJECT_SETTINGS = "Metadata/project_settings.config"
MODEL_SETTINGS = "Metadata/model_settings.config"

# Object/part metadata that describes the mesh rather than overriding a setting.
DESCRIPTIVE_KEYS = {
    "name", "extruder", "matrix", "source_file", "source_object_id", "source_volume_id",
    "source_offset_x", "source_offset_y", "source_offset_z", "source_in_inches", "source_in_meters",
}
# Plate metadata that is bookkeeping, not a print setting.
PLATE_BOOKKEEPING = {
    "plater_id", "plater_name", "locked", "thumbnail_file", "thumbnail_no_light_file",
    "top_file", "pick_file", "filament_map_mode", "filament_maps", "filament_volume_maps",
}


class ProjectError(Exception):
    pass


@dataclass
class Part:
    id: str
    name: str
    overrides: dict[str, str]


@dataclass
class Obj:
    id: str
    name: str
    extruder: str | None
    overrides: dict[str, str]
    parts: list[Part] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"object {self.id} ({self.name})"


@dataclass
class Plate:
    index: int
    name: str
    object_ids: list[str]  # one entry per instance, so a repeated id means copies
    settings: dict[str, str]


@dataclass(frozen=True)
class Change:
    """Set (or with value None, remove) one setting.

    object_id None means print-wide; removing a print-wide key is not allowed.
    """

    key: str
    value: str | None
    object_id: str | None = None


class Project:
    def __init__(self, path: str | os.PathLike, presets=None, data: bytes | None = None):
        """`data`: the file's bytes, read once. Reading and rebuilding then use this
        snapshot even if the file changes on disk (Bambu Studio saving over it)."""
        self.path = Path(path)
        self._presets = presets
        self._data = data
        self.notes: list[str] = []  # remarks from the last build()
        try:
            with zipfile.ZipFile(self._source()) as z:
                names = set(z.namelist())
                if PROJECT_SETTINGS not in names or MODEL_SETTINGS not in names:
                    raise ProjectError(f"{self.path.name} is not a Bambu Studio project (no {PROJECT_SETTINGS} or {MODEL_SETTINGS})")
                self._print_raw = z.read(PROJECT_SETTINGS)
                self._model_raw = z.read(MODEL_SETTINGS)
        except zipfile.BadZipFile as e:
            raise ProjectError(f"{self.path.name} is not a .3mf (zip) file: {e}") from e
        self.print_settings: dict = json.loads(self._print_raw)
        self._model_xml = self._model_raw.decode("utf-8")
        self.objects, self.plates = _parse_model(self._model_xml)

    def _source(self):
        return io.BytesIO(self._data) if self._data is not None else self.path

    def source_bytes(self) -> bytes:
        """The project file as loaded (the snapshot, or the file as it is now)."""
        return self._data if self._data is not None else self.path.read_bytes()

    # ---- reading -------------------------------------------------------------

    @property
    def studio_version(self) -> str | None:
        return self.print_settings.get("version")

    @property
    def presets(self):
        """System presets this project is based on (empty if they can't be found)."""
        if self._presets is None:
            from .presets import SystemPresets
            self._presets = SystemPresets(self.print_settings)
        return self._presets

    def is_system_default(self, key: str, value) -> bool | None:
        default = self.presets.process_default(key)
        return None if default is None else _as_list(value) == _as_list(default)

    def object(self, object_id: str) -> Obj:
        for o in self.objects:
            if o.id == object_id:
                return o
        raise ProjectError(f"no object with id {object_id}")

    def plate(self, index: int) -> Plate:
        for p in self.plates:
            if p.index == index:
                return p
        raise ProjectError(f"no plate {index} (plates: {', '.join(str(p.index) for p in self.plates)})")

    def objects_on_plate(self, index: int) -> list[Obj]:
        ids = list(dict.fromkeys(self.plate(index).object_ids))
        return [self.object(i) for i in ids]

    def plate_of(self, object_id: str) -> int | None:
        for p in self.plates:
            if object_id in p.object_ids:
                return p.index
        return None

    def find_objects(self, ref: str) -> list[Obj]:
        """Objects matching an id or a name (names can repeat, e.g. two rails)."""
        by_id = [o for o in self.objects if o.id == ref]
        return by_id or [o for o in self.objects if o.name == ref]

    def has_key(self, key: str) -> bool:
        return key in self.print_settings

    def effective(self, key: str, obj: Obj | None = None) -> tuple[str | list | None, str]:
        """Value that applies to obj (or print-wide), and where it comes from."""
        if obj is not None:
            part_vals = {p.overrides[key] for p in obj.parts if key in p.overrides}
            if part_vals:
                return (", ".join(sorted(part_vals)), "part")
            if key in obj.overrides:
                return (obj.overrides[key], "object")
        return (self.print_settings.get(key), "print-wide")

    def variant_index(self) -> int:
        """Which entry of a per-extruder-variant list applies (P2S: Standard, High Flow, E3D High Flow)."""
        ps = self.print_settings
        variants = ps.get("print_extruder_variant") or []
        want = f"{(ps.get('extruder_type') or ['Direct Drive'])[0]} {(ps.get('nozzle_volume_type') or ['Standard'])[0]}"
        return variants.index(want) if want in variants else 0

    def active(self, value):
        """The value that applies to this printer: one entry of a per-variant list, else the value itself."""
        if not isinstance(value, list):
            return value
        n = len(self.print_settings.get("print_extruder_variant") or [])
        if n > 1 and len(value) == n:
            return value[self.variant_index()]
        return value[0] if len(set(map(str, value))) == 1 else value

    def filament_value(self, key: str, obj: Obj):
        """A filament setting's value for the object's filament slot and the active variant."""
        value = self.print_settings.get(key)
        names = self.print_settings.get("filament_settings_id") or []
        if not isinstance(value, list) or not names:
            return value
        slot = int(obj.extruder or "1") - 1
        nvar = len(self.print_settings.get("print_extruder_variant") or []) or 1
        if len(value) == len(names) * nvar:
            return value[slot * nvar + self.variant_index()]
        if len(value) == len(names):
            return value[slot]
        return self.active(value)

    def filament_for(self, obj: Obj) -> dict:
        """Filament preset name and max volumetric speed for the object's extruder slot."""
        slot = int(obj.extruder or "1") - 1
        names = self.print_settings.get("filament_settings_id") or []
        flows = self.print_settings.get("filament_max_volumetric_speed") or []
        nvar = len(self.print_settings.get("print_extruder_variant") or []) or 1
        idx = slot * nvar + self.variant_index() if names and len(flows) == len(names) * nvar else slot
        return {
            "slot": slot + 1,
            "name": names[slot] if slot < len(names) else None,
            "max_flow": flows[idx] if idx < len(flows) else None,
        }

    def modified_vs_system(self) -> dict[str, list[str]]:
        """Keys the project changed from the system presets, as Bambu Studio records them."""
        raw = self.print_settings.get("different_settings_to_system") or []
        if not raw:
            return {}
        groups = {"process": raw[0]}
        for i, s in enumerate(raw[1:-1], start=1):
            groups[f"filament {i}"] = s
        if len(raw) > 1:
            groups["printer"] = raw[-1]
        return {k: [x for x in v.split(";") if x] for k, v in groups.items() if v}

    # ---- writing -------------------------------------------------------------

    def build(self, changes: list[Change]) -> bytes:
        """The project with changes applied, as .3mf bytes. Deterministic for equal input."""
        cfg = None
        xml = self._model_xml
        self.notes = []
        for ch in changes:
            if ch.object_id is None:
                if ch.value is None:
                    raise ProjectError(f"cannot remove print-wide setting {ch.key}")
                if cfg is None:
                    cfg = copy.deepcopy(self.print_settings)
                _set_print_wide(cfg, ch.key, ch.value, self.presets, self.notes)
            else:
                self.object(ch.object_id)  # raises for an unknown id
                xml = _set_object_setting(xml, ch.object_id, ch.key, ch.value)
        replaced = {}
        if cfg is not None:
            replaced[PROJECT_SETTINGS] = (json.dumps(cfg, indent=4, ensure_ascii=False) + "\n").encode("utf-8")
        if xml != self._model_xml:
            replaced[MODEL_SETTINGS] = xml.encode("utf-8")
        buf = io.BytesIO()
        with zipfile.ZipFile(self._source()) as zin, zipfile.ZipFile(buf, "w") as zout:
            for info in zin.infolist():
                data = replaced.get(info.filename)
                if data is None:
                    data = zin.read(info.filename)
                zout.writestr(info, data)
        return buf.getvalue()

    def save(self, dest: str | os.PathLike, changes: list[Change], overwrite: bool = False) -> Path:
        dest = Path(dest)
        if dest.exists() and dest.resolve() == self.path.resolve():
            raise ProjectError("refusing to overwrite the source project; pick another file name")
        if dest.exists() and not overwrite:
            raise ProjectError(f"{dest} already exists")
        return write_bytes_atomic(dest, self.build(changes))


def write_bytes_atomic(dest: Path, data: bytes) -> Path:
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)
    return dest


# ---- helpers -----------------------------------------------------------------


def _metadata(elem) -> dict[str, str]:
    return {m.get("key"): m.get("value", "") for m in elem.findall("metadata") if m.get("key") is not None}


def _parse_model(xml: str) -> tuple[list[Obj], list[Plate]]:
    root = ET.fromstring(xml)
    objects = []
    for o in root.findall("object"):
        md = _metadata(o)
        parts = []
        for p in o.findall("part"):
            pmd = _metadata(p)
            parts.append(Part(p.get("id", ""), pmd.get("name", ""),
                              {k: v for k, v in pmd.items() if k not in DESCRIPTIVE_KEYS}))
        objects.append(Obj(o.get("id", ""), md.get("name", ""), md.get("extruder"),
                           {k: v for k, v in md.items() if k not in DESCRIPTIVE_KEYS}, parts))
    plates = []
    for pl in root.findall("plate"):
        md = _metadata(pl)
        ids = []
        for mi in pl.findall("model_instance"):
            imd = _metadata(mi)
            if "object_id" in imd:
                ids.append(imd["object_id"])
        plates.append(Plate(int(md.get("plater_id", len(plates) + 1)), md.get("plater_name", ""), ids,
                            {k: v for k, v in md.items() if k not in PLATE_BOOKKEEPING}))
    return objects, plates


def _as_list(v) -> list[str]:
    return [str(x) for x in v] if isinstance(v, list) else [str(v)]


def _set_print_wide(cfg: dict, key: str, value: str, presets, notes: list[str]) -> None:
    if key not in cfg:
        raise ProjectError(f"unknown setting {key!r}")
    old = cfg[key]
    new = [value] * len(old) if isinstance(old, list) else value
    cfg[key] = new
    # Keep Bambu Studio's own record of which process settings differ from the system preset.
    diff = cfg.get("different_settings_to_system")
    if not isinstance(diff, list) or not diff or old == new:
        return
    group = presets.group(key) if presets is not None else None
    if group == "process":
        keys = [k for k in diff[0].split(";") if k]
        differs = _as_list(new) != _as_list(presets.process_default(key))
        if differs and key not in keys:
            keys.append(key)
        elif not differs and key in keys:
            keys.remove(key)
        diff[0] = ";".join(sorted(keys))
    else:
        notes.append(f"{key} is a {group or 'non-system'} setting; Bambu Studio's list of changed "
                     "settings was left as is. Check that it shows as changed when you open the file.")


_OBJECT_RE = r'(<object id="{id}">)(.*?)(</object>)'
_META_ELEM = re.compile(r'([ \t]*)<metadata key="([^"]+)" value="[^"]*"\s*/>')


def _set_object_setting(xml: str, object_id: str, key: str, value: str | None) -> str:
    """Set, replace or remove one per-object override in model_settings.config.

    Works on the object's own <metadata> elements wherever the line breaks are:
    Bambu Studio writes one per line (kept byte for byte), files made by other
    tools may put the whole config on one line.
    """
    if key in DESCRIPTIVE_KEYS:
        raise ProjectError(f"{key!r} is object metadata, not a setting")
    m = re.search(_OBJECT_RE.format(id=re.escape(object_id)), xml, flags=re.S)
    if not m:
        raise ProjectError(f"object {object_id} not found in {MODEL_SETTINGS}")
    body = m.group(2)
    head, sep, tail = body.partition("<part")
    elems = list(_META_ELEM.finditer(head))  # the object's own metadata, before its parts
    multiline = "\n" in head
    existing = [e for e in elems if e.group(2) == key]
    if value is None:
        for e in reversed(existing):
            a, b = e.span()
            if multiline and head[b:b + 1] == "\n":
                b += 1  # the whole line goes
            head = head[:a] + head[b:]
    else:
        elem = f'<metadata key="{key}" value="{escape(value, {chr(34): "&quot;"})}"/>'
        if existing:
            e = existing[0]
            head = head[:e.start(2) - len('<metadata key="')] + elem + head[e.end():]
        else:
            name = next((e for e in elems if e.group(2) == "name"), None)
            if name is None:
                raise ProjectError(f"object {object_id} has no name metadata; refusing to edit")
            indent = name.group(1) if multiline else ""
            # keep Bambu's alphabetical order of the keys after the name ("extruder" included)
            after = [e for e in elems if e.start() > name.start()]
            before = next((e for e in after if e.group(2) > key), None)
            if before is not None:
                at = before.start()
                head = head[:at] + indent + elem + ("\n" if multiline else "") + head[at:]
            else:
                at = (after[-1] if after else name).end()
                head = head[:at] + ("\n" if multiline else "") + indent + elem + head[at:]
    new_body = head + sep + tail
    return xml[: m.start(2)] + new_body + xml[m.end(2):]
