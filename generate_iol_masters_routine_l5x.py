"""Generate a Routine L5X from IOL_masters.toml.

This script reads BNI005L point mappings from IOL_masters.toml and emits
an importable RLL routine where each rung maps a physical input bit to a
logical destination tag:

    XIC(source_tag) OTE(logical_dest_tag)
"""

from __future__ import annotations

import argparse
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import lxml.etree as etree

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_IOL_MASTERS_TOML = PROJECT_ROOT / "input" / "Hybrid Main Line" / "IOL_masters.toml"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output" / "Hybrid Main Line"
DEFAULT_OUTPUT_STEM = "IOL_Masters_Map"
DEFAULT_ROUTINE_NAME = "IOL_Masters_Map"
DEFAULT_CONTROLLER_NAME = "Wolf_HybridMainLine"
DEFAULT_PROGRAM_NAME = "MainProgram"


@dataclass(frozen=True)
class MappingRow:
    source: str
    dest: str
    comment: str


@dataclass(frozen=True)
class RoutineConfig:
    output_dir: Path
    output_stem: str
    routine_name: str
    controller_name: str
    program_name: str


def _read_toml(path: Path) -> dict[str, Any]:
    with open(path, "rb") as f:
        raw = f.read()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return tomllib.loads(raw.decode("utf-8"))


def _require_nonempty(value: object, *, field: str) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        raise RuntimeError(f"Missing required field: {field}")
    return text


def _load_config(data: dict[str, Any]) -> RoutineConfig:
    cfg = data.get("config")
    if isinstance(cfg, dict):
        output_dir_raw = str(cfg.get("output_dir", "")).strip()
        output_dir = Path(output_dir_raw) if output_dir_raw else DEFAULT_OUTPUT_DIR
        if not output_dir.is_absolute():
            output_dir = PROJECT_ROOT / output_dir

        output_stem = str(cfg.get("output_stem", DEFAULT_OUTPUT_STEM)).strip() or DEFAULT_OUTPUT_STEM
        routine_name = str(cfg.get("routine_name", DEFAULT_ROUTINE_NAME)).strip() or DEFAULT_ROUTINE_NAME
        controller_name = (
            str(cfg.get("controller_name", DEFAULT_CONTROLLER_NAME)).strip() or DEFAULT_CONTROLLER_NAME
        )
        program_name = str(cfg.get("program_name", DEFAULT_PROGRAM_NAME)).strip() or DEFAULT_PROGRAM_NAME
    else:
        output_dir = DEFAULT_OUTPUT_DIR
        output_stem = DEFAULT_OUTPUT_STEM
        routine_name = DEFAULT_ROUTINE_NAME
        controller_name = DEFAULT_CONTROLLER_NAME
        program_name = DEFAULT_PROGRAM_NAME

    return RoutineConfig(
        output_dir=output_dir,
        output_stem=output_stem,
        routine_name=routine_name,
        controller_name=controller_name,
        program_name=program_name,
    )


def _load_mapping_rows(data: dict[str, Any]) -> list[MappingRow]:
    groups = data.get("bni005l_groups")
    if not isinstance(groups, list) or not groups:
        raise RuntimeError("Missing or empty [[bni005l_groups]] in IOL masters TOML.")

    rows: list[MappingRow] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        io_group = str(group.get("io_group", "")).strip()
        module = str(group.get("module", "")).strip()

        points = group.get("points", [])
        if not isinstance(points, list):
            continue

        for point in points:
            if not isinstance(point, dict):
                continue

            source = _require_nonempty(point.get("source_tag"), field="bni005l_groups.points.source_tag")
            dest = _require_nonempty(point.get("logical_dest_tag"), field="bni005l_groups.points.logical_dest_tag")

            point_id = str(point.get("point_id", "")).strip()
            zone = str(point.get("zone", "")).strip()
            device = str(point.get("device", "")).strip()
            state = str(point.get("state", "")).strip()
            state_label = state if state else "N/A"

            comment = (
                f"IOL masters input | IO {io_group} {module} {point_id} | "
                f"Zone {zone} | {device} {state_label}"
            )

            rows.append(MappingRow(source=source, dest=dest, comment=comment))

    if not rows:
        raise RuntimeError("No valid bni005l_groups.points mappings found.")

    return rows


def _make_rung(number: int, text: str, comment: str | None = None) -> etree._Element:
    rung = etree.Element("Rung", Number=str(number), Type="N")
    if comment:
        comment_el = etree.SubElement(rung, "Comment")
        comment_el.text = etree.CDATA(comment)
    text_el = etree.SubElement(rung, "Text")
    text_el.text = etree.CDATA(text)
    return rung


def _build_root(config: RoutineConfig) -> tuple[etree._Element, etree._Element]:
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


def _rung_text(source: str, dest: str) -> str:
    return f"XIC({source})OTE({dest});"


def write_l5x(config: RoutineConfig, rows: list[MappingRow]) -> Path:
    root, rll = _build_root(config)
    rll.append(_make_rung(0, "NOP();", "Auto-generated IOL masters map routine"))

    for idx, row in enumerate(rows, start=1):
        rll.append(_make_rung(idx, _rung_text(row.source, row.dest), row.comment))

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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate importable L5X routine from IOL_masters.toml.")
    parser.add_argument(
        "--iol-masters",
        type=Path,
        default=DEFAULT_IOL_MASTERS_TOML,
        help="Path to IOL_masters TOML (default: input/Hybrid Main Line/IOL_masters.toml).",
    )
    return parser.parse_args()


def main(path: Path) -> None:
    data = _read_toml(path)
    config = _load_config(data)
    rows = _load_mapping_rows(data)
    out = write_l5x(config, rows)

    print(f"IOL masters rows generated: {len(rows)}")
    print(f"Routine name: {config.routine_name}")
    print(f"Wrote: {out}")


if __name__ == "__main__":
    args = _parse_args()
    main(args.iol_masters)
