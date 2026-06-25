"""Generate per-station L5X files from SubZero/Wolf 1394 standard templates.

Mirrors the find/replace logic of Standard_Code_Maker.xlsm > GenerateRoutines.

Placeholder conventions (decoded from rung logic):
- Queue / Workstation:
    STYYYY = upstream (previous) station tag
    STZZZZ = downstream (next) station tag
- Chain Transfer:
    STYYYY = Conveyor Reverse neighbor
    STZZZZ = Conveyor Forward neighbor
    STVVVV = Chain Forward neighbor
    STWWWW = Chain Reverse neighbor
- All types:
    XXXX                       = current station number (e.g. 4000)
    Safety_PowerOn_Placeholder = safety power-on tag (e.g. Safety_PowerOn_Zone8)
    FFFFF                      = LEFT AS-IS (per user direction, 2026-04-24)

If a neighbor value is None (e.g. 4000 has no previous), the placeholder
is LEFT AS-IS so it can be filled in later in Studio 5000.
"""

# Standard library imports
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

# Third-party imports
import tomllib

# Local application imports

# Constants
PROJECT_ROOT = Path(__file__).resolve().parent
STATIONS_TOML = PROJECT_ROOT / "input" / "stations.toml"
OUTPUT_DIR = PROJECT_ROOT / "output" / "stationPrograms"

TEMPLATES = {
    "Workstation": PROJECT_ROOT / "input" / "Lift_Standard_Code_Program.L5X",
    "Queue": PROJECT_ROOT / "input" / "Staight_Track_Standard_Code_Program.L5X",  # sic: typo in source
    "Transfer": PROJECT_ROOT / "input" / "Chain_Transfer_Standard_Code_Program.L5X",
    "TestStation": PROJECT_ROOT / "input" / "Bi_Directional_Track_Standard_Code_Program.L5X",
}

SAFETY_TAG = "Safety_PowerOn_Zone8"  # 4000-series default; overridden per-station via Station.safety_zone


# --- station spec ----------------------------------------------------------
@dataclass
class Station:
    number: int
    type: str  # "Workstation" | "Queue" | "Transfer" -- used for filename
    # Queue/Workstation
    prev: Optional[int] = None  # -> STYYYY
    next: Optional[int] = None  # -> STZZZZ
    # Transfer extras
    conv_rev: Optional[int] = None  # -> STYYYY
    conv_fwd: Optional[int] = None  # -> STZZZZ
    chain_fwd: Optional[int] = None  # -> STVVVV
    chain_rev: Optional[int] = None  # -> STWWWW
    # Override which template (and therefore self-tag prefix) is used.
    # Example: a workstation that has no lift uses the Queue (straight track)
    # template. Filename still says "Workstation" but tags become ST####.
    template_type: Optional[str] = None  # "Workstation" | "Queue" | "Transfer" | "TestStation"
    # Per-station safety zone override. None -> use module-level SAFETY_TAG.
    # Pass an int (e.g. 10) to use Safety_PowerOn_Zone10.
    safety_zone: Optional[int] = None

    @property
    def effective_template_type(self) -> str:
        return self.template_type or self.type


def _st(n: Optional[int]) -> Optional[str]:
    """Legacy helper, retained but no longer used. See _neighbor_tag()."""
    return f"ST{n}" if n is not None else None


# Neighbor-tag prefix depends on the *neighbor's* station type, because each
# template names its own SZG_Station tag with a type-specific prefix:
#   Workstation -> Li####   (Lift)
#   Queue       -> ST####   (Straight track)
#   Transfer    -> CT####   (Chain transfer)
TYPE_PREFIX = {
    "Workstation": "Li",
    "Queue": "ST",
    "Transfer": "CT",
    "Filler": "FI",  # not generated, but referenced as neighbor
    "Gravity": "GR",  # gravity kickout, not generated
    # TestStation uses the straight-track template, which hardcodes "STXXXX"
    # for its self-tag. Neighbor refs MUST use "ST" too or they won't resolve
    # at runtime. The "TestStation" label survives only in the filename and
    # generated Program name.
    "TestStation": "ST",
}

