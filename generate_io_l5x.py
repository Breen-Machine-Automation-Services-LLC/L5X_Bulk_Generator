"""Generate Program L5X containing only IO_Simulate routine logic.

This script uses input/stations.toml as the single source of station
relationships and emits:
- Program-level TIMER tags used by simulation rungs.
- A single RLL routine named IO_Simulate.

It intentionally does NOT generate IO_Map or IOL_Masters.
"""

# Standard library imports
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

# Third-party imports
import lxml.etree as etree
import tomllib

# Local application imports

# Constants
STATIONS_TOML = Path("input/stations.toml")
OUTPUT_DIR = Path("output")
OUTPUT_FILE_STEM = "IO_Simulate"
DEFAULT_TIMER_PRESET_MS = 3000
ROUTINE_NAME = "IO_Simulate"
CONTROLLER_NAME = "Wolf_HybridMainLine"
PROGRAM_NAME = "MainProgram"


@dataclass(frozen=True)
class StationDef:
    number: int
    station_type: str
    prefix: str
    prev: int | None
    next: int | None
    conv_upstream: int | None
    conv_downstream: int | None
    chain_upstream: int | None
    chain_downstream: int | None

    @property
    def is_transfer(self) -> bool:
        return self.station_type == "Transfer"


Axis = Literal["conv", "chain"]
Relation = Literal["upstream", "downstream"]


def _endpoint_relation(station: StationDef, other_station_num: int) -> tuple[Axis, Relation] | None:
    if not station.is_transfer:
        if other_station_num == station.prev:
            return ("conv", "upstream")
        if other_station_num == station.next:
            return ("conv", "downstream")
        return None

    if other_station_num in {station.conv_upstream, station.conv_downstream}:
        rel: Relation = "upstream" if other_station_num == station.conv_upstream else "downstream"
        return ("conv", rel)
    if other_station_num in {station.chain_upstream, station.chain_downstream}:
        rel = "upstream" if other_station_num == station.chain_upstream else "downstream"
        return ("chain", rel)

    return None


def _endpoint_axis(station: StationDef, other_station_num: int) -> Axis:
    relation = _endpoint_relation(station, other_station_num)
    if relation is None:
        return "conv"
    return relation[0]


def _run_tag(station: StationDef, axis: Axis) -> str:
    if station.is_transfer and axis == "chain":
        return f"CT{station.number}_Chain_Run"
    return f"{station.prefix}{station.number}_Conv_Run"


def _dir_tag(station: StationDef, axis: Axis) -> str:
    if station.is_transfer and axis == "chain":
        return f"CT{station.number}_Chain_Dir"
    return f"{station.prefix}{station.number}_Conv_Dir"


def _presence_tag(station: StationDef, axis: Axis) -> str:
    if not station.is_transfer:
        return f"{station.prefix}{station.number}_PE_FE"
    if axis == "chain":
        return f"CT{station.number}_FE_Chain"
    return f"CT{station.number}_FE_Conv"


def _source_presence_tags(station: StationDef) -> list[str]:
    if station.is_transfer:
        return [
            f"CT{station.number}_FE_Conv",
            f"CT{station.number}_RE_Conv",
            f"CT{station.number}_FE_Chain",
            f"CT{station.number}_RE_Chain",
        ]
    return [
        f"{station.prefix}{station.number}_PE_FE",
        f"{station.prefix}{station.number}_PE_RE",
    ]


def _dir_instr(station: StationDef, other_station_num: int, role: str) -> str:
    relation = _endpoint_relation(station, other_station_num)
    if relation is None:
        return "XIO"

    _, rel = relation

    # For source side (A -> B): downstream is forward, upstream is reverse.
    # For destination side (B receiving from A): polarity is opposite.
    if role == "src":
        return "XIO" if rel == "downstream" else "XIC"
    return "XIO" if rel == "upstream" else "XIC"


def _read_toml(path: Path) -> dict:
    with open(path, "rb") as f:
        raw = f.read()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return tomllib.loads(raw.decode("utf-8"))


def _normalize_int(value: object | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, int) and value == 0:
        return None
    if isinstance(value, int):
        return value
    raise RuntimeError(f"Expected int or None for relationship field, got: {value!r}")


