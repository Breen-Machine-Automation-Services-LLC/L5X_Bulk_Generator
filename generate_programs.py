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

TODO: Fallback behavior should be driven by template-defined rules rather than
hardcoded generator defaults.
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
ROUTE_CONFIG_BIT = 30
ROUTE_CONFIG_MASK = 1 << ROUTE_CONFIG_BIT


# --- station spec ----------------------------------------------------------
@dataclass
class Station:
    number: int
    type: str  # "Lift" | "Queue" | "Transfer" | "TestStation" | "Gravity"
    # Filename label override; used for output naming only.
    output_type: Optional[str] = None
    # Queue/Workstation
    prev: Optional[int] = None  # -> STYYYY
    next: Optional[int] = None  # -> STZZZZ
    # Transfer extras
    conv_rev: Optional[int] = None  # -> STYYYY
    conv_fwd: Optional[int] = None  # -> STZZZZ
    chain_fwd: Optional[int] = None  # -> STVVVV
    chain_rev: Optional[int] = None  # -> STWWWW
    # Template selection is controlled only by station type.
    template_type: str = "Queue"  # "Lift" | "Queue" | "Transfer" | "TestStation" | "Gravity"
    # Per-station safety signal/tag override, for example Safety_Zone10_OK.DN.
    # None means leave template safety placeholder(s) unchanged.
    safety: Optional[str] = None
    # Optional MES code for station-specific MES tag naming.
    # When None, MES tag and MES-only logic are removed from output.
    mes_code: Optional[str] = None
    # Enable routing HMI behavior by setting Config.30.
    has_route: bool = False
    # Semantic flags derived from TOML row content.
    is_transfer: bool = False
    is_tester: bool = False

    @property
    def effective_template_type(self) -> str:
        return self.template_type


def _st(n: Optional[int]) -> Optional[str]:
    """Legacy helper, retained but no longer used. See _neighbor_tag()."""
    return f"ST{n}" if n is not None else None


def _is_transfer_type(type_name: str) -> bool:
    return "transfer" in type_name.strip().lower()


def _is_tester_type(type_name: str) -> bool:
    return "test" in type_name.strip().lower()


def _is_gravity_type(type_name: str) -> bool:
    return "gravity" in type_name.strip().lower()


def _as_optional_int(value: Any) -> Optional[int]:
    if value in (None, 0, "0"):
        return None
    return int(value)


def _as_optional_mes_code(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, bool):
        # Legacy boolean flags are treated as MES disabled.
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        code = value.strip()
        return code or None
    raise TypeError(f"Unsupported isMES value type: {type(value).__name__}")


def _resolve_template_path(path_value: str) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else (PROJECT_ROOT / path)


def _load_templates_from_toml(toml_data: dict[str, Any]) -> dict[str, Path]:
    templates = toml_data.get("templates")
    if not isinstance(templates, dict):
        raise KeyError(f"Missing [templates] table in {STATIONS_TOML}. Define template paths for each station type.")

    normalized: dict[str, Path] = {}
    for key, value in templates.items():
        type_name = str(key).strip()
        if not type_name:
            raise KeyError(f"[templates] contains an empty type key in {STATIONS_TOML}.")
        if type_name in normalized:
            raise KeyError(f"Duplicate [templates] key in {STATIONS_TOML}: {type_name}. Use each type only once.")
        normalized[type_name] = _resolve_template_path(str(value))

    if not normalized:
        raise KeyError(f"[templates] in {STATIONS_TOML} must define at least one station type.")

    return normalized


def _load_type_prefixes_from_toml(toml_data: dict[str, Any], station_types: set[str]) -> dict[str, str]:
    prefixes = toml_data.get("type_prefix")
    if not isinstance(prefixes, dict):
        raise KeyError(
            f"Missing [type_prefix] table in {STATIONS_TOML}. Define prefixes for each station type and Filler."
        )

    normalized: dict[str, str] = {}
    for key, value in prefixes.items():
        key_text = str(key).strip()
        if key_text in normalized:
            raise KeyError(
                f"Duplicate [type_prefix] key in {STATIONS_TOML}: {key_text}. Use each required key only once."
            )
        normalized[key_text] = str(value).strip()

    required_prefix_types = station_types | {"Filler"}
    missing = [type_name for type_name in sorted(required_prefix_types) if type_name not in normalized]
    if missing:
        missing_csv = ", ".join(missing)
        raise KeyError(f"Missing required [type_prefix] mappings in {STATIONS_TOML}: {missing_csv}.")

    return {type_name: normalized[type_name] for type_name in sorted(required_prefix_types)}