EXTERNAL_TYPE_HINTS: dict[int, str] = {}


def _as_optional_int(value: Any) -> Optional[int]:
    if value in (None, 0, "0"):
        return None
    return int(value)


def _resolve_template_path(path_value: str) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else (PROJECT_ROOT / path)


def _load_templates_from_toml(toml_data: dict[str, Any]) -> dict[str, Path]:
    templates = toml_data.get("templates")
    if not isinstance(templates, dict):
        return dict(TEMPLATES)

    required = {"workstation", "queue", "transfer", "teststation"}
    missing = [key for key in required if key not in templates]
    if missing:
        missing_csv = ", ".join(sorted(missing))
        raise KeyError(f"Missing [templates] keys in {STATIONS_TOML}: {missing_csv}")

    return {
        "Workstation": _resolve_template_path(str(templates["workstation"])),
        "Queue": _resolve_template_path(str(templates["queue"])),
        "Transfer": _resolve_template_path(str(templates["transfer"])),
        "TestStation": _resolve_template_path(str(templates["teststation"])),
    }


def _load_external_type_hints(toml_data: dict[str, Any]) -> dict[int, str]:
    hints_section = toml_data.get("external_type_hints", {})
    if not isinstance(hints_section, dict):
        raise TypeError("[external_type_hints] must be a TOML table")

    hints: dict[int, str] = {}
    for key, value in hints_section.items():
        hints[int(key)] = str(value)
    return hints


def _station_type_for_row(row: dict[str, Any]) -> str:
    if bool(row.get("isTestStation", False)):
        return "TestStation"
    if bool(row.get("isWorkstation", False)):
        return "Workstation"
    raw_type = str(row["type"])
    if raw_type == "Transfer":
        return "Transfer"
    return "Queue"


def _station_template_type_for_row(row: dict[str, Any], station_type: str) -> Optional[str]:
    raw_type = str(row["type"])
    if station_type == "TestStation":
        return "TestStation"
    if station_type == "Workstation" and raw_type == "Queue":
        # Queue mechanics labeled as workstation should keep ST self-tag behavior.
        return "Queue"
    return None


def _load_stations_from_toml(toml_data: dict[str, Any]) -> list[Station]:
    stations_section = toml_data.get("stations", {})
    if not isinstance(stations_section, dict):
        raise TypeError("[stations] must be a TOML table")
    station_rows = stations_section.get("data", [])
    if not isinstance(station_rows, list):
        raise TypeError("[stations].data must be a TOML array")

    default_safety_zone = int(toml_data.get("config", {}).get("default_safety_zone", 8))
    stations: list[Station] = []

    for row in station_rows:
        if not isinstance(row, dict):
            raise TypeError("Each entry in [stations].data must be a TOML inline table")

        station_type = _station_type_for_row(row)
        template_type = _station_template_type_for_row(row, station_type)

        base_kwargs: dict[str, Any] = {
            "number": int(row["number"]),
            "type": station_type,
            "template_type": template_type,
            "safety_zone": int(row.get("safety_zone", default_safety_zone)),
        }

        if station_type == "Transfer":
            stations.append(
                Station(
                    conv_rev=_as_optional_int(row.get("conv_upstream")),
                    conv_fwd=_as_optional_int(row.get("conv_downstream")),
                    chain_fwd=_as_optional_int(row.get("chain_upstream")),
                    chain_rev=_as_optional_int(row.get("chain_downstream")),
                    **base_kwargs,
                )
            )
        else:
            stations.append(
                Station(
                    prev=_as_optional_int(row.get("prev")),
                    next=_as_optional_int(row.get("next")),
                    **base_kwargs,
                )
            )

    return stations


