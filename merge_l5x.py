"""Merge per-station L5X files into a single combined L5X for one-shot import.

Re-implementation of Standard_Code_Maker.xlsm > AppendXMLFiles macro,
using lxml. Faster, safer (auto-creates missing containers), and
runs without Excel.

Containers merged (deduplicated by Name attribute):
  - Routines      (under .../Programs/Program/Routines)
  - Tags          (under .../Controller/Tags)
  - DataTypes     (under .../Controller/DataTypes)
  - Modules       (under .../Controller/Modules)
  - AOI defs      (under .../Controller/AddOnInstructionDefinitions)

Usage:
  Edit the constants below, then run:
    python merge_l5x.py
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

from lxml import etree

# --- configuration ---------------------------------------------------------

# Folder containing the per-station Sta####_*.L5X files
INPUT_DIR = Path(
    r"G:\Shared drives\Customers\SubZero - Cove - Wolf\Wolf"
    r"\1394 Wall Oven Expansion\Programs\GeneratedRoutines- Hybrid Main Line"
)

# Where to write the combined Program_<timestamp>.L5X
OUTPUT_DIR = INPUT_DIR  # same folder; change if you want it elsewhere

# Optional: explicit base file. If None, uses the alphabetically-first L5X
# in INPUT_DIR as the base. The base provides the Controller header
# (revision, processor, etc.). Any file generated from your templates works.
BASE_FILE: Path | None = None  # e.g. Path(r"G:\...\Sta4150_Workstation.L5X")

# Glob pattern for files to merge (the base file is excluded automatically)
INPUT_GLOB = "Sta*.L5X"

# Optional station-number filter. If set, only files whose station number
# falls in [STATION_MIN, STATION_MAX] (inclusive) are merged. Use this to
# import a single section into a controller that already has the others.
# Set both to None to disable filtering.
STATION_MIN: int | None = 7000
STATION_MAX: int | None = 7999

# Tag for output filename when filter is active (purely cosmetic)
OUTPUT_TAG: str = "7000s"  # set to "" to omit


def _ensure_parent(base_root: etree._Element, parent_xpath: str) -> etree._Element:
    """Find or create the parent container at parent_xpath. Returns the element."""
    found = base_root.xpath(parent_xpath)
    if found:
        return found[0]
    # walk the path, creating any missing ancestors
    parts = [p for p in parent_xpath.split("/") if p]
    if base_root.tag != parts[0]:
        raise ValueError(
            f"Base root <{base_root.tag}> does not match expected <{parts[0]}>."
        )
    cur = base_root
    for tag in parts[1:]:
        nxt = cur.find(tag)
        if nxt is None:
            nxt = etree.SubElement(cur, tag)
        cur = nxt
    return cur


# (parent_xpath, child_tag) pairs we merge with name-dedupe at the controller
# level. Programs are handled separately (see _merge_programs) because we want
# each station as its own sibling Program, not flattened into one.
CONTAINERS: list[tuple[str, str]] = [
    ("/RSLogix5000Content/Controller/DataTypes",                  "DataType"),
    ("/RSLogix5000Content/Controller/Modules",                    "Module"),
    ("/RSLogix5000Content/Controller/AddOnInstructionDefinitions","AddOnInstructionDefinition"),
    ("/RSLogix5000Content/Controller/Tags",                       "Tag"),
]


# Canonical order of <Controller>'s direct children expected by Studio 5000.
# Anything not in this list is appended at the end in original order.
# Source: schema observed in vendor-exported L5X files.
CONTROLLER_CHILD_ORDER: list[str] = [
    "Description",
    "RedundancyInfo",
    "Security",
    "SafetyInfo",
    "DataTypes",
    "Modules",
    "AddOnInstructionDefinitions",
    "Tags",
    "Programs",
    "Tasks",
    "ParameterConnections",
    "CommPorts",
    "AlarmManager",
    "TimeSynchronize",
    "EthernetPorts",
    "EthernetNetwork",
    "InternetProtocol",
    "PortConfigurations",
    "QuickWatchLists",
]


def _reorder_controller_children(base_root: etree._Element) -> None:
    """Re-sort direct children of <Controller> into canonical L5X order.

    Studio 5000's importer enforces a fixed element order under <Controller>
    and rejects the file with "Element <X> is in the wrong order." otherwise.
    This happens after merge because _ensure_parent() appends newly-created
    containers (e.g. <Modules>) to the end of <Controller>.
    """
    controllers = base_root.xpath("/RSLogix5000Content/Controller")
    if not controllers:
        return
    ctrl = controllers[0]
    rank = {name: i for i, name in enumerate(CONTROLLER_CHILD_ORDER)}
    fallback = len(CONTROLLER_CHILD_ORDER)
    # Stable sort preserves original order for unknown tags.
    children = list(ctrl)
    children.sort(key=lambda el: rank.get(el.tag, fallback))
    # Detach and re-append in new order.
    for child in children:
        ctrl.remove(child)
    for child in children:
        ctrl.append(child)


def merge(base_path: Path, sources: list[Path], output_path: Path) -> dict:
    """Merge `sources` into a copy of `base_path`, write to `output_path`.

    Returns a stats dict for reporting.
    """
    # strip_cdata=False is REQUIRED. L5X wraps STRING DATA members and
    # Description text in <![CDATA[...]]>; Studio 5000's importer rejects
    # those nodes if the CDATA wrapper is missing on import. lxml's default
    # parser silently strips CDATA markers (keeps text) -> import fails.
    parser = etree.XMLParser(
        remove_blank_text=False, huge_tree=True, strip_cdata=False
    )
    base_tree = etree.parse(str(base_path), parser)
    base_root = base_tree.getroot()

    # Index existing names per controller-level container so we don't
    # double-add what's already in the base file.
    existing: dict[str, set[str]] = {}
    for parent_xp, child_tag in CONTAINERS:
        parent_el = _ensure_parent(base_root, parent_xp)
        existing[child_tag] = {
            c.get("Name") for c in parent_el.findall(child_tag) if c.get("Name")
        }

    # Programs are merged as siblings (one per source file), not flattened.
    # Pre-load existing Program names from the base for dedupe.
    programs_parent = _ensure_parent(base_root, "/RSLogix5000Content/Controller/Programs")
    existing_programs = {
        p.get("Name") for p in programs_parent.findall("Program") if p.get("Name")
    }

    stats: dict[str, dict[str, int]] = {ct: {"added": 0, "skipped": 0} for _, ct in CONTAINERS}
    stats["Program"] = {"added": 0, "skipped": 0, "renamed": 0}

    # Rename the base file's own Program(s) to match the base file's stem so
    # all programs follow the per-station naming convention. Without this the
    # base contributes one oddly-named program (e.g. "Staight_Track_Standard_Code").
    base_program_name = base_path.stem  # e.g. "Sta4000_Workstation"
    base_progs = list(programs_parent.findall("Program"))
    if len(base_progs) == 1:
        old = base_progs[0].get("Name")
        if old != base_program_name:
            base_progs[0].set("Name", base_program_name)
            existing_programs.discard(old)
            existing_programs.add(base_program_name)
            stats["Program"]["renamed"] += 1

    # Pair each source file with the desired Program name (file stem).
    all_inputs: list[tuple[Path, str]] = [(s, s.stem) for s in sources]

    for src, prog_name in all_inputs:
        try:
            src_tree = etree.parse(str(src), parser)
        except etree.XMLSyntaxError as exc:
            print(f"  [SKIP] {src.name}: invalid XML ({exc})")
            continue
        src_root = src_tree.getroot()

        # 1) Controller-level containers (DataTypes, Modules, AOI defs, Tags) -
        #    dedupe by Name attribute.
        for parent_xp, child_tag in CONTAINERS:
            parent_el = _ensure_parent(base_root, parent_xp)
            for child in src_root.xpath(f"{parent_xp}/{child_tag}"):
                name = child.get("Name")
                if not name:
                    parent_el.append(child)
                    stats[child_tag]["added"] += 1
                    continue
                if name in existing[child_tag]:
                    stats[child_tag]["skipped"] += 1
                    continue
                # lxml note: appending a node from another tree moves it.
                parent_el.append(child)
                existing[child_tag].add(name)
                stats[child_tag]["added"] += 1

        # 2) Programs - rename source Program to file-stem and append as
        #    sibling. Each station gets its own Program containing its
        #    MainRoutine + 4 station-specific routines.
        src_programs = src_root.xpath("/RSLogix5000Content/Controller/Programs/Program")
        if not src_programs:
            print(f"  [WARN] {src.name}: no <Program> element found")
            continue
        if len(src_programs) > 1:
            print(f"  [WARN] {src.name}: {len(src_programs)} Programs; "
                  "merging only the first as '{prog_name}'")
        src_prog = src_programs[0]
        if prog_name in existing_programs:
            stats["Program"]["skipped"] += 1
            continue
        src_prog.set("Name", prog_name)
        programs_parent.append(src_prog)
        existing_programs.add(prog_name)
        stats["Program"]["added"] += 1

    # Studio 5000 requires Controller's children in a specific order; if any
    # were appended at the end (because they didn't exist in the base file),
    # the importer rejects the whole file with "Element <X> is in the wrong
    # order." Re-sort Controller children to the canonical sequence below.
    # Unknown elements keep their relative order at the end.
    _reorder_controller_children(base_root)

    # Write output. xml_declaration + encoding match Studio 5000 conventions.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    base_tree.write(
        str(output_path),
        xml_declaration=True,
        encoding="UTF-8",
        standalone=True,
        pretty_print=False,
    )
    return stats


def _station_number(path: Path) -> int | None:
    """Extract the station number from a 'Sta####_Type.L5X' filename.

    Returns None if the name doesn't match the expected pattern.
    """
    stem = path.stem  # e.g. 'Sta7150_Queue'
    if not stem.startswith("Sta"):
        return None
    rest = stem[3:].split("_", 1)[0]
    return int(rest) if rest.isdigit() else None


def _in_filter(path: Path) -> bool:
    n = _station_number(path)
    if n is None:
        return False  # ignore unrecognized files
    if STATION_MIN is not None and n < STATION_MIN:
        return False
    if STATION_MAX is not None and n > STATION_MAX:
        return False
    return True


def main() -> int:
    if not INPUT_DIR.is_dir():
        print(f"ERROR: input dir not found: {INPUT_DIR}", file=sys.stderr)
        return 1

    all_matches = sorted(INPUT_DIR.glob(INPUT_GLOB))
    if not all_matches:
        print(f"ERROR: no files match {INPUT_GLOB} in {INPUT_DIR}", file=sys.stderr)
        return 1

    # Apply optional station-number filter.
    filter_active = STATION_MIN is not None or STATION_MAX is not None
    files = [f for f in all_matches if _in_filter(f)] if filter_active else all_matches
    if not files:
        rng = f"[{STATION_MIN}..{STATION_MAX}]"
        print(f"ERROR: no files in station range {rng}", file=sys.stderr)
        return 1

    base = BASE_FILE if BASE_FILE is not None else files[0]
    if not base.is_file():
        print(f"ERROR: base file not found: {base}", file=sys.stderr)
        return 1

    sources = [f for f in files if f.resolve() != base.resolve()]

    timestamp = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
    suffix = f"_{OUTPUT_TAG}" if (filter_active and OUTPUT_TAG) else ""
    out = OUTPUT_DIR / f"Program{suffix}_{timestamp}.L5X"

    print(f"Base:    {base.name}")
    if filter_active:
        print(f"Filter:  station numbers in [{STATION_MIN}..{STATION_MAX}]  "
              f"({len(files)}/{len(all_matches)} files)")
    print(f"Merging: {len(sources)} files from {INPUT_DIR}")
    print(f"Output:  {out}")
    print()

    t0 = time.perf_counter()
    stats = merge(base, sources, out)
    elapsed = time.perf_counter() - t0

    print("Merge stats (added / skipped-duplicate):")
    for _, ct in CONTAINERS:
        s = stats[ct]
        print(f"  {ct:30s}  added={s['added']:>6}   skipped={s['skipped']:>6}")
    sp = stats["Program"]
    print(f"  {'Program':30s}  added={sp['added']:>6}   skipped={sp['skipped']:>6}"
          f"   (base renamed: {sp['renamed']})")
    print(f"\nDone in {elapsed:.2f}s -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
