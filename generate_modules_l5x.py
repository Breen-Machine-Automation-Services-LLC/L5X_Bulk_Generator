"""Generate a Module-targeted L5X from a strict TOML station list.

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
DEFAULT_TOML_FILE = Path("input/HM_Network1.toml")


# ---------------------------------------------------------------------------

def load_toml(path: Path) -> dict:
    with open(path, "rb") as f:
        raw = f.read()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return tomllib.loads(raw.decode("utf-8"))


def load_target_module(template_path: Path) -> etree._Element:
    root = etree.parse(template_path).getroot()
    module = root.find(".//Module[@Use='Target']")
    if module is None:
        raise RuntimeError(f"{template_path} has no <Module Use='Target'> element.")
    return module


def make_device_module(
    device_template: etree._Element,
    name: str,
    ip: str,
    parent_module: str,
    parent_mod_port: int,
) -> etree._Element:
    """Clone a device template and apply station name, IP, and parent binding."""
    el = deepcopy(device_template)
    el.set("Name", name)
    el.set("ParentModule", parent_module)
    el.set("ParentModPortId", str(parent_mod_port))
    el.attrib.pop("Use", None)
    for port in el.findall(".//Port[@Type='Ethernet']"):
        port.set("Address", ip)
    return el


def make_dlr_module(
    dlr_template: etree._Element,
    name: str,
    parent_module: str,
    parent_mod_port: int,
    icp_slot: int,
    ethernet_ip: str,
) -> etree._Element:
    """Clone parent template and apply name, parent binding, slot, and IP."""
    el = deepcopy(dlr_template)
    el.set("Name", name)
    el.set("ParentModule", parent_module)
    el.set("ParentModPortId", str(parent_mod_port))
    el.set("Use", "Target")
    for port in el.findall(".//Port[@Type='ICP']"):
        port.set("Address", str(icp_slot))
    for port in el.findall(".//Port[@Type='Ethernet']"):
        port.set("Address", ethernet_ip)
    return el


def build_module_export(
    dlr_root: etree._Element,
    dlr_module: etree._Element,
    device_modules: list[etree._Element],
    controller_name: str,
) -> etree._Element:
    """Produce TargetType=Module L5X with one parent and many child modules."""
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
    for m in device_modules:
        mods_el.append(m)

    return root


def main() -> None:
    data = load_toml(DEFAULT_TOML_FILE)
    cfg = data["config"]
    templates_cfg = data["templates"]
    root_cfg = data["root_module"]
    stations = data["stations"]["data"]

    controller_name = cfg["controller_name"]
    output_name = cfg["output_file"]

    output_dir = Path(cfg.get("output_dir", "output"))

    parent_template_path = Path(templates_cfg["parent_template"])
    device_templates: dict[str, str] = templates_cfg["device_templates"]

    dlr_name = root_cfg["name"]
    dlr_parent_module = root_cfg["parent_module"]
    dlr_parent_mod_port = int(root_cfg["parent_mod_port"])
    dlr_icp_slot = int(root_cfg["icp_slot"])
    dlr_eth_ip = root_cfg["ethernet_ip"]

    dlr_root = etree.parse(parent_template_path).getroot()
    dlr_template = dlr_root.find(".//Module[@Use='Target']")
    if dlr_template is None:
        raise RuntimeError(f"{parent_template_path} has no <Module Use='Target'> element.")

    dlr_module = make_dlr_module(
        dlr_template,
        dlr_name,
        dlr_parent_module,
        dlr_parent_mod_port,
        dlr_icp_slot,
        dlr_eth_ip,
    )

    cached_templates: dict[str, etree._Element] = {}
    device_modules: list[etree._Element] = []
    for s in stations:
        device_type = s["device_type"]
        template_rel = device_templates[device_type]
        if device_type not in cached_templates:
            cached_templates[device_type] = load_target_module(Path(template_rel))

        station_name = s["module_name"]
        station_ip = s["ip"]

        device_modules.append(
            make_device_module(
                cached_templates[device_type],
                station_name,
                station_ip,
                dlr_name,
                2,
            )
        )

    combined = build_module_export(dlr_root, dlr_module, device_modules, controller_name)

    output_dir.mkdir(exist_ok=True)
    out_path = output_dir / output_name
    etree.ElementTree(combined).write(
        str(out_path),
        xml_declaration=True,
        encoding="UTF-8",
        standalone=True,
        pretty_print=True,
    )

    print(f"Wrote {len(device_modules)} modules -> {out_path}\n")
    for s in stations:
        print(f"  {s['module_name']}  {s['ip']}  ({s['device_type']})")


if __name__ == "__main__":
    main()
