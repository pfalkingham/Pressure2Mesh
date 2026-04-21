"""
Blender script: build a pressure mesh from a delimited text file.

Usage:
1. Run this script from Blender's Text Editor.
2. Pick a source file when the file dialog opens.

Each pressure value maps to one vertex.
Z displacement is negative: z = -(pressure / PRESSURE_DIVISOR).
"""

import csv
import os
import re
import math
import traceback

import bpy
from mathutils import Vector


# -----------------------------------------------------------------------------
# Configuration (edit these values)
# -----------------------------------------------------------------------------

# Optional initial location for the file picker.
DEFAULT_FILE_PATH = r"C:\Users\pfalk\Desktop\Pressure\CCH_24 - 1-13-2014 - Entire Plate Roll Off.xls"

# Delimiter in your data file.
# Use "," for CSV or "\t" for tab-delimited text.
DELIMITER = "\t"

# Keep this available in case a future export includes header rows.
HEADER_ROWS_TO_SKIP = 0

# Pressure -> Z conversion: z = -(pressure / PRESSURE_DIVISOR).
PRESSURE_DIVISOR = 100

# Physical pressure-cell size (in cm).
# Keep these obvious and separate so you can swap/fix easily if needed.
CELL_SIZE_X_CM = 0.5
CELL_SIZE_Y_CM = 0.7

# Blender units are typically meters when scene unit scale is default.
CM_TO_BLENDER_UNITS = 0.01

# Mesh object name created/replaced by this script.
OBJECT_NAME = "PressureMesh"
PEAK_OBJECT_NAME = "PressureMesh_Peak"

# Gap between animated and peak meshes, in cell widths.
PEAK_MESH_GAP_CELLS = 4.0

# Optional source sampling frequency from the roll-off export header.
# Kept for reference; the animation uses a 1:1 source-frame timeline by default.
SOURCE_SCAN_HZ = 253.0

# Blender stores absolute shape-key frame positions in 10x scene-frame units.
ABSOLUTE_SHAPE_KEY_FRAME_SCALE = 10.0


FLOAT_PATTERN = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")
FRAME_HEADER_PATTERN = re.compile(r"^\s*Frame\s+(\d+)\s*\(([^)]*)\)", re.IGNORECASE)


def parse_numeric_row(line):
	"""Extract all float values from a line."""
	return [float(token) for token in FLOAT_PATTERN.findall(line)]


def extract_rectangular_grid_from_lines(path, header_rows_to_skip=0):
	"""Best-effort numeric grid extraction for headerless exports."""
	numeric_rows = []
	with open(path, "r", encoding="utf-8-sig") as fh:
		for line_idx, line in enumerate(fh, start=1):
			if line_idx <= header_rows_to_skip:
				continue
			values = parse_numeric_row(line)
			if values:
				numeric_rows.append((line_idx, values))

	if not numeric_rows:
		raise ValueError("No numeric pressure rows were found in the input file.")

	max_cols = max(len(values) for _, values in numeric_rows)
	candidates = [entry for entry in numeric_rows if len(entry[1]) == max_cols]
	if not candidates:
		raise ValueError("No rectangular numeric grid could be extracted from source.")

	# Keep the longest contiguous run to avoid mixing metadata lines.
	runs = []
	current_run = [candidates[0]]
	for prev, cur in zip(candidates, candidates[1:]):
		if cur[0] == prev[0] + 1:
			current_run.append(cur)
		else:
			runs.append(current_run)
			current_run = [cur]
	runs.append(current_run)

	best_run = max(runs, key=len)
	grid = [values for _, values in best_run]

	if not grid:
		raise ValueError("No contiguous numeric grid found.")

	return grid


def load_pressure_grid(path, delimiter, header_rows_to_skip=0):
	"""Read a rectangular numeric grid from a delimited text file."""
	if not os.path.exists(path):
		raise FileNotFoundError(f"Input file not found: {path}")

	grid = []
	try:
		with open(path, "r", newline="", encoding="utf-8-sig") as fh:
			reader = csv.reader(fh, delimiter=delimiter)
			for row_idx, row in enumerate(reader):
				if row_idx < header_rows_to_skip:
					continue

				# Drop trailing empty fields from accidental delimiter-at-EOL cases.
				while row and row[-1] == "":
					row.pop()

				if not row:
					continue

				numeric_row = []
				for token in row:
					token = token.strip()
					if token == "":
						raise ValueError("empty value inside data row")
					numeric_row.append(float(token))

				grid.append(numeric_row)
	except ValueError:
		grid = extract_rectangular_grid_from_lines(
			path=path,
			header_rows_to_skip=header_rows_to_skip,
		)

	if not grid:
		raise ValueError("No numeric pressure rows were found in the input file.")

	expected_cols = len(grid[0])
	if expected_cols == 0:
		raise ValueError("Parsed data has zero columns.")

	for r_idx, row in enumerate(grid, start=1):
		if len(row) != expected_cols:
			raise ValueError(
				"Non-rectangular data detected: "
				f"row {r_idx} has {len(row)} cols, expected {expected_cols}."
			)

	return grid


