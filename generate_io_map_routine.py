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


def _load_io_rows(data: dict[str, Any], source_path: Path) -> list[IoMapRow]:
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


def write_io_map_l5x(config: IoMapConfig, rows: list[IoMapRow]) -> Path:
	root, rll = _build_routine_root(config)

	# Keep rung 0 as a harmless placeholder to match existing routine style.
	rll.append(_make_rung(0, "NOP();", "Auto-generated IO_Map routine"))

	for idx, row in enumerate(rows, start=1):
		rll.append(_make_rung(idx, _build_rung_text(row), row.comment))

	config.output_dir.mkdir(parents=True, exist_ok=True)
	timestamp = datetime.now().isoformat(timespec="seconds").replace(":", "-")
	out_path = config.output_dir / f"{config.output_stem}_{timestamp}.L5X"
	etree.ElementTree(root).write(
		str(out_path),
		xml_declaration=True,
		encoding="UTF-8",
		standalone=True,
		pretty_print=True,
	)
	return out_path


def main(io_map_toml: Path) -> None:
	data = _read_toml(io_map_toml)
	config = _load_config(data, io_map_toml)
	rows = _load_io_rows(data, io_map_toml)
	out = write_io_map_l5x(config, rows)

	print(f"IO map rows generated: {len(rows)}")
	print(f"Routine name: {config.routine_name}")
	print(f"Wrote: {out}")


def _parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="Generate IO_Map routine L5X from a TOML map list.")
	parser.add_argument(
		"--stations",
		type=Path,
		default=DEFAULT_IO_MAP_TOML,
		help="Path to IO map TOML (default: input/Hybrid Main Line/stations_io_map.toml).",
	)
	return parser.parse_args()


if __name__ == "__main__":
	args = _parse_args()
	main(args.stations)