def _station_prefix(row: dict) -> tuple[str, str]:
    raw_type = str(row["type"])
    is_workstation = bool(row.get("isWorkstation", False))
    is_test_station = bool(row.get("isTestStation", False))

    if raw_type == "Transfer":
        return "Transfer", "CT"
    if raw_type == "Lift":
        if not is_workstation:
            raise RuntimeError(f"Station {row['number']} is Lift but isWorkstation is not true.")
        return "Lift", "Li"
    if raw_type == "Queue":
        if is_test_station:
            return "TestStation", "ST"
        if is_workstation:
            return "Workstation", "ST"
        return "Queue", "ST"

    raise RuntimeError(f"Unsupported station type '{raw_type}' at station {row['number']}")


def load_stations(path: Path) -> dict[int, StationDef]:
    data = _read_toml(path)

    rows: list[dict] = data["stations"]["data"]
    stations: dict[int, StationDef] = {}

    for row in rows:
        number = int(row["number"])
        if number in stations:
            raise RuntimeError(f"Duplicate station number: {number}")

        station_type, prefix = _station_prefix(row)
        stations[number] = StationDef(
            number=number,
            station_type=station_type,
            prefix=prefix,
            prev=_normalize_int(row.get("prev")),
            next=_normalize_int(row.get("next")),
            conv_upstream=_normalize_int(row.get("conv_upstream")),
            conv_downstream=_normalize_int(row.get("conv_downstream")),
            chain_upstream=_normalize_int(row.get("chain_upstream")),
            chain_downstream=_normalize_int(row.get("chain_downstream")),
        )

    return stations


def build_directed_edges(stations: dict[int, StationDef]) -> list[tuple[int, int]]:
    edges: set[tuple[int, int]] = set()

    for s in stations.values():
        if s.is_transfer:
            if s.conv_upstream is not None:
                edges.add((s.conv_upstream, s.number))
            if s.conv_downstream is not None:
                edges.add((s.number, s.conv_downstream))
            if s.chain_upstream is not None:
                edges.add((s.chain_upstream, s.number))
            if s.chain_downstream is not None:
                edges.add((s.number, s.chain_downstream))
        else:
            if s.prev is not None:
                edges.add((s.prev, s.number))
            if s.next is not None:
                edges.add((s.number, s.next))

    resolved_pairs: set[tuple[int, int]] = set()
    skipped = 0
    for src, dst in sorted(edges):
        if src not in stations or dst not in stations:
            skipped += 1
            continue
        resolved_pairs.add((src, dst))

    # Ensure reverse-direction movement is always generated for internal pairs.
    resolved: set[tuple[int, int]] = set()
    for src, dst in resolved_pairs:
        if src == dst:
            continue
        resolved.add((src, dst))
        resolved.add((dst, src))

    if skipped:
        print(f"Skipped {skipped} edge(s) pointing to non-generated stations.")

    return sorted(resolved)


def _make_timer_tag(name: str, preset_ms: int) -> etree._Element:
    tag = etree.Element(
        "Tag",
        Name=name,
        Class="Standard",
        TagType="Base",
        DataType="TIMER",
        Constant="false",
        ExternalAccess="Read/Write",
    )

    data_l5k = etree.SubElement(tag, "Data", Format="L5K")
    data_l5k.text = etree.CDATA(f"[0,{preset_ms},0]")

    data_decorated = etree.SubElement(tag, "Data", Format="Decorated")
    structure = etree.SubElement(data_decorated, "Structure", DataType="TIMER")
    etree.SubElement(
        structure,
        "DataValueMember",
        Name="PRE",
        DataType="DINT",
        Radix="Decimal",
        Value=str(preset_ms),
    )
    etree.SubElement(
        structure,
        "DataValueMember",
        Name="ACC",
        DataType="DINT",
        Radix="Decimal",
        Value="0",
    )
    etree.SubElement(structure, "DataValueMember", Name="EN", DataType="BOOL", Value="0")
    etree.SubElement(structure, "DataValueMember", Name="TT", DataType="BOOL", Value="0")
    etree.SubElement(structure, "DataValueMember", Name="DN", DataType="BOOL", Value="0")
    return tag


def _make_rung(number: int, text: str) -> etree._Element:
    rung = etree.Element("Rung", Number=str(number), Type="N")
    text_el = etree.SubElement(rung, "Text")
    text_el.text = etree.CDATA(text)
    return rung


