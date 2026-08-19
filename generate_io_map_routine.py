"""Generate Routine L5X containing only IO_Map routine logic.

This script uses a TOML file as the source of real input-to-tag map rows and emits:
- A single RLL routine (default: IO_Map)

It intentionally does NOT generate IO_Simulate, station programs, or module exports.
"""

# Standard library imports
from __future__ import annotations

import argparse
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

# Third-party imports
import lxml.etree as etree

# Local application imports

# Constants
PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_IO_MAP_TOML = PROJECT_ROOT / "input" / "Hybrid Main Line" / "stations_io_map.toml"
DEFAULT_TEMPLATE_L5X = PROJECT_ROOT / "input" / "Hybrid Main Line" / "IO_Map_Updated_Routine_New_RLL.L5X"
DEFAULT_OUTPUT_FILE_STEM = "IO_Map"
DEFAULT_ROUTINE_NAME = "IO_Map"
CONTROLLER_NAME = "Wolf_HybridMainLine"
PROGRAM_NAME = "MainProgram"


SourceInstruction = Literal["XIC", "XIO"]
DestInstruction = Literal["OTE", "OTL", "OTU"]


@dataclass(frozen=True)
class IoMapRow:
	source: str
	source_instruction: SourceInstruction
	dest: str
	dest_instruction: DestInstruction
	comment: str | None


@dataclass(frozen=True)
class IoMapConfig:
	output_dir: Path
	output_stem: str
	controller_name: str
	program_name: str
	routine_name: str


def _read_toml(path: Path) -> dict[str, Any]:
	with open(path, "rb") as f:
		raw = f.read()
	if raw.startswith(b"\xef\xbb\xbf"):
		raw = raw[3:]
	return tomllib.loads(raw.decode("utf-8"))


def _resolve_path(path_value: str, *, base_dir: Path) -> Path:
	path = Path(path_value)
	return path if path.is_absolute() else (base_dir / path)


def _require_nonempty_string(value: object, *, field_name: str) -> str:
	text = str(value).strip() if value is not None else ""
	if not text:
		raise RuntimeError(f"Missing required non-empty value for {field_name}.")
	return text


def _load_config(data: dict[str, Any], source_path: Path) -> IoMapConfig:
	config = data.get("config")
	if not isinstance(config, dict):
		raise RuntimeError(f"Missing [config] table in {source_path}.")

	output_dir_raw = config.get("output_dir")
	output_dir = _resolve_path(
		_require_nonempty_string(output_dir_raw, field_name="config.output_dir"),
		base_dir=PROJECT_ROOT,
	)

	io_map_cfg = data.get("io_map")
	if io_map_cfg is None:
		io_map_cfg = {}
	if not isinstance(io_map_cfg, dict):
		raise RuntimeError(f"[io_map] must be a TOML table in {source_path}.")

	output_stem = str(io_map_cfg.get("output_stem", DEFAULT_OUTPUT_FILE_STEM)).strip() or DEFAULT_OUTPUT_FILE_STEM
	controller_name = str(io_map_cfg.get("controller_name", CONTROLLER_NAME)).strip() or CONTROLLER_NAME
	program_name = str(io_map_cfg.get("program_name", PROGRAM_NAME)).strip() or PROGRAM_NAME
	routine_name = str(io_map_cfg.get("routine_name", DEFAULT_ROUTINE_NAME)).strip() or DEFAULT_ROUTINE_NAME

	return IoMapConfig(
		output_dir=output_dir,
		output_stem=output_stem,
		controller_name=controller_name,
		program_name=program_name,
		routine_name=routine_name,
	)


def _parse_source_instruction(value: object, *, row_idx: int) -> SourceInstruction:
	instr = str(value).strip().upper() if value is not None else "XIC"
	if instr not in {"XIC", "XIO"}:
		raise RuntimeError(f"Unsupported source_instruction '{value}' in io_map row {row_idx}. Use XIC or XIO.")
	return instr


def _parse_dest_instruction(value: object, *, row_idx: int) -> DestInstruction:
	instr = str(value).strip().upper() if value is not None else "OTE"
	if instr not in {"OTE", "OTL", "OTU"}:
		raise RuntimeError(f"Unsupported dest_instruction '{value}' in io_map row {row_idx}. Use OTE, OTL, or OTU.")
	return instr


def _compose_balluff_comment(block_module: str, output_row: dict[str, Any]) -> str | None:
	point_id = str(output_row.get("point_id", "")).strip()
	zone = str(output_row.get("zone", "")).strip()
	function = str(output_row.get("function", "")).strip()
	notes = str(output_row.get("notes", "")).strip()

	parts: list[str] = []
	head = f"{block_module} {point_id}".strip()
	if head:
		parts.append(head)
	if zone:
		parts.append(f"Zone {zone}")
	if function:
		parts.append(function)
	if notes:
		parts.append(notes)

	if not parts:
		return None
	return " | ".join(parts)


