"""A small synthetic Bambu project shaped like the real ones (same file names, same XML layout)."""

import json
import zipfile
from pathlib import Path

PRINT_SETTINGS = {
    "bridge_speed": ["50", "50", "50"],
    "different_settings_to_system": ["enable_arc_fitting", "", "", ""],
    "enable_support": "0",
    "filament_max_volumetric_speed": ["16", "12"],
    "filament_settings_id": ["PLA A", "PLA B"],
    "initial_layer_infill_speed": ["105", "105", "105"],
    "print_settings_id": "0.20mm Standard @BBL P2S",
    "printer_settings_id": "Bambu Lab P2S 0.4 nozzle",
    "small_perimeter_speed": ["50%", "50%", "50%"],
    "sparse_infill_density": "15%",
    "top_shell_layers": "5",
    "version": "02.08.04.57",
}

MODEL_SETTINGS = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <object id="6">
    <metadata key="name" value="rail.stl"/>
    <metadata key="extruder" value="1"/>
    <metadata face_count="920"/>
    <part id="5" subtype="normal_part" uuid="0b0f36f2">
      <metadata key="name" value="rail.stl"/>
      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>
    </part>
  </object>
  <object id="4">
    <metadata key="name" value="lid.stl"/>
    <metadata key="extruder" value="1"/>
    <metadata key="sparse_infill_density" value="12%"/>
    <metadata face_count="324"/>
    <part id="3" subtype="normal_part" uuid="b9677e2f">
      <metadata key="name" value="lid.stl"/>
      <metadata key="small_perimeter_speed" value="80%,nil,nil"/>
    </part>
  </object>
  <object id="7">
    <metadata key="name" value="rail.stl"/>
    <metadata key="extruder" value="1"/>
    <metadata face_count="920"/>
    <part id="5" subtype="normal_part" uuid="a25f8d81">
      <metadata key="name" value="rail.stl"/>
    </part>
  </object>
  <plate>
    <metadata key="plater_id" value="1"/>
    <metadata key="plater_name" value=""/>
    <metadata key="locked" value="false"/>
    <model_instance>
      <metadata key="object_id" value="4"/>
      <metadata key="instance_id" value="0"/>
    </model_instance>
  </plate>
  <plate>
    <metadata key="plater_id" value="2"/>
    <metadata key="plater_name" value="rails"/>
    <metadata key="locked" value="false"/>
    <model_instance>
      <metadata key="object_id" value="6"/>
      <metadata key="instance_id" value="0"/>
    </model_instance>
    <model_instance>
      <metadata key="object_id" value="7"/>
      <metadata key="instance_id" value="0"/>
    </model_instance>
  </plate>
  <assemble>
   <assemble_item object_id="6" instance_id="0" transform="1 0 0 0 1 0 0 0 1 0 0 18" offset="0 0 0" />
  </assemble>
</config>
"""


def make_project(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(zipfile.ZipInfo("[Content_Types].xml", (2026, 10, 12, 12, 52, 0)),
                   b'<?xml version="1.0"?><Types/>', compress_type=zipfile.ZIP_DEFLATED)
        z.writestr(zipfile.ZipInfo("Metadata/plate_1.png", (2026, 10, 12, 12, 52, 0)),
                   bytes(range(256)) * 8, compress_type=zipfile.ZIP_STORED)
        z.writestr(zipfile.ZipInfo("3D/3dmodel.model", (2026, 10, 12, 12, 52, 0)),
                   b"<model>" + b"v" * 5000 + b"</model>", compress_type=zipfile.ZIP_DEFLATED)
        z.writestr(zipfile.ZipInfo("Metadata/project_settings.config", (2026, 10, 12, 12, 52, 0)),
                   (json.dumps(PRINT_SETTINGS, indent=4) + "\n").encode(), compress_type=zipfile.ZIP_DEFLATED)
        z.writestr(zipfile.ZipInfo("Metadata/model_settings.config", (2026, 10, 12, 12, 52, 0)),
                   MODEL_SETTINGS.encode(), compress_type=zipfile.ZIP_DEFLATED)
        z.writestr(zipfile.ZipInfo("Metadata/assembly_step.json", (2026, 10, 12, 12, 52, 0)),
                   b'{"steps": []}', compress_type=zipfile.ZIP_DEFLATED)
    return path