def finalize_frame(frame_rows, frame_tag):
	"""Validate one frame block and return a strict rectangular grid."""
	if not frame_rows:
		raise ValueError(f"Frame {frame_tag} has no numeric rows.")

	expected_cols = len(frame_rows[0])
	if expected_cols == 0:
		raise ValueError(f"Frame {frame_tag} has zero columns.")

	for row_idx, row in enumerate(frame_rows, start=1):
		if len(row) != expected_cols:
			raise ValueError(
				f"Non-rectangular data in frame {frame_tag}: "
				f"row {row_idx} has {len(row)} cols, expected {expected_cols}."
			)

	return frame_rows


def load_pressure_frames(path):
	"""Read full roll-off export with 'Frame N (...)' sections."""
	if not os.path.exists(path):
		raise FileNotFoundError(f"Input file not found: {path}")

	with open(path, "r", encoding="utf-8-sig") as fh:
		lines = fh.readlines()

	frames = []
	timestamps_ms = []
	current_rows = None
	current_frame_tag = None

	for line_idx, line in enumerate(lines, start=1):
		header = FRAME_HEADER_PATTERN.match(line)
		if header:
			if current_rows is not None:
				frames.append(finalize_frame(current_rows, current_frame_tag))

			current_frame_tag = int(header.group(1))
			current_rows = []

			time_tokens = parse_numeric_row(header.group(2))
			timestamps_ms.append(time_tokens[0] if time_tokens else None)
			continue

		if current_rows is None:
			continue

		row_values = parse_numeric_row(line)
		if row_values:
			current_rows.append(row_values)

	if current_rows is not None:
		frames.append(finalize_frame(current_rows, current_frame_tag))

	if not frames:
		raise ValueError(
			"No frame headers were found. Expected lines like: 'Frame 0 (0.00 ms)'."
		)

	expected_rows = len(frames[0])
	expected_cols = len(frames[0][0])
	for f_idx, frame in enumerate(frames):
		if len(frame) != expected_rows or len(frame[0]) != expected_cols:
			raise ValueError(
				f"Frame size mismatch at frame index {f_idx}: "
				f"got {len(frame)}x{len(frame[0])}, expected {expected_rows}x{expected_cols}."
			)

	return frames, timestamps_ms


def load_pressure_data(path, delimiter, header_rows_to_skip=0):
	"""Auto-detect single-grid vs roll-off frame format."""
	with open(path, "r", encoding="utf-8-sig") as fh:
		preview = [next(fh, "") for _ in range(60)]

	if any(FRAME_HEADER_PATTERN.match(line) for line in preview):
		frames, timestamps_ms = load_pressure_frames(path)
		return frames, timestamps_ms, True

	grid = load_pressure_grid(path, delimiter, header_rows_to_skip)
	return [grid], [None], False


def delete_existing_object(name):
	"""Delete existing object (and its mesh data) if it already exists."""
	obj = bpy.data.objects.get(name)
	if obj is None:
		return

	mesh_data = obj.data if obj.type == "MESH" else None
	bpy.data.objects.remove(obj, do_unlink=True)

	if mesh_data and mesh_data.users == 0:
		bpy.data.meshes.remove(mesh_data)


def build_pressure_mesh(grid, cell_size_x_bu, cell_size_y_bu, pressure_divisor, object_name=OBJECT_NAME):
	"""Create and link a new pressure mesh object from grid data."""
	if pressure_divisor == 0:
		raise ValueError("PRESSURE_DIVISOR cannot be 0.")

	rows = len(grid)
	cols = len(grid[0])

	# Center mesh around world origin in XY for easier viewport use.
	x_offset = (cols - 1) * cell_size_x_bu * 0.5
	y_offset = (rows - 1) * cell_size_y_bu * 0.5

	verts = []
	for r in range(rows):
		y = y_offset - (r * cell_size_y_bu)
		for c in range(cols):
			x = (c * cell_size_x_bu) - x_offset
			z = -(grid[r][c] / pressure_divisor)
			verts.append(Vector((x, y, z)))

	faces = []
	for r in range(rows - 1):
		for c in range(cols - 1):
			v0 = r * cols + c
			v1 = v0 + 1
			v2 = v0 + cols + 1
			v3 = v0 + cols
			faces.append((v0, v1, v2, v3))

	mesh = bpy.data.meshes.new(f"{object_name}_Mesh")
	mesh.from_pydata(verts, [], faces)
	mesh.update()

	obj = bpy.data.objects.new(object_name, mesh)
	bpy.context.scene.collection.objects.link(obj)
	return obj