def _build_program_root() -> tuple[etree._Element, etree._Element, etree._Element]:
    root = etree.Element(
        "RSLogix5000Content",
        SchemaRevision="1.0",
        SoftwareRevision="34.03",
        TargetName=PROGRAM_NAME,
        TargetType="Program",
        TargetClass="Standard",
        ContainsContext="true",
        ExportDate=datetime.now().strftime("%a %b %d %H:%M:%S %Y"),
        ExportOptions="References NoRawData L5KData DecoratedData Context Dependencies ForceProtectedEncoding AllProjDocTrans",
    )

    controller = etree.SubElement(root, "Controller", Use="Context", Name=CONTROLLER_NAME)
    programs = etree.SubElement(controller, "Programs", Use="Context")
    program = etree.SubElement(
        programs,
        "Program",
        Use="Target",
        Name=PROGRAM_NAME,
        TestEdits="false",
        MainRoutineName="MainRoutine",
        Disabled="false",
        Class="Standard",
        UseAsFolder="false",
    )
    tags = etree.SubElement(program, "Tags")
    routines = etree.SubElement(program, "Routines")
    return root, tags, routines


def build_simulation_rungs(
    stations: dict[int, StationDef],
    edges: list[tuple[int, int]],
    preset_ms: int,
) -> tuple[list[str], list[str]]:
    timer_names: set[str] = set()
    rung_texts: list[str] = ["NOP();"]

    for src_num, dst_num in edges:
        src = stations[src_num]
        dst = stations[dst_num]
        src_axis = _endpoint_axis(src, dst_num)
        dst_axis = _endpoint_axis(dst, src_num)
        timer = f"T{src_num}to{dst_num}"
        src_dir_instr = _dir_instr(src, dst_num, role="src")
        dst_dir_instr = _dir_instr(dst, src_num, role="dst")
        src_otu_text = "".join(f"OTU({tag})" for tag in _source_presence_tags(src))
        timer_names.add(timer)

        rung_texts.append(
            (
                f"XIC({_run_tag(src, src_axis)})"
                f"{src_dir_instr}({_dir_tag(src, src_axis)})"
                f"XIC({_run_tag(dst, dst_axis)})"
                f"{dst_dir_instr}({_dir_tag(dst, dst_axis)})"
                f"TON({timer},{preset_ms},0)"
                f"XIC({timer}.DN)"
                f"{src_otu_text}"
                f"OTL({_presence_tag(dst, dst_axis)});"
            )
        )

    for s in sorted(stations.values(), key=lambda x: x.number):
        if not s.is_transfer:
            continue

        up_timer = f"CT{s.number}_T_Transfer_Up"
        down_timer = f"CT{s.number}_T_Transfer_Down"
        timer_names.add(up_timer)
        timer_names.add(down_timer)

        rung_texts.append(
            (
                f"[XIO(CT{s.number}_Sol_Transfer_Up) "
                f"TON({down_timer},{preset_ms},0) "
                f"XIC({down_timer}.DN) "
                f"OTE(CT{s.number}_Px_Transfer_Down) ,"
                f"XIC(CT{s.number}_Sol_Transfer_Up) "
                f"TON({up_timer},{preset_ms},0) "
                f"XIC({up_timer}.DN) "
                f"OTE(CT{s.number}_Px_Transfer_Up) ];"
            )
        )

    return sorted(timer_names), rung_texts


def write_program_l5x(
    timer_names: list[str],
    rung_texts: list[str],
) -> Path:
    root, tags, routines = _build_program_root()

    for timer_name in timer_names:
        tags.append(_make_timer_tag(timer_name, DEFAULT_TIMER_PRESET_MS))

    # Keep MainRoutine present so MainRoutineName always resolves on import.
    etree.SubElement(routines, "Routine", Name="MainRoutine", Type="RLL")

    routine = etree.SubElement(routines, "Routine", Name=ROUTINE_NAME, Type="RLL")
    rll = etree.SubElement(routine, "RLLContent")
    for idx, text in enumerate(rung_texts):
        rll.append(_make_rung(idx, text))

    OUTPUT_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().isoformat(timespec="seconds").replace(":", "-")
    out_path = OUTPUT_DIR / f"{OUTPUT_FILE_STEM}_{timestamp}.L5X"
    etree.ElementTree(root).write(
        str(out_path),
        xml_declaration=True,
        encoding="UTF-8",
        standalone=True,
        pretty_print=True,
    )
    return out_path


def main() -> None:
    stations = load_stations(STATIONS_TOML)
    edges = build_directed_edges(stations)
    timer_names, rung_texts = build_simulation_rungs(stations, edges, DEFAULT_TIMER_PRESET_MS)
    out = write_program_l5x(timer_names, rung_texts)

    print(f"Stations loaded: {len(stations)}")
    print(f"Directed movement edges simulated: {len(edges)}")
    print(f"Timer tags generated: {len(timer_names)}")
    print(f"IO_Simulate rungs generated: {len(rung_texts)}")
    print(f"Wrote: {out}")


if __name__ == "__main__":
    main()