def _load_io_rows_from_balluff(data: dict[str, Any], source_path: Path) -> list[IoMapRow]:
	balluff_cfg = data.get("balluff")
	if not isinstance(balluff_cfg, dict):
		raise RuntimeError(f"Missing [balluff] table in {source_path}.")

	blocks = balluff_cfg.get("blocks")
	if not isinstance(blocks, list):
		raise RuntimeError(f"Missing [[balluff.blocks]] array in {source_path}.")

	rows: list[IoMapRow] = []
	row_idx = 0
	for block_idx, block in enumerate(blocks, start=1):
		if not isinstance(block, dict):
			raise RuntimeError(f"Each [[balluff.blocks]] row must be a TOML table (block {block_idx}).")

		module = _require_nonempty_string(
			block.get("module"),
			field_name=f"balluff.blocks[{block_idx}].module",
		)

		outputs_raw = block.get("outputs")
		if not isinstance(outputs_raw, list):
			continue

		for output in outputs_raw:
			if not isinstance(output, dict):
				raise RuntimeError(
					f"Each [[balluff.blocks.outputs]] row must be a TOML table (block {block_idx})."
				)

			enabled = output.get("enabled", True)
			if isinstance(enabled, bool) and not enabled:
				continue

			row_idx += 1
			source = _require_nonempty_string(
				output.get("logical_dest_tag"),
				field_name=f"balluff.blocks[{block_idx}].outputs[{row_idx}].logical_dest_tag",
			)
			dest = _require_nonempty_string(
				output.get("source_tag"),
				field_name=f"balluff.blocks[{block_idx}].outputs[{row_idx}].source_tag",
			)
			source_instruction = _parse_source_instruction(output.get("source_instruction"), row_idx=row_idx)
			dest_instruction = _parse_dest_instruction(output.get("dest_instruction"), row_idx=row_idx)

			comment_raw = output.get("comment")
			if comment_raw is not None and str(comment_raw).strip():
				comment = str(comment_raw).strip()
			else:
				comment = _compose_balluff_comment(module, output)

			rows.append(
				IoMapRow(
					source=source,
					source_instruction=source_instruction,
					dest=dest,
					dest_instruction=dest_instruction,
					comment=comment,
				)
			)

	if not rows:
		raise RuntimeError("No enabled [[balluff.blocks.outputs]] mappings found. Nothing to generate.")

	return rows


def _load_io_rows_from_rungs(data: dict[str, Any], source_path: Path) -> list[IoMapRow]:
	io_map_cfg = data.get("io_map")
	if not isinstance(io_map_cfg, dict):
		raise RuntimeError(f"Missing [io_map] table in {source_path}.")

	rows_raw = io_map_cfg.get("rungs")
	if not isinstance(rows_raw, list):
		raise RuntimeError(f"Missing [[io_map.rungs]] array in {source_path}.")
	if not rows_raw:
		raise RuntimeError(f"[[io_map.rungs]] is empty in {source_path}.")

	rows: list[IoMapRow] = []
	for idx, row in enumerate(rows_raw, start=1):
		if not isinstance(row, dict):
			raise RuntimeError(f"Each [[io_map.rungs]] row must be a TOML inline table (row {idx}).")

		enabled = row.get("enabled", True)
		if isinstance(enabled, bool) and not enabled:
			continue

		source = _require_nonempty_string(row.get("source"), field_name=f"io_map.rungs[{idx}].source")
		dest = _require_nonempty_string(row.get("dest"), field_name=f"io_map.rungs[{idx}].dest")
		source_instruction = _parse_source_instruction(row.get("source_instruction"), row_idx=idx)
		dest_instruction = _parse_dest_instruction(row.get("dest_instruction"), row_idx=idx)
		comment_raw = row.get("comment")
		comment = str(comment_raw).strip() if comment_raw is not None else None
		if comment == "":
			comment = None

		rows.append(
			IoMapRow(
				source=source,
				source_instruction=source_instruction,
				dest=dest,
				dest_instruction=dest_instruction,
				comment=comment,
			)
		)

	if not rows:
		raise RuntimeError("All [[io_map.rungs]] rows were disabled. Nothing to generate.")

	return rows


def _load_io_rows(data: dict[str, Any], source_path: Path) -> list[IoMapRow]:
	io_map_cfg = data.get("io_map")
	if isinstance(io_map_cfg, dict) and isinstance(io_map_cfg.get("rungs"), list):
		return _load_io_rows_from_rungs(data, source_path)

	balluff_cfg = data.get("balluff")
	if isinstance(balluff_cfg, dict) and isinstance(balluff_cfg.get("blocks"), list):
		return _load_io_rows_from_balluff(data, source_path)

	raise RuntimeError(
		f"No IO map rows found in {source_path}. Provide either [[io_map.rungs]] or [[balluff.blocks.outputs]]."
	)