def _load_external_type_hints(toml_data: dict[str, Any], allowed_types: set[str]) -> dict[int, str]:
    hints_section = toml_data.get("external_type_hints", {})
    if not isinstance(hints_section, dict):
        raise TypeError("[external_type_hints] must be a TOML table")

    hints: dict[int, str] = {}
    for key, value in hints_section.items():
        hint_type = str(value).strip()
        if hint_type not in allowed_types:
            raise ValueError(
                f"Unsupported external_type_hints value '{hint_type}' for station {key}. "
                f"Allowed values: {', '.join(sorted(allowed_types))}."
            )
        hints[int(key)] = hint_type
    return hints


def _station_type_for_row(row: dict[str, Any], station_types: set[str]) -> str:
    raw_type = str(row["type"]).strip()
    if raw_type in station_types:
        return raw_type
    raise ValueError(
        f"Unsupported station type '{row['type']}' at station {row['number']}. "
        f"Supported values from [templates]: {', '.join(sorted(station_types))}."
    )


def _load_stations_from_toml(toml_data: dict[str, Any], station_types: set[str]) -> list[Station]:
    stations_section = toml_data.get("stations", {})
    if not isinstance(stations_section, dict):
        raise TypeError("[stations] must be a TOML table")
    station_rows = stations_section.get("data", [])
    if not isinstance(station_rows, list):
        raise TypeError("[stations].data must be a TOML array")

    stations: list[Station] = []

    for row in station_rows:
        if not isinstance(row, dict):
            raise TypeError("Each entry in [stations].data must be a TOML inline table")

        station_type = _station_type_for_row(row, station_types)
        template_type = station_type
        output_type = "Workstation" if bool(row.get("isWorkstation", False)) else station_type

        safety_expr: Optional[str] = None
        if "safety" in row:
            safety_value = row.get("safety")
            safety_expr = str(safety_value).strip()
            if not safety_expr:
                raise ValueError(f"Empty safety value at station {row['number']}")
        elif "safety_zone" in row:
            raise ValueError(
                f"Unsupported safety_zone at station {row['number']}. "
                'Use safety = "<tag>" and omit safety to keep template placeholder.'
            )

        base_kwargs: dict[str, Any] = {
            "number": int(row["number"]),
            "type": station_type,
            "output_type": output_type,
            "template_type": template_type,
            "safety": safety_expr,
            "mes_code": _as_optional_mes_code(row.get("isMES")),
            "has_route": bool(row.get("hasRoute", False)),
            "is_transfer": _is_transfer_type(station_type),
            "is_tester": _is_tester_type(station_type),
        }

        if base_kwargs["is_transfer"]:
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
) -> tuple[dict[str, Path], dict[str, str], dict[int, str], list[Station]]:
    with stations_toml.open("rb") as file_obj:
        toml_data = tomllib.load(file_obj)

    templates = _load_templates_from_toml(toml_data)
    station_types = set(templates.keys())
    allowed_types = station_types | {"Filler"}
    type_prefix = _load_type_prefixes_from_toml(toml_data, station_types)
    hints = _load_external_type_hints(toml_data, allowed_types)
    stations = _load_stations_from_toml(toml_data, station_types)
    return templates, type_prefix, hints, stations


def _neighbor_tag(
    neighbor_num: Optional[int],
    station_lookup: dict[int, str],
    type_prefix: dict[str, str],
) -> Optional[str]:
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
    return f"{type_prefix[nbr_type]}{neighbor_num}"


