"""Generate a Module-targeted L5X from a flat TOML modules list.

The TOML defines one flat list of modules with explicit parent references,
port binding, and addressing so the same schema can generate mixed hierarchies.
"""

# Standard library imports
from __future__ import annotations

import argparse
import tomllib
from copy import deepcopy
from datetime import datetime
from pathlib import Path

# Third-party imports
import lxml.etree as etree

# Local application imports

# Constants
DEFAULT_TOML_FILE = Path("input/Hybrid Main Line/Network.toml")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a Module-targeted L5X from a TOML modules list.")
    parser.add_argument(
        "--toml",
        type=Path,
        default=DEFAULT_TOML_FILE,
        help="Path to modules TOML (default: input/Hybrid Main Line/Network.toml).",
    )
    return parser.parse_args()


def load_toml(path: Path) -> dict:
    with open(path, "rb") as f:
        raw = f.read()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return tomllib.loads(raw.decode("utf-8"))


def load_template_modules(template_path: Path) -> list[etree._Element]:
    root = etree.parse(template_path).getroot()
    modules = root.findall(".//Module")
    if not modules:
        raise RuntimeError(f"{template_path} has no <Module> element.")
    return modules


def make_module(
    module_templates: list[etree._Element],
    *,
    name: str,
    parent_module: str,
    parent_mod_port: int | None,
    address: str | int,
) -> list[etree._Element]:
    """Clone a template set and apply parent binding plus upstream address.
    Child modules (index > 0) have their ParentModule reference updated to match
    the new root module name."""
    root_template = module_templates[0]
    template_root_name = root_template.get("Name", "")

    el = deepcopy(root_template)
    el.set("Name", name)
    el.set("ParentModule", parent_module)
    if parent_module == "Local":
        el.set("Use", "Target")
    else:
        el.attrib.pop("Use", None)
    if parent_mod_port is not None:
        el.set("ParentModPortId", str(parent_mod_port))

    upstream_ports = [p for p in el.findall(".//Port") if p.get("Upstream", "").lower() == "true"]
    if len(upstream_ports) != 1:
        raise RuntimeError(f"Module '{name}' must have exactly one Upstream='true' port.")
    upstream_ports[0].set("Address", str(address))

    result = [el]
    for child_template in module_templates[1:]:
        child = deepcopy(child_template)
        if child.get("ParentModule") == template_root_name:
            child.set("ParentModule", name)
        result.append(child)

    return result


def build_module_export(
    target_root: etree._Element,
    modules: list[etree._Element],
    controller_name: str,
    target_name: str,
) -> etree._Element:
    """Produce TargetType=Module L5X from a flat module list."""
    root = deepcopy(target_root)
    root.set("TargetName", target_name)
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

    for module_el in modules:
        mods_el.append(module_el)

    return root


def main(toml_path: Path) -> None:
    data = load_toml(toml_path)
    cfg = data["config"]
    templates_cfg: dict[str, str] = data["templates"]
    modules_cfg: list[dict] = data["modules"]["data"]

    controller_name = cfg["controller_name"]
    target_name = cfg["target"]
    output_name = cfg["output_file"]
    output_dir = Path(cfg.get("output_dir", "output"))

    names = [m["name"] for m in modules_cfg]
    if len(names) != len(set(names)):
        raise RuntimeError("Duplicate module names found in [modules].data.")
    if target_name not in set(names):
        raise RuntimeError(f"config.target '{target_name}' is not present in [modules].data.")

    target_cfg = next(m for m in modules_cfg if m["name"] == target_name)
    target_type = target_cfg["type"]
    target_template_path = Path(templates_cfg[target_type])
    target_root = etree.parse(target_template_path).getroot()

    cached_templates: dict[str, list[etree._Element]] = {}

    def get_template_for_type(module_type: str) -> etree._Element:
        if module_type not in templates_cfg:
            raise RuntimeError(f"Missing template mapping for type '{module_type}'.")
        if module_type not in cached_templates:
            cached_templates[module_type] = load_template_modules(Path(templates_cfg[module_type]))
        return cached_templates[module_type]

    modules_out: list[etree._Element] = []
    for m in modules_cfg:
        module_type = m["type"]
        module_template = get_template_for_type(module_type)

        parent_name = m["ParentModule"]
        parent_mod_port_raw = m.get("ParentModPortId", None)
        if parent_mod_port_raw is not None and str(parent_mod_port_raw).strip() != "":
            parent_mod_port = int(parent_mod_port_raw)
        else:
            # Missing/blank ParentModPortId means keep the template's default value.
            parent_mod_port = None

        modules_out.extend(
            make_module(
                module_template,
                name=m["name"],
                parent_module=parent_name,
                parent_mod_port=parent_mod_port,
                address=m["Address"],
            )
        )

    combined = build_module_export(target_root, modules_out, controller_name, target_name)

    output_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().isoformat(timespec="seconds").replace(":", "-")
    output_file = Path(output_name)
    out_name = f"{output_file.stem}_{timestamp}{output_file.suffix}"
    out_path = output_dir / out_name
    etree.ElementTree(combined).write(
        str(out_path),
        xml_declaration=True,
        encoding="UTF-8",
        standalone=True,
        pretty_print=True,
    )

    print(f"Wrote {len(modules_out)} modules -> {out_path}\\n")
    for m in modules_cfg:
        print(f"  {m['name']}  {m.get('Address', '')}  ({m['type']})")


if __name__ == "__main__":
    args = _parse_args()
    main(args.toml)