def compute_peak_pressure_grid(frames):
	"""Compute per-cell maximum pressure across all frames."""
	if not frames:
		raise ValueError("No frames available for peak-pressure computation.")

	rows = len(frames[0])
	cols = len(frames[0][0])
	peak_grid = [[-math.inf for _ in range(cols)] for _ in range(rows)]

	for frame_idx, frame in enumerate(frames):
		if len(frame) != rows or len(frame[0]) != cols:
			raise ValueError(
				f"Frame size mismatch at index {frame_idx}: "
				f"got {len(frame)}x{len(frame[0])}, expected {rows}x{cols}."
			)
		for r in range(rows):
			for c in range(cols):
				peak_grid[r][c] = max(peak_grid[r][c], frame[r][c])

	return peak_grid


def apply_grid_to_shape_key(shape_key, grid, pressure_divisor):
	"""Write one pressure frame into one shape key by updating only Z."""
	if pressure_divisor == 0:
		raise ValueError("PRESSURE_DIVISOR cannot be 0.")

	idx = 0
	for row in grid:
		for pressure in row:
			shape_key.data[idx].co.z = -(pressure / pressure_divisor)
			idx += 1


def create_rolloff_shape_key_animation(obj, frames, pressure_divisor):
	"""Create absolute shape keys for all frames and animate eval_time."""
	scene = bpy.context.scene
	previous_frame = scene.frame_current
	print(f"Creating roll-off shape keys for {len(frames)} frames")

	scene.frame_set(0)
	basis = obj.shape_key_add(name="Basis", from_mix=False)
	apply_grid_to_shape_key(basis, frames[0], pressure_divisor)

	sk_data = obj.data.shape_keys

	for frame_idx in range(1, len(frames)):
		scene.frame_set(frame_idx)
		key = obj.shape_key_add(name=f"Frame_{frame_idx:04d}", from_mix=False)
		apply_grid_to_shape_key(key, frames[frame_idx], pressure_divisor)
		if frame_idx <= 3 or frame_idx == len(frames) - 1 or frame_idx % 100 == 0:
			print(
				f"Stamped {key.name} at scene frame {scene.frame_current}; "
				f"stored key frame={key.frame}"
			)

	sk_data.use_relative = False

	start_frame = 1.0
	end_frame = float(len(frames))

	scene.frame_start = int(math.floor(start_frame))
	scene.frame_end = int(math.ceil(end_frame))

	# Absolute shape keys are played as a sequence through eval_time.
	# Blender stores absolute key positions on a 10x scale relative to scene frames.
	for scene_frame in range(1, len(frames) + 1):
		sk_data.eval_time = float(scene_frame - 1) * ABSOLUTE_SHAPE_KEY_FRAME_SCALE
		sk_data.keyframe_insert(data_path="eval_time", frame=float(scene_frame))

	print(f"Absolute shape key mode: {sk_data.use_relative}")
	print(
		f"eval_time keyframes: frame {start_frame} -> 0.0, "
		f"frame {end_frame} -> {float(len(frames) - 1) * ABSOLUTE_SHAPE_KEY_FRAME_SCALE}"
	)
	print(f"Baked eval_time keys: {len(frames)}")
	print(f"Basis stored frame: {basis.frame}")
	print(f"Final stored frame: {sk_data.key_blocks[-1].frame}")

	scene.frame_set(previous_frame)