def build_substitutions(
    s: Station,
    station_lookup: dict[int, str],
    type_prefix: dict[str, str],
) -> dict[str, str]:
    """Return {placeholder: replacement} for non-None values only.

    Placeholders left out of the dict are NOT touched in the template.
    """
    subs: dict[str, str] = {"XXXX": str(s.number)}

    if s.safety is not None:
        # Replace full .DN placeholder first to avoid generating '.DN.DN'.
        subs["Safety_PowerOn_Placeholder.DN"] = s.safety if s.safety.endswith(".DN") else f"{s.safety}.DN"
        subs["Safety_PowerOn_Placeholder"] = s.safety[:-3] if s.safety.endswith(".DN") else s.safety

    if s.type == "Transfer":
        for ph, val in [
            ("STYYYY", _neighbor_tag(s.conv_rev, station_lookup, type_prefix)),
            ("STZZZZ", _neighbor_tag(s.conv_fwd, station_lookup, type_prefix)),
            ("STVVVV", _neighbor_tag(s.chain_fwd, station_lookup, type_prefix)),
            ("STWWWW", _neighbor_tag(s.chain_rev, station_lookup, type_prefix)),
        ]:
            if val is not None:
                subs[ph] = val
    else:
        for ph, val in [
            ("STYYYY", _neighbor_tag(s.prev, station_lookup, type_prefix)),
            ("STZZZZ", _neighbor_tag(s.next, station_lookup, type_prefix)),
        ]:
            if val is not None:
                subs[ph] = val
    return subs


def _resolve_transfer_infeed_state(
    current_station: Station,
    neighbor_station: Station,
) -> Optional[tuple[str, str]]:
    """Return the CT run tag suffix and infeed state for a CT neighbor link."""
    if neighbor_station.type != "Transfer":
        return None

    current_number = current_station.number
    if neighbor_station.conv_rev == current_number:
        return ("Conv_Run", "State_InfeedingConveyorForward")
    if neighbor_station.conv_fwd == current_number:
        return ("Conv_Run", "State_InfeedingConveyorReverse")
    if neighbor_station.chain_fwd == current_number:
        return ("Chain_Run", "State_InfeedingChainForward")
    if neighbor_station.chain_rev == current_number:
        return ("Chain_Run", "State_InfeedingChainReverse")
    return None


def _rewrite_ct_neighbor_outfeed_checks(
    text: str,
    current_station: Station,
    stations_by_number: dict[int, Station],
) -> str:
    """Patch outfeed checks when the adjacent station is a CT neighbor."""
    if current_station.type == "Transfer":
        neighbor_numbers = (
            current_station.conv_rev,
            current_station.conv_fwd,
            current_station.chain_fwd,
            current_station.chain_rev,
        )
    else:
        neighbor_numbers = (current_station.prev, current_station.next)

    for neighbor_number in neighbor_numbers:
        if neighbor_number is None:
            continue
        neighbor_station = stations_by_number.get(neighbor_number)
        if neighbor_station is None or neighbor_station.type != "Transfer":
            continue

        ct_behavior = _resolve_transfer_infeed_state(current_station, neighbor_station)
        if ct_behavior is None:
            continue

        run_suffix, state_name = ct_behavior
        ct_tag = f"CT{neighbor_station.number}"
        text = text.replace(f"XIC({ct_tag}_Conv_Run)", f"XIC({ct_tag}_{run_suffix})")
        text = text.replace(
            f"EQU(State_Infeeding,{ct_tag}.State)",
            f"EQU({state_name},{ct_tag}.State)",
        )

    return text


def _self_tag(station: Station, type_prefix: dict[str, str]) -> str:
    return f"{type_prefix[station.effective_template_type]}{station.number}"


def _outfeed_complete_neighbor_number(
    station: Station,
    branch_name: str,
    station_lookup: dict[int, str],
) -> Optional[int]:
    if station.is_transfer:
        branch_to_neighbor = {
            "conveyor_forward": station.conv_fwd,
            "conveyor_reverse": station.conv_rev,
            "chain_forward": station.chain_rev,
            "chain_reverse": station.chain_fwd,
        }
    elif station.is_tester:
        branch_to_neighbor = {
            "conveyor_forward": station.next,
            "conveyor_reverse": station.prev,
        }
    else:
        branch_to_neighbor = {
            "conveyor_forward": station.next,
            "conveyor_reverse": station.prev,
        }

    neighbor_number = branch_to_neighbor.get(branch_name)
    if neighbor_number is None:
        return None

    neighbor_type = station_lookup.get(neighbor_number)
    if neighbor_type is None:
        return None
    if _is_gravity_type(neighbor_type):
        return None
    if neighbor_type == "Filler":
        return None

    return neighbor_number


