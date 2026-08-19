"""Generate IOL Masters Routine L5X using a template export.

This keeps the template's full context sections (DataTypes, dependencies, etc.)
and replaces the routine with AOI master-mapping logic:
- AOI_BNI006A_50_31_040 call per master
- MOV(1, ...Port_n_Function) to force IO-Link mode on all 8 ports
"""

from __future__ import annotations

import argparse
import re
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import lxml.etree as etree

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_IOL_MASTERS_TOML = PROJECT_ROOT / "input" / "Hybrid Main Line" / "IOL_masters.toml"
DEFAULT_STATIONS_TOML = PROJECT_ROOT / "input" / "Hybrid Main Line" / "stations_io_map_balluff_master.toml"
DEFAULT_TEMPLATE_L5X = (
    PROJECT_ROOT / "input" / "Hybrid Main Line" / "IOL_Masters_Updated_Routine_New_RLL.L5X"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output" / "Hybrid Main Line"
DEFAULT_OUTPUT_NAME = "IOL_Masters_Routine_RLL_Generated.L5X"


@dataclass(frozen=True)
class MasterConfig:
    station_id: str
    aoi_tag: str
    mapped_tag: str
    port_prefix: str


@dataclass(frozen=True)
class Bni0091PortBinding:
    module_name: str
    master_port_tag: str


@dataclass(frozen=True)
class OutputBridge:
    source_tag: str
    dest_bit_tag: str
    comment: str


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


def _load_masters(data: dict[str, Any]) -> list[MasterConfig]:
    masters = data.get("masters")
    if not isinstance(masters, list) or not masters:
        raise RuntimeError("Missing or empty [[masters]] in IOL_masters TOML.")

    out: list[MasterConfig] = []
    for master in masters:
        if not isinstance(master, dict):
            continue
        iol_master_tag = _require_nonempty(master.get("iol_master_tag"), field="masters.iol_master_tag")
        hm = str(master.get("hm", "")).strip()
        station_id = ""
        if iol_master_tag.startswith("IOL_Master"):
            station_id = iol_master_tag.replace("IOL_Master", "", 1)
        else:
            match = re.search(r"(\d{5})", iol_master_tag)
            if match:
                station_id = match.group(1)
        if not station_id:
            station_id = hm
        station_id = _require_nonempty(station_id, field="master station_id")

        port_prefix_raw = str(master.get("iol_port_prefix", "")).strip()
        port_prefix = port_prefix_raw if port_prefix_raw else f"_{station_id}IOL_P"

        out.append(
            MasterConfig(
                station_id=station_id,
                aoi_tag=f"_{station_id}IOL",
                mapped_tag=f"_{station_id}IOL_Mapped_Data",
                port_prefix=port_prefix,
            )
        )

    if not out:
        raise RuntimeError("No valid [[masters]] rows found in IOL_masters TOML.")
    return out


def _load_bni0091_port_bindings(data: dict[str, Any]) -> dict[str, Bni0091PortBinding]:
    masters = data.get("masters")
    if not isinstance(masters, list) or not masters:
        return {}

    module_to_binding: dict[str, Bni0091PortBinding] = {}
    for master in masters:
        if not isinstance(master, dict):
            continue

        iol_master_tag = str(master.get("iol_master_tag", "")).strip()
        if not iol_master_tag:
            continue
        station_id_match = re.search(r"(\d{5})", iol_master_tag)
        if station_id_match:
            station_id = station_id_match.group(1)
        else:
            hm = str(master.get("hm", "")).strip()
            station_id = f"{hm}00" if hm else ""
        if not station_id:
            continue

        connections = master.get("connections", [])
        if not isinstance(connections, list):
            continue

        for connection in connections:
            if not isinstance(connection, dict):
                continue
            target_type = str(connection.get("target_type", "")).strip()
            if target_type != "BNI0091":
                continue

            target_module = str(connection.get("target_module", "")).strip()
            if not target_module:
                target = str(connection.get("target", "")).strip()
                if target:
                    target_module = f"{target}00OM"
            if not target_module:
                continue

            port = int(connection.get("port", 0))
            master_port_tag = f"_{station_id}IOL_P{port + 1}"
            module_to_binding[target_module] = Bni0091PortBinding(
                module_name=target_module,
                master_port_tag=master_port_tag,
            )

    return module_to_binding


def _load_output_bridges(
    stations_data: dict[str, Any],
    module_bindings: dict[str, Bni0091PortBinding],
) -> list[OutputBridge]:
    balluff = stations_data.get("balluff")
    if not isinstance(balluff, dict):
        return []

    blocks = balluff.get("blocks")
    if not isinstance(blocks, list):
        return []

    bridges: list[OutputBridge] = []

    for block in blocks:
        if not isinstance(block, dict):
            continue
        module_name = str(block.get("module", "")).strip()
        if not module_name:
            continue

        binding = module_bindings.get(module_name)
        if binding is None:
            continue

        outputs = block.get("outputs")
        if not isinstance(outputs, list):
            continue

        for point in outputs:
            if not isinstance(point, dict):
                continue

            source_tag = str(point.get("source_tag", "")).strip()
            if not source_tag:
                continue

            port = int(point.get("port", -1))
            if port < 0:
                continue

            channel = str(point.get("channel", "")).strip().upper()
            bit = 1 if channel == "B" else 0
            dest_bit_tag = f"{binding.master_port_tag}.Outputs[{port}].{bit}"

            point_id = str(point.get("point_id", "")).strip()
            zone = str(point.get("zone", "")).strip()
            function = str(point.get("function", "")).strip()
            comment = (
                f"Bridge {module_name} {point_id} zone {zone} {function} -> "
                f"{binding.master_port_tag}.Outputs[{port}].{bit}"
            )

            bridges.append(OutputBridge(source_tag=source_tag, dest_bit_tag=dest_bit_tag, comment=comment))

    return bridges


def _make_rung(number: int, text: str, comment: str | None = None) -> etree._Element:
    rung = etree.Element("Rung", Number=str(number), Type="N")
    if comment:
        comment_el = etree.SubElement(rung, "Comment")
        comment_el.text = etree.CDATA(comment)
    text_el = etree.SubElement(rung, "Text")
    text_el.text = etree.CDATA(text)
    return rung


def _aoi_call_text(master: MasterConfig) -> str:
    ports = ",".join(f"{master.port_prefix}{p}" for p in range(1, 9))
    return (
        f"AOI_BNI006A_50_31_040({master.aoi_tag},{master.aoi_tag}:I.Data,"
        f"{master.aoi_tag}:O.Data,{master.aoi_tag}:C.Data,{ports},{master.mapped_tag});"
    )


def _iolink_mode_text(master: MasterConfig, port: int) -> str:
    return f"MOV(1,{master.mapped_tag}.C.Port_{port}_Function);"


def _bridge_set_text(bridge: OutputBridge) -> str:
    return f"XIC({bridge.source_tag})OTL({bridge.dest_bit_tag});"


def _bridge_clear_text(bridge: OutputBridge) -> str:
    return f"XIO({bridge.source_tag})OTU({bridge.dest_bit_tag});"


def _find_rll_content(root: etree._Element) -> etree._Element:
    rll_nodes = root.xpath("//Routine[@Use='Target' and @Type='RLL']/RLLContent")
    if not rll_nodes:
        rll_nodes = root.xpath("//Routine[@Type='RLL']/RLLContent")
    if not rll_nodes:
        raise RuntimeError("Could not find target RLLContent in template L5X.")
    return rll_nodes[0]


def _replace_rungs(
    rll: etree._Element,
    masters: list[MasterConfig],
    bridges: list[OutputBridge],
) -> None:
    for child in list(rll):
        rll.remove(child)

    rung_number = 0
    for master in masters:
        rll.append(
            _make_rung(
                rung_number,
                "NOP();",
                f"Station {master.station_id} IO-Link Master Configuration Logic",
            )
        )
        rung_number += 1
        rll.append(_make_rung(rung_number, _aoi_call_text(master)))
        rung_number += 1
        for port in range(1, 9):
            rll.append(_make_rung(rung_number, _iolink_mode_text(master, port)))
            rung_number += 1

    if bridges:
        rll.append(_make_rung(rung_number, "NOP();", "BNI0091 output bridges into IO-Link master port payload"))
        rung_number += 1
        for bridge in bridges:
            rll.append(_make_rung(rung_number, _bridge_set_text(bridge), bridge.comment))
            rung_number += 1
            rll.append(_make_rung(rung_number, _bridge_clear_text(bridge)))
            rung_number += 1


def _replace_master_tags(root: etree._Element, masters: list[MasterConfig]) -> None:
    tags_nodes = root.xpath("//Controller/Tags")
    if not tags_nodes:
        raise RuntimeError("Could not find <Tags> section in template L5X.")
    tags_node = tags_nodes[0]

    aoi_proto = None
    mapped_proto = None
    for tag in tags_node.findall("Tag"):
        name = tag.get("Name", "")
        if name == "StaXXXX_IOL_Master":
            aoi_proto = tag
        elif name == "StaXXXX_IOL_Master_Mapped_Data":
            mapped_proto = tag

    if aoi_proto is None or mapped_proto is None:
        # Finalized template exports can already contain concrete _#####IOL tags
        # instead of placeholder prototypes. In that case, keep template tags as-is.
        return

    aoi_index = list(tags_node).index(aoi_proto)
    mapped_index = list(tags_node).index(mapped_proto)
    insert_at = min(aoi_index, mapped_index)

    tags_node.remove(aoi_proto)
    tags_node.remove(mapped_proto)

    for master in masters:
        aoi_tag = etree.fromstring(etree.tostring(aoi_proto))
        mapped_tag = etree.fromstring(etree.tostring(mapped_proto))
        aoi_tag.set("Name", master.aoi_tag)
        mapped_tag.set("Name", master.mapped_tag)

        tags_node.insert(insert_at, aoi_tag)
        insert_at += 1
        tags_node.insert(insert_at, mapped_tag)
        insert_at += 1


def generate_from_template(
    iol_masters_toml: Path,
    stations_toml: Path,
    template_l5x: Path,
    output_file: Path,
) -> Path:
    data = _read_toml(iol_masters_toml)
    stations_data = _read_toml(stations_toml)
    masters = _load_masters(data)
    module_bindings = _load_bni0091_port_bindings(data)
    bridges = _load_output_bridges(stations_data, module_bindings)

    parser = etree.XMLParser(remove_blank_text=False)
    tree = etree.parse(str(template_l5x), parser)
    root = tree.getroot()

    root.set("ExportDate", datetime.now().strftime("%a %b %d %H:%M:%S %Y"))

    _replace_master_tags(root, masters)
    rll = _find_rll_content(root)
    _replace_rungs(rll, masters, bridges)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    tree.write(
        str(output_file),
        xml_declaration=True,
        encoding="UTF-8",
        standalone=True,
        pretty_print=True,
    )

    return output_file


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate IOL masters routine L5X by replacing rung content in a template file."
    )
    parser.add_argument(
        "--iol-masters",
        type=Path,
        default=DEFAULT_IOL_MASTERS_TOML,
        help="Path to IOL_masters TOML.",
    )
    parser.add_argument(
        "--template",
        type=Path,
        default=DEFAULT_TEMPLATE_L5X,
        help="Path to template Routine L5X to clone and update.",
    )
    parser.add_argument(
        "--stations",
        type=Path,
        default=DEFAULT_STATIONS_TOML,
        help="Path to stations IO map TOML used for BNI0091 output bridge generation.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_DIR / DEFAULT_OUTPUT_NAME,
        help="Output L5X path.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    out = generate_from_template(args.iol_masters, args.stations, args.template, args.output)

    print(f"Template: {args.template}")
    print(f"IOL masters TOML: {args.iol_masters}")
    print(f"Stations TOML: {args.stations}")
    print(f"Wrote: {out}")


if __name__ == "__main__":
    main()
