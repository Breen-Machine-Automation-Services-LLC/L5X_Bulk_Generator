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
from __future__ import annotations

import re  # noqa: F401  (kept for ad-hoc inspection)
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# --- paths -----------------------------------------------------------------
TEMPLATE_DIR = Path(
    r"G:\Shared drives\Customers\SubZero - Cove - Wolf\Wolf"
    r"\1394 Wall Oven Expansion\Programs\1394 Standard Programs"
)
OUTPUT_DIR = Path(
    r"G:\Shared drives\Customers\SubZero - Cove - Wolf\Wolf"
    r"\1394 Wall Oven Expansion\Programs\GeneratedRoutines- Hybrid Main Line"
)

TEMPLATES = {
    "Workstation": "Lift_Standard_Code_Program.L5X",
    "Queue":       "Staight_Track_Standard_Code_Program.L5X",  # sic: typo in source
    "Transfer":    "Chain_Transfer_Standard_Code_Program.L5X",
    # Test stations are treated like lift-less workstations: same straight-track
    # template, just labeled "TestStation" in the filename and prefixed "TS"
    # in neighbor refs (per user direction, 2026-04-28).
    "TestStation": "Staight_Track_Standard_Code_Program.L5X",
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
    conv_rev:  Optional[int] = None  # -> STYYYY
    conv_fwd:  Optional[int] = None  # -> STZZZZ
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
    "Queue":       "ST",
    "Transfer":    "CT",
    "Filler":      "FI",   # not generated, but referenced as neighbor
    "Gravity":     "GR",   # gravity kickout, not generated
    # TestStation uses the straight-track template, which hardcodes "STXXXX"
    # for its self-tag. Neighbor refs MUST use "ST" too or they won't resolve
    # at runtime. The "TestStation" label survives only in the filename and
    # generated Program name.
    "TestStation": "ST",
}

# Type hints for neighbor stations that are NOT in the generation set.
# Lets neighbor-tag lookup use the right prefix without producing a file.
EXTERNAL_TYPE_HINTS: dict[int, str] = {
    # Fillers (skipped per user direction; reference only)
    4282: "Filler",
    4302: "Filler",
    4322: "Filler",
    # Gravity kickouts (skipped; reference only)
    4561: "Gravity",
    4581: "Gravity",
    4601: "Gravity",
    # Outside this PLC / chunk
    4300: "Transfer",   # in-set now, but harmless
    4700: "Transfer",   # different PLC, do not generate
    # 4690's conveyor forward is forced to literal "ST5520" per user direction
    # (5520 lives on a different PLC, treat as queue prefix).
    5520: "Queue",
    # --- 7000-series neighbors (Zone 10) ---
    7155: "Filler",     # FI between 7154 and 7156 (skipped)
    7181: "Filler",     # FI between 7180 and 7156 conv-rev side (skipped)
    7200: "Transfer",   # upstream feeder into ST7150 (different chunk/loop)
    7164: "Transfer",   # CT downstream of ST7163 (next chunk, not yet generated)
    7180: "Transfer",   # CT parent of TS7308 (not yet generated)
    7186: "Filler",     # FI between 7180 and 7187 (skipped)
    7189: "Gravity",    # gravity kickout off 7187 conv_fwd (skipped)
    7190: "Gravity",    # gravity kickout off 7188 conv_fwd (skipped)
    7205: "Filler",     # FI between CT7210 and CT? (skipped)
    7220: "Transfer",   # downstream of CT7210 (different chunk/loop)
    # Vertical-column fillers (between transfer pairs in the TS column)
    7165: "Filler", 7167: "Filler", 7169: "Filler", 7171: "Filler",
    7173: "Filler", 7175: "Filler", 7177: "Filler", 7179: "Filler",
    # Test stations not yet generated; provide type hint so neighbor refs
    # use TS#### prefix.
    7301: "TestStation", 7302: "TestStation", 7303: "TestStation",
    7304: "TestStation", 7305: "TestStation", 7306: "TestStation",
    7307: "TestStation", 7308: "TestStation",
}


def _neighbor_tag(neighbor_num: Optional[int],
                  station_lookup: dict[int, str]) -> Optional[str]:
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


def build_substitutions(s: Station,
                        station_lookup: dict[int, str]) -> dict[str, str]:
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
            ("STYYYY", _neighbor_tag(s.conv_rev,  station_lookup)),
            ("STZZZZ", _neighbor_tag(s.conv_fwd,  station_lookup)),
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


