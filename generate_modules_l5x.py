"""Generate an IB-E03B module L5X from a TOML station list.

Outputs a Module-targeted L5X (TargetType="Module") rooted at the DLR
parent bridge. Studio 5000 imports it via right-click on the DLR in the
I/O tree, which adds all IB child modules in one shot.

Change TOML_FILE at the top for each new panel.
"""
from __future__ import annotations

import tomllib
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from lxml import etree

# --- paths (edit per panel run) --------------------------------------------
TOML_FILE     = Path("input/panel2.toml")
IB_TEMPLATE   = Path("input/IBXXXX_Module.L5X")
DLR_TEMPLATE  = Path("input/DLR4000_Module.L5X")
OUTPUT_DIR    = Path("Output")


# ---------------------------------------------------------------------------

def load_toml(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def assign_ips(stations: list[dict], ip_prefix: str, ip_start: int) -> list[str]:
    """Sequential IPs from ip_start.

    When a station has gap=true, the current address is rounded up to the
    next multiple of 10 before assigning (e.g. 125 → 130). Gap is ignored
    on the first station.
    """
    ips: list[str] = []
    current = ip_start
    for s in stations:
        if s.get("gap", False) and ips:
            current = ((current + 9) // 10) * 10
        ips.append(f"{ip_prefix}.{current}")
        current += 1
    return ips


def make_ib_module(
    ib_template: etree._Element,
    name: str,
    ip: str,
    parent_module: str,
) -> etree._Element:
    """Clone IB template, set name/IP/parent. Strips Use attr (context child)."""
    el = deepcopy(ib_template)
    el.set("Name", name)
    el.set("ParentModule", parent_module)
    el.set("AutoDiagsEnabled", "true")
    el.attrib.pop("Use", None)
    for port in el.findall(".//Port[@Type='Ethernet']"):
        port.set("Address", ip)
    return el


def make_dlr_module(
    dlr_template: etree._Element,
    name: str,
    icp_slot: str,
    ethernet_ip: str,
) -> etree._Element:
    """Clone DLR template and apply name/slot/IP. Sets Use='Target'."""
    el = deepcopy(dlr_template)
    el.set("Name", name)
    el.set("Use", "Target")
    for port in el.findall(".//Port[@Type='ICP']"):
        port.set("Address", str(icp_slot))
    for port in el.findall(".//Port[@Type='Ethernet']"):
        port.set("Address", ethernet_ip)
    return el


def build_module_export(
    dlr_root: etree._Element,
    dlr_module: etree._Element,
    ib_modules: list[etree._Element],
    controller_name: str,
) -> etree._Element:
    """Produce TargetType=Module L5X with DLR as target and IBs as context siblings."""
    root = deepcopy(dlr_root)
    root.set("TargetName", dlr_module.get("Name"))
    root.set("TargetType", "Module")
    root.set("ContainsContext", "true")
    root.set("ExportDate", datetime.now().strftime("%a %b %d %H:%M:%S %Y"))

    ctrl = root.find("Controller")
    ctrl.set("Use", "Context")
    ctrl.set("Name", controller_name)

    mods_el = ctrl.find("Modules")
    if mods_el is None:
        mods_el = etree.SubElement(ctrl, "Modules")
    mods_el.set("Use", "Context")
    for child in list(mods_el):
        mods_el.remove(child)

    mods_el.append(dlr_module)
    for m in ib_modules:
        mods_el.append(m)

    return root


def main() -> None:
    data = load_toml(TOML_FILE)
    cfg      = data["config"]
    dlr_cfg  = data["dlr"]
    stations = data["station"]

    controller_name = cfg["controller_name"]
    ip_prefix       = cfg["ip_prefix"]
    ip_start        = int(cfg["ip_start"])
    output_name     = cfg.get("output_file", "IB_Modules.L5X")

    dlr_name     = dlr_cfg["name"]
    dlr_icp_slot = str(dlr_cfg["icp_slot"])
    dlr_eth_ip   = dlr_cfg["ethernet_ip"]

    ips = assign_ips(stations, ip_prefix, ip_start)

    ib_root     = etree.parse(IB_TEMPLATE).getroot()
    ib_template = ib_root.find(".//Module[@Use='Target']")
    if ib_template is None:
        raise RuntimeError(f"{IB_TEMPLATE} has no <Module Use='Target'> element.")

    dlr_root     = etree.parse(DLR_TEMPLATE).getroot()
    dlr_template = dlr_root.find(".//Module[@Use='Target']")
    if dlr_template is None:
        raise RuntimeError(f"{DLR_TEMPLATE} has no <Module Use='Target'> element.")

    dlr_module = make_dlr_module(dlr_template, dlr_name, dlr_icp_slot, dlr_eth_ip)

    ib_modules = [
        make_ib_module(ib_template, f"IB{s['number']}", ip, dlr_name)
        for s, ip in zip(stations, ips)
    ]

    combined = build_module_export(dlr_root, dlr_module, ib_modules, controller_name)

    OUTPUT_DIR.mkdir(exist_ok=True)
    out_path = OUTPUT_DIR / output_name
    etree.ElementTree(combined).write(
        str(out_path),
        xml_declaration=True,
        encoding="UTF-8",
        standalone=True,
        pretty_print=True,
    )

    print(f"Wrote {len(ib_modules)} modules → {out_path}\n")
    chain = 1
    for s, ip in zip(stations, ips):
        if s.get("gap", False):
            chain += 1
            print(f"  --- chain {chain} ---")
        print(f"  IB{s['number']}  {ip}  ({s.get('device_id', '')})")


if __name__ == "__main__":
    main()
