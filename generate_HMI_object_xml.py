"""Regenerate GO_Conv groups in MAIN.xml from stations.toml.

Keep the FactoryTalk display aligned with the station source of truth.
"""

from __future__ import annotations

# Standard library imports
from copy import deepcopy
from pathlib import Path

# Third-party imports
import lxml.etree as etree

# Local application imports
from generate_io_l5x import STATIONS_TOML, load_stations

MAIN_XML = Path("reference/MAIN.xml")
GO_GROUP_PREFIX = "GO_Conv"
TEMPLATE_GROUP_NAME = "GO_Conv4000"
TEMPLATE_STATION = "4000"


def _parse_main_xml(path: Path) -> etree._ElementTree:
    parser = etree.XMLParser(remove_blank_text=False)
    return etree.parse(str(path), parser)


def _iter_go_conv_groups(root: etree._Element) -> list[etree._Element]:
    return [
        child
        for child in root
        if child.tag == "group" and child.get("name", "").startswith(GO_GROUP_PREFIX)
    ]


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
    template: etree._Element, station_number: int
) -> etree._Element:
    group = deepcopy(template)
    _replace_station_tokens(group, TEMPLATE_STATION, str(station_number))
    return group


def regenerate_main_xml(main_xml_path: Path, station_numbers: list[int]) -> Path:
    tree = _parse_main_xml(main_xml_path)
    root = tree.getroot()

    existing_groups = _iter_go_conv_groups(root)
    if not existing_groups:
        raise RuntimeError("No GO_Conv groups found in MAIN.xml.")

    template_group = _find_template_group(root)
    insertion_index = root.index(existing_groups[0])
    template_tail = existing_groups[0].tail

    for group in existing_groups:
        root.remove(group)

    for offset, station_number in enumerate(station_numbers):
        group = _build_station_group(template_group, station_number)
        group.tail = template_tail
        root.insert(insertion_index + offset, group)

    tree.write(
        str(main_xml_path),
        xml_declaration=True,
        encoding="UTF-8",
        pretty_print=False,
    )
    return main_xml_path


def main() -> None:
    stations = load_stations(STATIONS_TOML)
    station_numbers = sorted(stations)
    out_path = regenerate_main_xml(MAIN_XML, station_numbers)

    print(f"Stations loaded: {len(station_numbers)}")
    print(f"GO_Conv groups written: {len(station_numbers)}")
    print(f"Wrote: {out_path}")


if __name__ == "__main__":
    main()
