# Pressure to Mesh for Blender

This script imports pressure-map data and builds Blender meshes from it.

It creates:
- An animated mesh named PressureMesh when the source contains multiple frames
- A static mesh named PressureMesh_Peak that shows peak pressure at each grid point (per-cell maximum across all frames)

## What the script does

The script in pressure2mesh.py:
- Opens a Blender file picker so you can choose a source file each run
- Auto-detects whether the source is:
  - A roll-off export with Frame N (time) headers (multi-frame animation)
  - A single-grid export without frame headers
- Converts pressure values to vertex height using:
  - z = -(pressure / PRESSURE_DIVISOR)
- Builds a quad mesh from the rectangular pressure grid
- For roll-off files, creates absolute shape keys and animates eval_time
- Builds a second unanimated mesh from peak-per-cell values and places it to the right

## Supported input files

The picker accepts:
- .xls (text-exported/tabular pressure data)
- .csv
- .tsv
- .txt

Notes:
- The parser expects numeric pressure values arranged in rows and columns
- For roll-off animation, frame headers should look like: Frame 0 (0.00 ms)
- For single-frame files without headers, the script extracts the best rectangular numeric block automatically

## Usage

1. Open Blender (UI mode, not background mode).
2. Open your .blend file.
3. Open pressure2mesh.py in Blender Text Editor (or copy-paste it there).
4. Press Run Script.
5. In the file browser popup, choose your pressure file.
6. Wait for mesh generation and check Blender console output for details.

## Output in the scene

After a successful run:
- PressureMesh is created (and replaces an older object with the same name)
- PressureMesh_Peak is created next to it (and replaces an older one with that name)

If the file contains multiple frames:
- PressureMesh receives absolute shape keys
- Scene frame range is updated to match source frame count
- Playback runs one Blender frame per source frame via eval_time keys

If the file contains one frame:
- PressureMesh is generated as a static mesh
- PressureMesh_Peak will match that same frame geometry

## Important settings inside pressure2mesh.py

You can edit these constants near the top of the script:
- DELIMITER: Default delimiter for direct row parsing (tab by default)
- HEADER_ROWS_TO_SKIP: Skip non-data rows for single-grid parsing
- PRESSURE_DIVISOR: Vertical scaling divisor for z displacement
- CELL_SIZE_X_CM and CELL_SIZE_Y_CM: Physical spacing between pressure cells
- PEAK_MESH_GAP_CELLS: Horizontal spacing between animated and peak meshes
- ABSOLUTE_SHAPE_KEY_FRAME_SCALE: Absolute-key timing scale used by Blender

## Version notes

Current behavior includes:
- Picker-based file selection each run
- Headerless single-frame fallback parsing
- Static peak-pressure mesh generation beside animated mesh