def _resolve_outfeed_complete_infeed_state(
    current_station: Station,
    neighbor_number: int,
    branch_name: str,
    station_lookup: dict[int, str],
    stations_by_number: dict[int, Station],
) -> str:
    neighbor_type = station_lookup.get(neighbor_number)
    if neighbor_type is not None and _is_transfer_type(neighbor_type):
        neighbor_station = stations_by_number.get(neighbor_number)
        if neighbor_station is not None and neighbor_station.is_transfer:
            ct_behavior = _resolve_transfer_infeed_state(current_station, neighbor_station)
            if ct_behavior is not None:
                return ct_behavior[1]

        return {
            "conveyor_forward": "State_InfeedingConveyorForward",
            "conveyor_reverse": "State_InfeedingConveyorReverse",
            "chain_forward": "State_InfeedingChainForward",
            "chain_reverse": "State_InfeedingChainReverse",
        }.get(branch_name, "State_Infeeding")

    if neighbor_type is not None and _is_tester_type(neighbor_type):
        neighbor_station = stations_by_number.get(neighbor_number)
        if neighbor_station is not None and neighbor_station.is_tester:
            if neighbor_station.prev == current_station.number:
                return "State_Infeeding"
            if neighbor_station.next == current_station.number:
                return "State_InfeedingConveyorReverse"

    return "State_Infeeding"


def _rewrite_outfeed_complete_checks(
    text: str,
    station: Station,
    station_lookup: dict[int, str],
    stations_by_number: dict[int, Station],
    type_prefix: dict[str, str],
) -> str:
    self_tag = _self_tag(station, type_prefix)
    replacements: list[tuple[str, str, str]] = []

    if station.is_transfer:
        replacements = [
            (
                (
                    f"EQU(State_OutfeedingConveyorForward,{self_tag}.State) "
                    f"XIO({self_tag}_FE_Conv) XIC(Outfeed_Complete_Placeholder)"
                ),
                "conveyor_forward",
                f"EQU(State_OutfeedingConveyorForward,{self_tag}.State) XIO({self_tag}_FE_Conv)",
            ),
            (
                (
                    f"EQU(State_OutfeedingConveyorReverse,{self_tag}.State) "
                    f"XIO({self_tag}_RE_Conv) XIC(Outfeed_Complete_Placeholder)"
                ),
                "conveyor_reverse",
                f"EQU(State_OutfeedingConveyorReverse,{self_tag}.State) XIO({self_tag}_RE_Conv)",
            ),
            (
                (
                    f"EQU(State_OutfeedingChainForward,{self_tag}.State) "
                    f"XIO({self_tag}_FE_Chain) XIC(Outfeed_Complete_Placeholder)"
                ),
                "chain_forward",
                f"EQU(State_OutfeedingChainForward,{self_tag}.State) XIO({self_tag}_FE_Chain)",
            ),
            (
                (
                    f"EQU(State_OutfeedingChainReverse,{self_tag}.State) "
                    f"XIO({self_tag}_FE_Chain) XIC(Outfeed_Complete_Placeholder)"
                ),
                "chain_reverse",
                f"EQU(State_OutfeedingChainReverse,{self_tag}.State) XIO({self_tag}_FE_Chain)",
            ),
        ]
    elif station.is_tester:
        replacements = [
            (
                f"EQU(State_Outfeeding,{self_tag}.State)XIO({self_tag}_PE_FE)XIC(Outfeed_Complete_Placeholder)",
                "conveyor_forward",
                f"EQU(State_Outfeeding,{self_tag}.State)XIO({self_tag}_PE_FE)",
            ),
            (
                f"EQU(State_OutfeedingConveyorReverse,{self_tag}.State)XIO({self_tag}_PE_RE)XIC(Outfeed_Complete_Placeholder)",
                "conveyor_reverse",
                f"EQU(State_OutfeedingConveyorReverse,{self_tag}.State)XIO({self_tag}_PE_RE)",
            ),
        ]
    else:
        stop_eye = f"{self_tag}_PE_FE"
        replacements = [
            (
                f"EQU(State_Outfeeding,{self_tag}.State)XIO({stop_eye})XIC(Outfeed_Complete_Placeholder)",
                "conveyor_forward",
                f"EQU(State_Outfeeding,{self_tag}.State)XIO({stop_eye})",
            )
        ]

    for old_snippet, branch_name, prefix in replacements:
        neighbor_number = _outfeed_complete_neighbor_number(station, branch_name, station_lookup)
        if neighbor_number is None:
            continue
        target_tag = _neighbor_tag(neighbor_number, station_lookup, type_prefix)
        if target_tag is None:
            continue
        infeed_state = _resolve_outfeed_complete_infeed_state(
            station,
            neighbor_number,
            branch_name,
            station_lookup,
            stations_by_number,
        )
        new_snippet = f"{prefix}NEQ({infeed_state},{target_tag}.State)"
        text = text.replace(old_snippet, new_snippet)

    return text