def process_selected_file(selected_path):
	"""Load source data and create animated + peak meshes."""
	if not selected_path:
		raise RuntimeError("File selection canceled by user.")

	print(f"Selected file: {selected_path}")
	frames, timestamps_ms, is_rolloff = load_pressure_data(
		path=selected_path,
		delimiter=DELIMITER,
		header_rows_to_skip=HEADER_ROWS_TO_SKIP,
	)
	grid = frames[0]
	peak_grid = compute_peak_pressure_grid(frames)

	cell_size_x_bu = CELL_SIZE_X_CM * CM_TO_BLENDER_UNITS
	cell_size_y_bu = CELL_SIZE_Y_CM * CM_TO_BLENDER_UNITS

	if cell_size_x_bu <= 0 or cell_size_y_bu <= 0:
		raise ValueError("CELL_SIZE_X_CM and CELL_SIZE_Y_CM must be > 0.")

	delete_existing_object(OBJECT_NAME)
	obj = build_pressure_mesh(
		grid=grid,
		cell_size_x_bu=cell_size_x_bu,
		cell_size_y_bu=cell_size_y_bu,
		pressure_divisor=PRESSURE_DIVISOR,
		object_name=OBJECT_NAME,
	)

	delete_existing_object(PEAK_OBJECT_NAME)
	peak_obj = build_pressure_mesh(
		grid=peak_grid,
		cell_size_x_bu=cell_size_x_bu,
		cell_size_y_bu=cell_size_y_bu,
		pressure_divisor=PRESSURE_DIVISOR,
		object_name=PEAK_OBJECT_NAME,
	)

	grid_width_bu = (len(grid[0]) - 1) * cell_size_x_bu
	peak_gap_bu = max(PEAK_MESH_GAP_CELLS * cell_size_x_bu, cell_size_x_bu)
	peak_obj.location.x = grid_width_bu + peak_gap_bu

	if is_rolloff and len(frames) > 1:
		create_rolloff_shape_key_animation(
			obj=obj,
			frames=frames,
			pressure_divisor=PRESSURE_DIVISOR,
		)

	rows = len(grid)
	cols = len(grid[0])
	min_p = min(min(row) for row in grid)
	max_p = max(max(row) for row in grid)
	width_cm = cols * CELL_SIZE_X_CM
	height_cm = rows * CELL_SIZE_Y_CM

	print(f"Created object: {obj.name}")
	print(f"Created peak object: {peak_obj.name}")
	print(f"Grid: {rows} rows x {cols} cols")
	print(f"Pressure range: {min_p} .. {max_p}")
	peak_min_p = min(min(row) for row in peak_grid)
	peak_max_p = max(max(row) for row in peak_grid)
	print(f"Peak pressure range: {peak_min_p} .. {peak_max_p}")
	print(f"Frames loaded: {len(frames)}")
	if is_rolloff and len(frames) > 1:
		t0 = timestamps_ms[0]
		t1 = timestamps_ms[1] if len(timestamps_ms) > 1 else None
		print(f"Roll-off mode: yes")
		print(f"Source scan rate (Hz): {SOURCE_SCAN_HZ}")
		print("Timeline mode: 1 Blender frame per source frame")
		if t0 is not None and t1 is not None:
			print(f"Header timing sample (ms): first={t0}, second={t1}")
		print(f"Scene frame range: {bpy.context.scene.frame_start} .. {bpy.context.scene.frame_end}")
	else:
		print("Roll-off mode: no (single pressure grid)")
	print(f"Cell size (cm): X={CELL_SIZE_X_CM}, Y={CELL_SIZE_Y_CM}")
	print(f"Footprint (cm): width={width_cm}, height={height_cm}")
	print(f"Z mapping: z = -(pressure / {PRESSURE_DIVISOR})")


class PRESSURE_OT_pick_file(bpy.types.Operator):
	"""Select a pressure export file and build meshes."""
	bl_idname = "pressure.pick_file"
	bl_label = "Pick Pressure File"
	bl_options = {'REGISTER'}

	filepath: bpy.props.StringProperty(subtype="FILE_PATH")
	filter_glob: bpy.props.StringProperty(
		default="*.xls;*.csv;*.tsv;*.txt",
		options={'HIDDEN'},
	)

	def execute(self, context):
		try:
			process_selected_file(self.filepath)
		except Exception as exc:
			print("pressure2mesh failed:")
			traceback.print_exc()
			self.report({'ERROR'}, str(exc))
			return {'CANCELLED'}
		self.report({'INFO'}, "Pressure mesh generation completed")
		return {'FINISHED'}

	def invoke(self, context, event):
		if DEFAULT_FILE_PATH and os.path.exists(DEFAULT_FILE_PATH):
			self.filepath = DEFAULT_FILE_PATH
		context.window_manager.fileselect_add(self)
		return {'RUNNING_MODAL'}


def register_operator():
	"""Register or refresh the file-picker operator."""
	try:
		bpy.utils.unregister_class(PRESSURE_OT_pick_file)
	except Exception:
		pass
	bpy.utils.register_class(PRESSURE_OT_pick_file)


def main():
	if bpy.app.background:
		raise RuntimeError("Cannot open file picker in background mode. Run this in Blender UI.")

	register_operator()
	result = bpy.ops.pressure.pick_file('INVOKE_DEFAULT')
	if 'RUNNING_MODAL' not in result:
		raise RuntimeError(f"Failed to open Blender file picker: {result}")
	print(f"File picker started: {result}")


if __name__ == "__main__":
	main()