def generate(station: Station,
             station_lookup: dict[int, str],
             dry_run: bool = False) -> Path:
    """Render a single station's L5X. Returns the output path."""
    template_name = TEMPLATES[station.effective_template_type]
    template_path = TEMPLATE_DIR / template_name
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


# --- station list for stations 4000-4090 -----------------------------------
# 4030 transfer mapping is PENDING user confirmation.
STATIONS_FIRST_TEN: list[Station] = [
    # 4000: head of line, no previous.
    # Workstation without a lift -> use straight-track template, but keep
    # "Workstation" in the filename for human clarity.
    Station(number=4000, type="Workstation", template_type="Queue",
            prev=None,  next=4010),
    Station(number=4010, type="Queue",       prev=4000,  next=4020),
    # 4020 transfer (per user 2026-04-24):
    #   chain fwd  = placeholder, chain rev = 4010
    #   conv  fwd  = 4030,         conv rev  = placeholder
    Station(number=4020, type="Transfer",
            conv_rev=None, conv_fwd=4030, chain_fwd=None, chain_rev=4010),
    # 4030 transfer (per user 2026-04-24):
    #   conv  fwd = placeholder, conv rev  = 4020
    #   chain fwd = 4040,         chain rev = placeholder
    Station(number=4030, type="Transfer",
            conv_rev=4020, conv_fwd=None, chain_fwd=4040, chain_rev=None),
    Station(number=4040, type="Queue", prev=4030, next=4050),
    Station(number=4050, type="Queue", prev=4040, next=4060),
    Station(number=4060, type="Queue", prev=4050, next=4070),
    Station(number=4070, type="Queue", prev=4060, next=4080),
    Station(number=4080, type="Queue", prev=4070, next=4090),
    Station(number=4090, type="Queue", prev=4080, next=4100),
    # --- chunk 2: 4100-4290 (added 2026-04-24) ---
    Station(number=4100, type="Queue",       prev=4090, next=4110),
    Station(number=4110, type="Queue",       prev=4100, next=4120),
    Station(number=4120, type="Workstation", prev=4110, next=4130),
    Station(number=4130, type="Queue",       prev=4120, next=4140),
    Station(number=4140, type="Queue",       prev=4130, next=4150),
    Station(number=4150, type="Workstation", prev=4140, next=4160),
    Station(number=4160, type="Queue",       prev=4150, next=4170),
    Station(number=4170, type="Workstation", prev=4160, next=4180),
    Station(number=4180, type="Queue",       prev=4170, next=4190),
    Station(number=4190, type="Workstation", prev=4180, next=4200),
    Station(number=4200, type="Queue",       prev=4190, next=4210),
    Station(number=4210, type="Queue",       prev=4200, next=4220),
    Station(number=4220, type="Workstation", prev=4210, next=4230),
    Station(number=4230, type="Queue",       prev=4220, next=4240),
    Station(number=4240, type="Queue",       prev=4230, next=4250),
    Station(number=4250, type="Queue",       prev=4240, next=4260),
    # 4260 transfer: conv_fwd=4270, conv_rev=None, chain_fwd=None, chain_rev=4250
    Station(number=4260, type="Transfer",
            conv_rev=None, conv_fwd=4270, chain_fwd=None, chain_rev=4250),
    Station(number=4270, type="Queue",       prev=4260, next=4280),
    # 4280 transfer: conv_fwd=4290, conv_rev=4270, chain_fwd=4281, chain_rev=4282
    Station(number=4280, type="Transfer",
            conv_rev=4270, conv_fwd=4290, chain_fwd=4281, chain_rev=4282),
    # 4281 sub-station workstation (no lift): straight-track template.
    Station(number=4281, type="Workstation", template_type="Queue",
            prev=4280, next=4280),
    # 4282 filler -> SKIPPED per user direction
    Station(number=4290, type="Queue",       prev=4280, next=4300),
    # --- chunk 3: 4300-4690 (added 2026-04-24) ---
    # 4300 transfer (same pattern as 4280)
    Station(number=4300, type="Transfer",
            conv_rev=4290, conv_fwd=4310, chain_fwd=4301, chain_rev=4302),
    # 4301 lift-less workstation
    Station(number=4301, type="Workstation", template_type="Queue",
            prev=4300, next=4300),
    # 4302 filler -> SKIPPED
    Station(number=4310, type="Queue",       prev=4300, next=4320),
    # 4320 transfer (same pattern as 4280)
    Station(number=4320, type="Transfer",
            conv_rev=4310, conv_fwd=4330, chain_fwd=4321, chain_rev=4322),
    # 4321 lift-less workstation
    Station(number=4321, type="Workstation", template_type="Queue",
            prev=4320, next=4320),
    # 4322 filler -> SKIPPED
    Station(number=4330, type="Queue",       prev=4320, next=4340),
    Station(number=4340, type="Queue",       prev=4330, next=4350),
    Station(number=4350, type="Workstation", prev=4340, next=4360),
    Station(number=4360, type="Queue",       prev=4350, next=4370),
    Station(number=4370, type="Queue",       prev=4360, next=4380),
    Station(number=4380, type="Workstation", prev=4370, next=4390),
    Station(number=4390, type="Queue",       prev=4380, next=4400),
    Station(number=4400, type="Queue",       prev=4390, next=4410),
    Station(number=4410, type="Workstation", prev=4400, next=4420),
    # 4420 transfer: bottom of down-line, conv in from 4410, chain east to 4440
    Station(number=4420, type="Transfer",
            conv_rev=4410, conv_fwd=None, chain_fwd=4440, chain_rev=None),
    # 4430 does NOT exist (per user, line jumps from 4420 to 4440)
    Station(number=4440, type="Queue",       prev=4420, next=4450),
    # 4450 transfer: bottom of up-line, chain in from west (4440), conv north to 4460
    Station(number=4450, type="Transfer",
            conv_rev=None, conv_fwd=4460, chain_fwd=None, chain_rev=4440),
    Station(number=4460, type="Workstation", prev=4450, next=4470),
    Station(number=4470, type="Queue",       prev=4460, next=4480),
    Station(number=4480, type="Queue",       prev=4470, next=4490),
    Station(number=4490, type="Queue",       prev=4480, next=4500),
    Station(number=4500, type="Queue",       prev=4490, next=4510),
    Station(number=4510, type="Queue",       prev=4500, next=4520),
    # 4520 transfer: top of up-line, conv in from below (4510), chain east to 4530
    Station(number=4520, type="Transfer",
            conv_rev=4510, conv_fwd=None, chain_fwd=4530, chain_rev=None),
    # 4530 transfer: top, chain in from west (4520), conv south to 4540
    Station(number=4530, type="Transfer",
            conv_rev=None, conv_fwd=4540, chain_fwd=None, chain_rev=4520),
    Station(number=4540, type="Queue",       prev=4530, next=4550),
    Station(number=4550, type="Queue",       prev=4540, next=4560),
    # 4560 transfer (per user): chain_fwd=4322 (filler upstream), chain_rev=4561 (gravity)
    Station(number=4560, type="Transfer",
            conv_rev=4550, conv_fwd=4570, chain_fwd=4322, chain_rev=4561),
    # 4561 gravity -> SKIPPED
    Station(number=4570, type="Queue",       prev=4560, next=4580),
    # 4580 transfer (per user): chain_fwd=4302, chain_rev=4581
    Station(number=4580, type="Transfer",
            conv_rev=4570, conv_fwd=4590, chain_fwd=4302, chain_rev=4581),
    # 4581 gravity -> SKIPPED
    Station(number=4590, type="Queue",       prev=4580, next=4600),
    # 4600 transfer (per user): chain_fwd=4282, chain_rev=4601
    Station(number=4600, type="Transfer",
            conv_rev=4590, conv_fwd=4610, chain_fwd=4282, chain_rev=4601),
    # 4601 gravity -> SKIPPED
    Station(number=4610, type="Queue",       prev=4600, next=4620),
    # 4620 transfer (per user)
    Station(number=4620, type="Transfer",
            conv_rev=4610, conv_fwd=None, chain_fwd=4630, chain_rev=None),
    Station(number=4630, type="Queue",       prev=4620, next=4640),
    Station(number=4640, type="Queue",       prev=4630, next=4650),
    Station(number=4650, type="Queue",       prev=4640, next=4660),
    Station(number=4660, type="Queue",       prev=4650, next=4670),
    Station(number=4670, type="Queue",       prev=4660, next=4680),
    # 4680 transfer (per user)
    Station(number=4680, type="Transfer",
            conv_rev=None, conv_fwd=4690, chain_fwd=None, chain_rev=4670),
    # 4690's conveyor forward is forced to ST5520 (different-PLC neighbor)
    Station(number=4690, type="Queue",       prev=4680, next=5520),
]


