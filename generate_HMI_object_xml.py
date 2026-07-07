"""Regenerate GO_Conv groups in MAIN.xml from stations.toml.

Keep the FactoryTalk display aligned with the station source of truth.
"""

from __future__ import annotations

# Standard library imports
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Third-party imports
import lxml.etree as etree
import tomllib

# Local application imports

# Constants
STATIONS_TOML = Path("input/stations.toml")
OUTPUT_DIR = Path("output")
GO_GROUP_PREFIX = "GO_Conv"
TEMPLATE_GROUP_NAME = "GO_Conv4000"
TEMPLATE_STATION = "4000"
POPUP_STRAIGHT = "300_Pop_StraightTrack"
POPUP_LIFT = "301_Pop_Lift"
POPUP_CHAIN_TRANSFER = "303_Pop_ChainTransfer"


@dataclass(frozen=True)
class DisplayConfig:
    template_path: Path
    output_stem: str
    set_station_number_param: bool
    set_popup_param: bool


DISPLAY_CONFIGS = [
    DisplayConfig(
        template_path=Path("reference/MAIN.xml"),
        output_stem="Level2",
        set_station_number_param=False,
        set_popup_param=True,
    ),
    DisplayConfig(
        template_path=Path("reference/Level1.xml"),
        output_stem="Level1",
        set_station_number_param=True,
        set_popup_param=False,
    ),
]


@dataclass(frozen=True)
class StationInfo:
    number: int
    prefix: str


def _read_toml(path: Path) -> dict:
    with open(path, "rb") as f:
        raw = f.read()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return tomllib.loads(raw.decode("utf-8"))


def _station_prefix_from_row(row: dict) -> str:
    raw_type = str(row.get("type", "")).strip()
    if raw_type == "Transfer":
        return "CT"
    if raw_type == "Lift":
        return "Li"
    return "ST"


def load_hmi_stations(path: Path) -> dict[int, StationInfo]:
    data = _read_toml(path)
    rows = data["stations"]["data"]

    stations: dict[int, StationInfo] = {}
    for row in rows:
        number = int(row["number"])
        if number in stations:
            raise RuntimeError(f"Duplicate station number in TOML: {number}")
        stations[number] = StationInfo(
            number=number,
            prefix=_station_prefix_from_row(row),
        )
    return stations


def _parse_display_xml(path: Path) -> etree._ElementTree:
    parser = etree.XMLParser(remove_blank_text=False)
    return etree.parse(str(path), parser)


def _iter_go_conv_groups(root: etree._Element) -> list[etree._Element]:
    return [child for child in root if child.tag == "group" and child.get("name", "").startswith(GO_GROUP_PREFIX)]


def _find_template_group(root: etree._Element) -> etree._Element:
    for group in _iter_go_conv_groups(root):
        if group.get("name") == TEMPLATE_GROUP_NAME:
            return group
    raise RuntimeError(f"Could not find template group {TEMPLATE_GROUP_NAME!r}.")


def _replace_station_tokens(
    node: etree._Element,
    old_station: str,
    new_station: str,
) -> None:
    for attr_name, attr_value in node.attrib.items():
        if old_station in attr_value:
            node.set(attr_name, attr_value.replace(old_station, new_station))

    if node.text and old_station in node.text:
        node.text = node.text.replace(old_station, new_station)

    for child in node:
        _replace_station_tokens(child, old_station, new_station)


def _build_station_group(
    template: etree._Element,
    station_number: int,
    machine_name: str,
    popup_name: str,
    set_station_number_param: bool,
    set_popup_param: bool,
) -> etree._Element:
    group = deepcopy(template)
    _replace_station_tokens(group, TEMPLATE_STATION, str(station_number))

    machine_name_param = group.find("./parameters/parameter[@name='#2']")
    if machine_name_param is None:
        raise RuntimeError("Template is missing parameter #2 for machine name.")
    machine_name_param.set("value", machine_name)

    if set_station_number_param:
        station_number_param = group.find("./parameters/parameter[@name='#3']")
        if station_number_param is None:
            raise RuntimeError("Template is missing parameter #3 for station number.")
        station_number_param.set("value", str(station_number))

    if set_popup_param:
        popup_param = group.find("./parameters/parameter[@name='#4']")
        if popup_param is None:
            raise RuntimeError("Template is missing parameter #4 for popup name.")
        popup_param.set("value", popup_name)

    return group


def generate_display_xml(
    config: DisplayConfig,
    stations: dict[int, StationInfo],
    timestamp: str,
) -> Path:
    tree = _parse_display_xml(config.template_path)
    root = tree.getroot()

    existing_groups = _iter_go_conv_groups(root)
    if not existing_groups:
        raise RuntimeError(f"No GO_Conv groups found in {config.template_path}.")

    template_group = _find_template_group(root)
    insertion_index = root.index(existing_groups[0])
    template_tail = existing_groups[0].tail

    for group in existing_groups:
        root.remove(group)

    station_numbers = sorted(stations, reverse=True)
    for offset, station_number in enumerate(station_numbers):
        station = stations[station_number]
        machine_name = f"{station.prefix}{station_number}"
        if station.prefix == "Li":
            popup_name = POPUP_LIFT
        elif station.prefix == "CT":
            popup_name = POPUP_CHAIN_TRANSFER
        else:
            popup_name = POPUP_STRAIGHT
        group = _build_station_group(
            template_group,
            station_number,
            machine_name,
            popup_name,
            config.set_station_number_param,
            config.set_popup_param,
        )
        group.tail = template_tail
        root.insert(insertion_index + offset, group)

    OUTPUT_DIR.mkdir(exist_ok=True)
    out_path = OUTPUT_DIR / f"{config.output_stem}_{timestamp}.xml"
    tree.write(
        str(out_path),
        xml_declaration=True,
        encoding="UTF-8",
        pretty_print=False,
    )
    return out_path


def main() -> None:
    stations = load_hmi_stations(STATIONS_TOML)
    station_numbers = sorted(stations, reverse=True)
    timestamp = datetime.now().isoformat(timespec="seconds").replace(":", "-")
    out_paths = [generate_display_xml(config, stations, timestamp) for config in DISPLAY_CONFIGS]

    print(f"Stations loaded: {len(station_numbers)}")
    print(f"GO_Conv groups written: {len(station_numbers)}")
    for out_path in out_paths:
        print(f"Wrote: {out_path}")


if __name__ == "__main__":
    main()