def _make_rung(number: int, text: str, comment: str | None = None) -> etree._Element:
	rung = etree.Element("Rung", Number=str(number), Type="N")
	if comment:
		comment_el = etree.SubElement(rung, "Comment")
		comment_el.text = etree.CDATA(comment)
	text_el = etree.SubElement(rung, "Text")
	text_el.text = etree.CDATA(text)
	return rung


def _build_routine_root(config: IoMapConfig) -> tuple[etree._Element, etree._Element]:
	root = etree.Element(
		"RSLogix5000Content",
		SchemaRevision="1.0",
		SoftwareRevision="34.03",
		TargetName=config.routine_name,
		TargetType="Routine",
		TargetSubType="RLL",
		ContainsContext="true",
		ExportDate=datetime.now().strftime("%a %b %d %H:%M:%S %Y"),
		ExportOptions=(
			"References NoRawData L5KData DecoratedData Context Dependencies "
			"ForceProtectedEncoding AllProjDocTrans"
		),
	)

	controller = etree.SubElement(root, "Controller", Use="Context", Name=config.controller_name)
	programs = etree.SubElement(controller, "Programs", Use="Context")
	program = etree.SubElement(programs, "Program", Use="Context", Name=config.program_name)
	routines = etree.SubElement(program, "Routines", Use="Context")
	routine = etree.SubElement(routines, "Routine", Use="Target", Name=config.routine_name, Type="RLL")
	rll = etree.SubElement(routine, "RLLContent")
	return root, rll


def _build_rung_text(row: IoMapRow) -> str:
	return f"{row.source_instruction}({row.source}){row.dest_instruction}({row.dest});"


def _replace_rungs(rll: etree._Element, rows: list[IoMapRow]) -> None:
	for child in list(rll):
		rll.remove(child)

	# Keep rung 0 as a harmless placeholder to match existing routine style.
	rll.append(_make_rung(0, "NOP();", "Auto-generated IO_Map routine"))

	for idx, row in enumerate(rows, start=1):
		rll.append(_make_rung(idx, _build_rung_text(row), row.comment))


def _find_target_rll_content(root: etree._Element, *, routine_name: str) -> etree._Element:
	# Prefer the target routine context in importable L5X exports.
	nodes = root.xpath("//Routine[@Use='Target' and @Type='RLL']/RLLContent")
	if nodes:
		return nodes[0]

	# Fall back to routine-name match when template lacks Use='Target'.
	nodes = root.xpath(f"//Routine[@Type='RLL' and @Name='{routine_name}']/RLLContent")
	if nodes:
		return nodes[0]

	# Last resort: first RLL routine in template.
	nodes = root.xpath("//Routine[@Type='RLL']/RLLContent")
	if nodes:
		return nodes[0]

	raise RuntimeError("Could not find an RLL routine in template L5X.")


def write_io_map_l5x(config: IoMapConfig, rows: list[IoMapRow], *, template_path: Path | None = None) -> Path:
	if template_path and template_path.exists():
		parser = etree.XMLParser(remove_blank_text=False)
		tree = etree.parse(str(template_path), parser)
		root = tree.getroot()
		root.set("ExportDate", datetime.now().strftime("%a %b %d %H:%M:%S %Y"))
		rll = _find_target_rll_content(root, routine_name=config.routine_name)
		_replace_rungs(rll, rows)
	else:
		root, rll = _build_routine_root(config)
		_replace_rungs(rll, rows)

	config.output_dir.mkdir(parents=True, exist_ok=True)
	timestamp = datetime.now().isoformat(timespec="seconds").replace(":", "-")
	out_path = config.output_dir / f"{config.output_stem}_{timestamp}.L5X"
	if template_path and template_path.exists():
		tree.write(
			str(out_path),
			xml_declaration=True,
			encoding="UTF-8",
			standalone=True,
			pretty_print=True,
		)
	else:
		etree.ElementTree(root).write(
			str(out_path),
			xml_declaration=True,
			encoding="UTF-8",
			standalone=True,
			pretty_print=True,
		)
	return out_path


def main(io_map_toml: Path, template_l5x: Path | None) -> None:
	data = _read_toml(io_map_toml)
	config = _load_config(data, io_map_toml)
	rows = _load_io_rows(data, io_map_toml)
	out = write_io_map_l5x(config, rows, template_path=template_l5x)

	print(f"IO map rows generated: {len(rows)}")
	print(f"Routine name: {config.routine_name}")
	if template_l5x:
		print(f"Template: {template_l5x}")
	print(f"Wrote: {out}")


def _parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="Generate IO_Map routine L5X from a TOML map list.")
	parser.add_argument(
		"--stations",
		type=Path,
		default=DEFAULT_IO_MAP_TOML,
		help="Path to IO map TOML (default: input/Hybrid Main Line/stations_io_map.toml).",
	)
	parser.add_argument(
		"--template",
		type=Path,
		default=DEFAULT_TEMPLATE_L5X,
		help="Path to template Routine L5X to clone and update.",
	)
	return parser.parse_args()


if __name__ == "__main__":
	args = _parse_args()
	main(args.stations, args.template)