# --- 7000-series stations (Zone 10, same PLC as 4000-series) ---------------
# Chunk 1: 7150-7154. Flow per user 2026-04-28:
#   CT7200(ext) -> ST7150 -> ST7151 -> ST7152 -> ST7153 -> CT7154 -> south
#   to FI7155(skip) -> CT7156 (next chunk).
STATIONS_7000: list[Station] = [
    Station(number=7150, type="Queue", prev=7200, next=7151, safety_zone=10),
    Station(number=7151, type="Queue", prev=7150, next=7152, safety_zone=10),
    Station(number=7152, type="Queue", prev=7151, next=7153, safety_zone=10),
    Station(number=7153, type="Queue", prev=7152, next=7154, safety_zone=10),
    # 7154 corner transfer: queue line in from east (7153), branch south
    # through filler 7155. Conv axis = main line (east-west); chain axis =
    # perpendicular branch (south).
    Station(number=7154, type="Transfer", safety_zone=10,
            conv_rev=7153, conv_fwd=None,
            chain_fwd=7155, chain_rev=None),
    # Chunk 2: 7156 + 7157-7163 (added 2026-04-28).
    # 7156 transfer per user 2026-04-28:
    #   chain_fwd = ST7157 (east into bottom queue line)
    #   chain_rev = FI7155 (north back to 7154 via filler)
    #   conv_fwd  = 7182   (south into bottom queue line, opposite direction)
    #   conv_rev  = FI7181 (west toward 7180 via filler)
    Station(number=7156, type="Transfer", safety_zone=10,
            conv_rev=7181, conv_fwd=7182,
            chain_fwd=7157, chain_rev=7155),
    # 7157-7163: bottom queue line, west-to-east, fed by 7156, exits to 7164.
    Station(number=7157, type="Queue", prev=7156, next=7158, safety_zone=10),
    Station(number=7158, type="Queue", prev=7157, next=7159, safety_zone=10),
    Station(number=7159, type="Queue", prev=7158, next=7160, safety_zone=10),
    Station(number=7160, type="Queue", prev=7159, next=7161, safety_zone=10),
    Station(number=7161, type="Queue", prev=7160, next=7162, safety_zone=10),
    Station(number=7162, type="Queue", prev=7161, next=7163, safety_zone=10),
    Station(number=7163, type="Queue", prev=7162, next=7164, safety_zone=10),
    # Chunk 3: vertical column transfers 7164, 7166, 7168, 7170, 7172, 7174,
    # 7176, 7178 (added 2026-04-28). The TS column hangs off the conv_fwd
    # side of each even transfer (7301..7307); fillers (7165, 7167, ..., 7179)
    # bridge each pair on chain or conv axis.
    #
    # 7164 corner: turns east-west chain (from 7163 queue) into north-south
    # conv axis (toward 7165 filler).
    Station(number=7164, type="Transfer", safety_zone=10,
            conv_rev=None, conv_fwd=7165,
            chain_fwd=None, chain_rev=7163),
    # 7166: bottom of TS column, conv axis east to TS7301 / west via FI7165,
    # chain axis north via FI7167.
    Station(number=7166, type="Transfer", safety_zone=10,
            conv_rev=7165, conv_fwd=7301,
            chain_fwd=7167, chain_rev=None),
    # 7168, 7170, 7172, 7174, 7176, 7178 follow the same pattern as 7168:
    #   conv_fwd  = TS7###  (test station to the east)
    #   chain_fwd = FI7### (filler to the north)
    #   chain_rev = FI7### (filler to the south, between this and prior pair)
    Station(number=7168, type="Transfer", safety_zone=10,
            conv_rev=None, conv_fwd=7302,
            chain_fwd=7169, chain_rev=7167),
    Station(number=7170, type="Transfer", safety_zone=10,
            conv_rev=None, conv_fwd=7303,
            chain_fwd=7171, chain_rev=7169),
    Station(number=7172, type="Transfer", safety_zone=10,
            conv_rev=None, conv_fwd=7304,
            chain_fwd=7173, chain_rev=7171),
    Station(number=7174, type="Transfer", safety_zone=10,
            conv_rev=None, conv_fwd=7305,
            chain_fwd=7175, chain_rev=7173),
    Station(number=7176, type="Transfer", safety_zone=10,
            conv_rev=None, conv_fwd=7306,
            chain_fwd=7177, chain_rev=7175),
    Station(number=7178, type="Transfer", safety_zone=10,
            conv_rev=None, conv_fwd=7307,
            chain_fwd=7179, chain_rev=7177),
    # Chunk 4: test stations 7301-7308 (added 2026-04-28).
    # Pattern matches lift-less workstations 4281/4301/4321: file labeled
    # "TestStation" but rendered from the straight-track template (via
    # template_type implicit in TEMPLATES["TestStation"]). Each TS sits on
    # the conv_fwd side of its parent transfer, with prev=next=parent so
    # the routine references the parent CT on both directions.
    Station(number=7301, type="TestStation", prev=7166, next=7166, safety_zone=10),
    Station(number=7302, type="TestStation", prev=7168, next=7168, safety_zone=10),
    Station(number=7303, type="TestStation", prev=7170, next=7170, safety_zone=10),
    Station(number=7304, type="TestStation", prev=7172, next=7172, safety_zone=10),
    Station(number=7305, type="TestStation", prev=7174, next=7174, safety_zone=10),
    Station(number=7306, type="TestStation", prev=7176, next=7176, safety_zone=10),
    Station(number=7307, type="TestStation", prev=7178, next=7178, safety_zone=10),
    Station(number=7308, type="TestStation", prev=7180, next=7180, safety_zone=10),
    # Chunk 5: 7180, 7187, 7188 transfers (added 2026-04-28).
    # 7180: top of TS column. Conv axis east to TS7308 / west via FI7181;
    # chain axis north to FI7186 (toward 7187) / south from FI7179.
    Station(number=7180, type="Transfer", safety_zone=10,
            conv_rev=7181, conv_fwd=7308,
            chain_fwd=7186, chain_rev=7179),
    # 7187: chain axis north to 7188 / south from FI7186; conv axis north to
    # gravity kickout 7189.
    Station(number=7187, type="Transfer", safety_zone=10,
            conv_rev=None, conv_fwd=7189,
            chain_fwd=7188, chain_rev=7186),
    # 7188: top of column. Chain axis south from 7187; conv axis north to
    # gravity kickout 7190.
    Station(number=7188, type="Transfer", safety_zone=10,
            conv_rev=None, conv_fwd=7190,
            chain_fwd=None, chain_rev=7187),
    # Chunk 6: bottom east-bound queue 7182-7185 + exit transfer 7210
    # (added 2026-04-28). Flow: CT7156 -> ST7182 -> 7183 -> 7184 -> 7185 ->
    # CT7210 -> ST7220.
    Station(number=7182, type="Queue", prev=7156, next=7183, safety_zone=10),
    Station(number=7183, type="Queue", prev=7182, next=7184, safety_zone=10),
    Station(number=7184, type="Queue", prev=7183, next=7185, safety_zone=10),
    Station(number=7185, type="Queue", prev=7184, next=7210, safety_zone=10),
    # 7210 corner transfer: queue line in from west (7185), exits east via
    # filler 7205 to ST7220 (next loop). Conv axis = main line; chain axis
    # carries the queue feed.
    Station(number=7210, type="Transfer", safety_zone=10,
            conv_rev=7205, conv_fwd=7220,
            chain_fwd=None, chain_rev=7185),
]


def main(only: Optional[set[int]] = None) -> None:
    # Build lookup so neighbor tags can be type-prefixed correctly.
    # Use *effective* template type (so a lift-less workstation uses ST prefix).
    all_stations: list[Station] = STATIONS_FIRST_TEN + STATIONS_7000
    lookup: dict[int, str] = dict(EXTERNAL_TYPE_HINTS)
    lookup.update({s.number: s.effective_template_type for s in all_stations})
    written: list[Path] = []
    for s in all_stations:
        if only is not None and s.number not in only:
            continue
        out = generate(s, lookup)
        written.append(out)
        print(f"  wrote {out.name}")
    print(f"\nDone. {len(written)} file(s) written to {OUTPUT_DIR}")


if __name__ == "__main__":
    # Generate only the new chunk this run.
    # Remove the `only=` arg to regenerate everything.
    NEW_THIS_RUN = {7182, 7183, 7184, 7185, 7210}
    main(only=NEW_THIS_RUN)