def _rewrite_mes_logic(text: str, station: Station) -> str:
    if station.mes_code is not None:
        mes_tag = f"MES_{station.mes_code}_{station.number}"
        text = text.replace("MES_FFFFF_XXXX", mes_tag)
    else:
        # Remove the MES tag declaration block when MES is not configured.
        text = re.sub(
            r"\s*<Tag Name=\"MES_FFFFF_XXXX\"[\s\S]*?</Tag>\s*",
            "\n",
            text,
            count=1,
        )

        # Drop MES-only instructions while preserving station state logic.
        replacements = [
            "XIC(MES_FFFFF_XXXX.Data.UpstreamOK)",
            "[OTE(MES_FFFFF_XXXX.OK) ,[",
            "[OTE(MES_FFFFF_XXXX.OK) ,",
            "OTE(MES_FFFFF_XXXX.OK)",
            ",OTL(MES_FFFFF_XXXX.DN)",
            "OTL(MES_FFFFF_XXXX.DN)",
        ]
        for snippet in replacements:
            text = text.replace(snippet, "")

        # Clean up simple branch punctuation artifacts from MES removal.
        cleanup_patterns = [
            ("[ ,", "["),
            (", ]", "]"),
            (",,", ","),
            ("[ [", "[["),
            ("  ", " "),
        ]
        for old, new in cleanup_patterns:
            text = text.replace(old, new)

        # Restore missing branch opener in release rungs after MES branch removal.
        text = re.sub(
            r"(XIC\([A-Za-z0-9_]+_Release_TON\.DN\))XIC\(([A-Za-z0-9_]+_S[Ww]_Active)\)",
            r"\1[XIC(\2)",
            text,
        )

        # Remove orphan final branch closer left after MES branch removal.
        text = re.sub(
            r"(Release_TON\.DN\)\[[^\]]+\]\s*MOV\(State_WaitingForDownstream,[A-Za-z0-9_]+\.State\)\s*)\];",
            r"\1;",
            text,
        )

        # Unwrap queue outfeed branch artifact created after removing MES OTL.
        text = re.sub(
            r"\[\s*MOV\(State_Outfeeding,[A-Za-z0-9_]+\.State\)\s*\];",
            lambda match: match.group(0).replace("[", "", 1).replace("]", "", 1),
            text,
        )

    return text