def load_config(
    stations_toml: Path,
) -> tuple[dict[str, Path], dict[int, str], list[Station]]:
    with stations_toml.open("rb") as file_obj:
        toml_data = tomllib.load(file_obj)

    templates = _load_templates_from_toml(toml_data)
    hints = _load_external_type_hints(toml_data)
    stations = _load_stations_from_toml(toml_data)
    return templates, hints, stations


def _neighbor_tag(neighbor_num: Optional[int], station_lookup: dict[int, str]) -> Optional[str]:
    """Build the correctly-prefixed neighbor tag (e.g. 'CT4020' or 'ST4010').

    Returns None if neighbor_num is None or unknown — caller leaves the
    placeholder alone in that case.
    """
    if neighbor_num is None:
        return None
    nbr_type = station_lookup.get(neighbor_num)
    if nbr_type is None:
        # Unknown neighbor (outside the generated set). Default to ST as a
        # visible placeholder; user can find/replace later if needed.
        return f"ST{neighbor_num}"
    return f"{TYPE_PREFIX[nbr_type]}{neighbor_num}"


def build_substitutions(s: Station, station_lookup: dict[int, str]) -> dict[str, str]:
    """Return {placeholder: replacement} for non-None values only.

    Placeholders left out of the dict are NOT touched in the template.
    """
    subs: dict[str, str] = {
        "XXXX": str(s.number),
        "Safety_PowerOn_Placeholder": (
            f"Safety_PowerOn_Zone{s.safety_zone}" if s.safety_zone is not None else SAFETY_TAG
        ),
    }

    if s.type == "Transfer":
        for ph, val in [
            ("STYYYY", _neighbor_tag(s.conv_rev, station_lookup)),
            ("STZZZZ", _neighbor_tag(s.conv_fwd, station_lookup)),
            ("STVVVV", _neighbor_tag(s.chain_fwd, station_lookup)),
            ("STWWWW", _neighbor_tag(s.chain_rev, station_lookup)),
        ]:
            if val is not None:
                subs[ph] = val
    else:
        for ph, val in [
            ("STYYYY", _neighbor_tag(s.prev, station_lookup)),
            ("STZZZZ", _neighbor_tag(s.next, station_lookup)),
        ]:
            if val is not None:
                subs[ph] = val
    return subs


def generate(
    station: Station,
    station_lookup: dict[int, str],
    templates: dict[str, Path],
    dry_run: bool = False,
) -> Path:
    """Render a single station's L5X. Returns the output path."""
    template_path = templates[station.effective_template_type]
    if not template_path.is_file():
        raise FileNotFoundError(template_path)

    text = template_path.read_text(encoding="utf-8", errors="strict")
    subs = build_substitutions(station, station_lookup)

    # Apply replacements. Order by length desc as a habit (no overlaps today).
    for ph in sorted(subs, key=len, reverse=True):
        text = text.replace(ph, subs[ph])

    out_path = OUTPUT_DIR / f"Sta{station.number}_{station.type}.L5X"
    if not dry_run:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
    return out_path


def main(only: Optional[set[int]] = None) -> None:
    templates, hints, all_stations = load_config(STATIONS_TOML)

    # Build lookup so neighbor tags can be type-prefixed correctly.
    # Use *effective* template type (so a lift-less workstation uses ST prefix).
    lookup: dict[int, str] = dict(hints)
    lookup.update({s.number: s.effective_template_type for s in all_stations})
    written: list[Path] = []
    for s in all_stations:
        if only is not None and s.number not in only:
            continue
        out = generate(s, lookup, templates)
        written.append(out)
        print(f"  wrote {out.name}")
    print(f"\nDone. {len(written)} file(s) written to {OUTPUT_DIR}")


if __name__ == "__main__":
    # Generate only the new chunk this run.
    # Remove the `only=` arg to regenerate everything.
    # NEW_THIS_RUN = {7182, 7183, 7184, 7185, 7210}
    main()  # only=NEW_THIS_RUN)
