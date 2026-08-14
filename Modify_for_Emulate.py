"""Rewrite STRING MOV instructions in an L5X file to CPS for Logix Emulate compatibility.

This avoids controller verify errors where Emulate rejects MOV on STRING operands.
"""

from __future__ import annotations

import argparse
import re
import xml.etree.ElementTree as ET
from pathlib import Path

MOV_PATTERN = re.compile(r"MOV\(\s*([^,]+?)\s*,\s*([^)]+?)\s*\)")
IDENT_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*")
MEMBER_PATTERN = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)")
NUMERIC_PATTERN = re.compile(r"^[-+]?\d+(?:\.\d+)?$")


def build_type_maps(root: ET.Element) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    """Collect UDT member types and visible tag root types from the L5X XML."""
    udt_members: dict[str, dict[str, str]] = {}
    root_tag_types: dict[str, str] = {}

    for dt in root.findall(".//DataTypes/DataType"):
        dt_name = dt.get("Name")
        if not dt_name:
            continue
        members: dict[str, str] = {}
        for member in dt.findall("./Members/Member"):
            member_name = member.get("Name")
            member_type = member.get("DataType")
            if member_name and member_type:
                members[member_name] = member_type
        udt_members[dt_name] = members

    for tag in root.findall(".//Tag"):
        tag_name = tag.get("Name")
        data_type = tag.get("DataType")
        if tag_name and data_type:
            root_tag_types[tag_name] = data_type

    return udt_members, root_tag_types


def strip_indices(token: str) -> str:
    return re.sub(r"\[[^\]]*\]", "", token)


def resolve_operand_type(
    operand: str,
    udt_members: dict[str, dict[str, str]],
    root_tag_types: dict[str, str],
) -> str | None:
    """Resolve operand type for common Tag.Member[index] syntax used in rung text."""
    cleaned = strip_indices(operand.strip())
    if NUMERIC_PATTERN.match(cleaned):
        return "NUMERIC_LITERAL"
    if cleaned.startswith('"') and cleaned.endswith('"'):
        return "STRING"

    ident_match = IDENT_PATTERN.match(cleaned)
    if not ident_match:
        return None

    root_name = ident_match.group(0)
    current_type = root_tag_types.get(root_name)
    if not current_type:
        return None

    for member_name in MEMBER_PATTERN.findall(cleaned):
        members = udt_members.get(current_type)
        if not members:
            return None
        current_type = members.get(member_name)
        if not current_type:
            return None

    return current_type


def rewrite_string_mov_to_cps(content: str) -> tuple[str, int]:
    """Replace MOV(src,dst) with CPS(src,dst,1) when both operands resolve to STRING."""
    root = ET.fromstring(content)
    udt_members, root_tag_types = build_type_maps(root)

    replacements = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal replacements
        src = match.group(1).strip()
        dst = match.group(2).strip()
        src_type = resolve_operand_type(src, udt_members, root_tag_types)
        dst_type = resolve_operand_type(dst, udt_members, root_tag_types)

        if src_type == "STRING" and dst_type == "STRING":
            replacements += 1
            return f"CPS({src},{dst},1)"

        return match.group(0)

    updated = MOV_PATTERN.sub(_replace, content)
    return updated, replacements


def default_output_path(input_path: Path) -> Path:
    return input_path.with_name(f"{input_path.stem}_string_cps{input_path.suffix}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=("Copy an L5X file and replace STRING MOV(source,dest) instructions with CPS(source,dest,1).")
    )
    parser.add_argument("input_l5x", type=Path, help="Path to source L5X file")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output file path (default: <input>_string_cps.L5X)",
    )
    args = parser.parse_args()

    input_path = args.input_l5x
    output_path = args.output or default_output_path(input_path)

    content = input_path.read_text(encoding="utf-8")
    updated, replacements = rewrite_string_mov_to_cps(content)
    output_path.write_text(updated, encoding="utf-8")

    print(f"Input: {input_path}")
    print(f"Output: {output_path}")
    print(f"Replacements: {replacements}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