def _apply_has_route_config(
    text: str,
    station: Station,
    type_prefix: dict[str, str],
) -> str:
    if not station.has_route:
        return text

    self_tag = _self_tag(station, type_prefix)
    tag_pattern = re.compile(rf'(<Tag Name="{re.escape(self_tag)}"[^>]*DataType="SZG_Station"[^>]*>)([\s\S]*?)(</Tag>)')

    match = tag_pattern.search(text)
    if match is None:
        return text

    tag_open, tag_body, tag_close = match.groups()

    # Update the 5th element (Config) of the station's L5K tuple.
    def _rewrite_l5k_tuple(body_text: str) -> str:
        l5k_data_pattern = re.compile(r'(<Data Format="L5K">\s*<!\[CDATA\[)([\s\S]*?)(\]\]>\s*</Data>)')
        l5k_match = l5k_data_pattern.search(body_text)
        if l5k_match is None:
            return body_text

        cdata_text = l5k_match.group(2)

        tuple_pattern = re.compile(
            r"\[\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*\]"
        )

        tuple_match = tuple_pattern.search(cdata_text)
        if tuple_match is None:
            return body_text

        values = [int(tuple_match.group(i)) for i in range(1, 7)]
        values[4] |= ROUTE_CONFIG_MASK
        replacement = f"[{values[0]},{values[1]},{values[2]},{values[3]},{values[4]},{values[5]}]"
        updated_cdata = cdata_text[: tuple_match.start()] + replacement + cdata_text[tuple_match.end() :]
        return (
            body_text[: l5k_match.start()]
            + l5k_match.group(1)
            + updated_cdata
            + l5k_match.group(3)
            + body_text[l5k_match.end() :]
        )

    updated_body = _rewrite_l5k_tuple(tag_body)

    # Ensure Decorated Config member exists and has Config.30 set.
    config_member_pattern = re.compile(
        r'(<DataValueMember Name="Config" DataType="DINT" Radix="Decimal" Value=")(-?\d+)("\s*/>)'
    )
    config_match = config_member_pattern.search(updated_body)
    if config_match is not None:
        config_value = int(config_match.group(2)) | ROUTE_CONFIG_MASK
        updated_body = (
            updated_body[: config_match.start()]
            + config_match.group(1)
            + str(config_value)
            + config_match.group(3)
            + updated_body[config_match.end() :]
        )
    else:
        warning_member_pattern = re.compile(r'(<DataValueMember Name="Warning"[^\n]*/>)')
        warning_match = warning_member_pattern.search(updated_body)
        insert_text = '\n<DataValueMember Name="Config" DataType="DINT" Radix="Decimal" Value="1073741824"/>'
        if warning_match is not None:
            insert_pos = warning_match.end()
            updated_body = updated_body[:insert_pos] + insert_text + updated_body[insert_pos:]
        else:
            structure_close = updated_body.find("</Structure>")
            if structure_close != -1:
                updated_body = updated_body[:structure_close] + insert_text + updated_body[structure_close:]

    updated_tag = tag_open + updated_body + tag_close
    return text[: match.start()] + updated_tag + text[match.end() :]


def generate(
    station: Station,
    station_lookup: dict[int, str],
    stations_by_number: dict[int, Station],
    templates: dict[str, Path],
    type_prefix: dict[str, str],
    dry_run: bool = False,
) -> Path:
    """Render a single station's L5X. Returns the output path."""
    template_path = templates[station.effective_template_type]
    if not template_path.is_file():
        raise FileNotFoundError(template_path)

    text = template_path.read_text(encoding="utf-8", errors="strict")
    text = _rewrite_mes_logic(text, station)
    subs = build_substitutions(station, station_lookup, type_prefix)

    # Apply replacements. Order by length desc as a habit (no overlaps today).
    for ph in sorted(subs, key=len, reverse=True):
        text = text.replace(ph, subs[ph])

    text = _rewrite_ct_neighbor_outfeed_checks(text, station, stations_by_number)
    text = _rewrite_outfeed_complete_checks(
        text,
        station,
        station_lookup,
        stations_by_number,
        type_prefix,
    )
    text = _apply_has_route_config(text, station, type_prefix)

    output_type = station.output_type or station.type
    out_path = OUTPUT_DIR / f"Sta{station.number}_{output_type}.L5X"
    if not dry_run:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
    return out_path


def main(only: Optional[set[int]] = None) -> None:
    templates, type_prefix, hints, all_stations = load_config(STATIONS_TOML)

    # Build lookup so neighbor tags can be type-prefixed correctly.
    stations_by_number = {s.number: s for s in all_stations}
    lookup: dict[int, str] = dict(hints)
    lookup.update({s.number: s.template_type for s in all_stations})
    written: list[Path] = []
    for s in all_stations:
        if only is not None and s.number not in only:
            continue
        out = generate(s, lookup, stations_by_number, templates, type_prefix)
        written.append(out)
        print(f"  wrote {out.name}")
    print(f"\nDone. {len(written)} file(s) written to {OUTPUT_DIR}")


if __name__ == "__main__":
    # Generate only the new chunk this run.
    # Remove the `only=` arg to regenerate everything.
    # NEW_THIS_RUN = {7182, 7183, 7184, 7185, 7210}
    main()  # only=NEW_THIS_RUN)
