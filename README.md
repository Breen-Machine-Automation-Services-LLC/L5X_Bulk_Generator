# L5X Bulk Generator (Python)

Python replacement for `Standard_Code_Maker.xlsm` that generates per-station
L5X routine files from the SubZero standard templates **and** merges them
into a single Studio 5000 import file.

Built for the 1394 Wall Oven Expansion project (Wolf), April 2026.
Equivalent to `GenerateRoutines` + `AppendXMLFiles` macros, but:
- Faster (~3 sec for 100+ files vs. minutes in Excel).
- Doesn't crash on missing `<Modules>` / `<AddOnInstructionDefinitions>`
  containers (auto-creates them).
- Preserves CDATA wrappers on STRING data members (Studio 5000 requires it).
- Re-orders `<Controller>` children to the canonical sequence Studio 5000
  enforces.
- Each station becomes its own sibling `<Program>` (not flattened into one).
- Optional station-number filter so you can re-import a single section.

## Folder layout

```
Programs/
  1394 Standard Programs/                 <- source templates (read-only)
  GeneratedRoutines- Hybrid Main Line/    <- per-station L5X output + merged file
  L5X Bulk Generator (Python)/            <- this folder
      generate_l5x.py
      merge_l5x.py
      README.md
```

## Prerequisites

- Python 3.11+ (3.12 was used).
- `lxml` package: `pip install lxml`
- Read/write access to the project's Programs folder on G:.

## Workflow

1. **Edit the station list** in `generate_l5x.py` (see "Adding stations" below).
2. **Generate per-station L5X files**:
   ```powershell
   python generate_l5x.py
   ```
   Outputs `Sta####_<Type>.L5X` into the `GeneratedRoutines-...` folder.
3. **Merge** into a single importable L5X:
   ```powershell
   python merge_l5x.py
   ```
   Outputs `Program_<timestamp>.L5X` (or `Program_<tag>_<timestamp>.L5X` if
   the station filter is active).
4. **Import** that single file in Studio 5000 (right-click controller →
   Import Component → Program). All routines/tags/UDTs/AOIs land in one shot.

> **Always** back up the target ACD before importing. Imports are not
> reversible without it.

## Templates and tag prefixes

The generator finds & replaces placeholders in the templates listed in
`TEMPLATES`:

| Station type | Template L5X                              | Self-tag prefix |
|--------------|-------------------------------------------|-----------------|
| Workstation  | `Lift_Standard_Code_Program.L5X`          | `Li`            |
| Queue        | `Staight_Track_Standard_Code_Program.L5X` | `ST`            |
| Transfer     | `Chain_Transfer_Standard_Code_Program.L5X`| `CT`            |
| TestStation  | `Staight_Track_Standard_Code_Program.L5X` | `ST` *          |
| Filler       | (not generated; reference only)           | `FI`            |
| Gravity      | (not generated; reference only)           | `GR`            |

\* TestStation reuses the straight-track template; the `TS` label survives
only in the filename and Program name. Self-tag is still `ST####` because
the template hardcodes it.

> **Filename typo:** `Staight_Track...` (missing the second `r`) is the
> name in the source library. Don't rename it.

## Placeholder substitutions

| Placeholder                     | Replaced with                          |
|---------------------------------|----------------------------------------|
| `XXXX`                          | Station number, e.g. `4150`            |
| `Safety_PowerOn_Placeholder`    | `Safety_PowerOn_Zone<n>` (per-station) |
| `STYYYY`                        | Upstream / `conv_rev` neighbor tag     |
| `STZZZZ`                        | Downstream / `conv_fwd` neighbor tag   |
| `STVVVV`                        | `chain_fwd` neighbor (Transfer only)   |
| `STWWWW`                        | `chain_rev` neighbor (Transfer only)   |
| `FFFFF`                         | **Left as-is** (purpose TBD)           |

For Queue / Workstation / TestStation, the two neighbors come from
`prev` / `next`. For Transfer, all four come from the conv/chain fields.

If a neighbor is `None`, the placeholder stays unsubstituted so it's easy to
fix in Studio 5000 later.

Neighbor tags are **type-aware**: `_neighbor_tag()` looks up the neighbor's
station type and applies the right prefix. A queue feeding into a chain
transfer correctly emits `CT4280`, not `ST4280`.

## Adding stations

Each station is a `Station` dataclass entry:

```python
# Queue / Workstation / TestStation
Station(number=4150, type="Workstation", prev=4140, next=4160,
        safety_zone=8)

# Transfer
Station(number=4280, type="Transfer", safety_zone=8,
        conv_rev=4270, conv_fwd=4290,
        chain_fwd=4281, chain_rev=4282)

# Lift-less workstation: file labeled "Workstation" but uses straight-track
# template (so self-tag is ST####, not Li####).
Station(number=4281, type="Workstation", template_type="Queue",
        prev=4280, next=4280)
```

Fields:
- `number` — station number, used in filename and `XXXX` substitution.
- `type` — used for the `_<Type>` filename suffix.
- `template_type` — overrides which template to render from (and therefore
  which self-tag prefix the template emits). Use this for lift-less
  workstations or test stations.
- `prev` / `next` — for Queue / Workstation / TestStation.
- `conv_rev` / `conv_fwd` / `chain_fwd` / `chain_rev` — for Transfer.
- `safety_zone` — integer (e.g., `8` or `10`); generates
  `Safety_PowerOn_Zone<n>`. Defaults to `SAFETY_TAG` module constant.

If a neighbor is **outside the generated set** (different chunk or different
PLC), add an entry to `EXTERNAL_TYPE_HINTS` so its tag gets the right prefix:

```python
EXTERNAL_TYPE_HINTS: dict[int, str] = {
    7200: "Transfer",   # CT7200, upstream feeder
    7155: "Filler",     # FI7155, between two transfers
    ...
}
```

## Generating in chunks

Set `NEW_THIS_RUN` at the bottom of `generate_l5x.py` to a `set` of station
numbers to limit output:

```python
if __name__ == "__main__":
    NEW_THIS_RUN = {7164, 7166, 7168, 7170, 7172, 7174, 7176, 7178}
    main(only=NEW_THIS_RUN)
```

To regenerate everything, change to `main()` (no `only=` arg).

## Merger options (`merge_l5x.py`)

Top-of-file constants:

| Constant       | Default                   | Purpose                                    |
|----------------|---------------------------|--------------------------------------------|
| `INPUT_DIR`    | `GeneratedRoutines-...`   | Where to read per-station files            |
| `OUTPUT_DIR`   | same                      | Where to write the merged file             |
| `BASE_FILE`    | `None`                    | Specific file to use as base; else first   |
| `INPUT_GLOB`   | `Sta*.L5X`                | File pattern to merge                      |
| `STATION_MIN`  | `None`                    | Lower bound (inclusive) of station number  |
| `STATION_MAX`  | `None`                    | Upper bound (inclusive)                    |
| `OUTPUT_TAG`   | `""`                      | Cosmetic suffix when filter is active      |

**Filter example** — to merge only the 7000-series:
```python
STATION_MIN = 7000
STATION_MAX = 7999
OUTPUT_TAG  = "7000s"
```
Output filename: `Program_7000s_<timestamp>.L5X`.

**Set both bounds back to `None`** to merge everything.

## Known facts and gotchas (decoded the hard way)

These are non-obvious things confirmed by trial and error against
Studio 5000 imports. Don't change them without good reason.

1. **`lxml.etree.XMLParser(strip_cdata=False)`** is required. lxml
   silently strips `<![CDATA[...]]>` by default. Studio 5000's L5X
   importer rejects STRING `<Data Format="L5K">` and
   `<Data Format="String">` elements that don't have CDATA wrappers.
   Symptom: *"String invalid"* error on any `State_Desc_HMI`-style tag.

2. **`<Controller>` child order matters.** Studio 5000 enforces this
   sequence and rejects the file with *"Element \<X\> is in the wrong
   order"* if it's off. The merger reorders to:
   `Description, RedundancyInfo, Security, SafetyInfo, DataTypes, Modules,
   AddOnInstructionDefinitions, Tags, Programs, Tasks, ...`
   The original VBA macro fails on any base file missing one of these
   containers because `SelectNodes(name)(0)` returns `Nothing`.

3. **Programs are merged as siblings**, not flattened. Earlier merger
   versions piled all stations of the same template into one Program
   (e.g., `Staight_Track_Standard_Code` with 60 routines). Each L5X
   source file's single `<Program>` is renamed to its file stem
   (`Sta4150_Workstation`) and appended.

4. **External UDT dependency: `SZG_IOL_Inclinometer`**. Lift template tags
   reference this UDT but no template defines it. Must already exist in
   the target controller (export from a prior project, import as a
   separate L5X *before* importing the merged file). 9 references per
   merge, one per workstation lift.

5. **Source files don't include `<Tasks>`**. Studio 5000 assigns new
   programs to MainTask on import. If you want them on a different task
   you'll need to move them after import (or extend the merger to inject
   a `<Tasks>` block).

6. **Cross-PLC neighbors stay as references.** `ST5520` (4690's downstream)
   and `CT4700` were both kept as external refs. Logix accepts
   forward references during import but they show as unresolved until
   the target tags exist.

7. **Filename typo: `Staight_Track`** (missing `r`) is the name in
   `1394 Standard Programs/` and the substring used as the Program Name
   inside the L5X. Renaming it would break the templates.

## What's currently generated

| Series       | Count | Zone | Notes                                       |
|--------------|------:|:----:|---------------------------------------------|
| 4000-series  |    72 |  8   | Stations 4000-4690 (full hybrid main line)  |
| 7000-series  |    37 |  10  | Stations 7150-7308 (test station section)   |
| **Total**    |   109 |      |                                             |

Skipped (need filler/gravity templates):
- Fillers: 4282, 4302, 4322, 7155, 7165, 7167, 7169, 7171, 7173, 7175,
  7177, 7179, 7181, 7186, 7205
- Gravity kickouts: 4561, 4581, 4601, 7189, 7190

## Outstanding TODOs

1. Filler template + generation (currently external-ref only).
2. Gravity kickout template + generation.
3. Lift `7999` rework (mentioned but not started).
4. Decode `FFFFF` placeholder — meaning still unknown. Currently
   left as literal `FFFFF` in output per direction 2026-04-24.
5. Auto-bundle `SZG_IOL_Inclinometer.L5X` (and any other SubZero
   standard UDTs) into the merger so a fresh controller doesn't need
   manual UDT pre-import.

## Troubleshooting

| Symptom on import                                              | Cause                                              | Fix                                                              |
|----------------------------------------------------------------|----------------------------------------------------|------------------------------------------------------------------|
| *"String invalid"* on a `State_Desc_HMI` or similar tag        | CDATA stripped during merge                        | Re-merge with current `merge_l5x.py` (uses `strip_cdata=False`)  |
| *"Element \<Modules\> is in the wrong order"*                  | Container appended after Tags                      | Re-merge with current `merge_l5x.py` (calls `_reorder_controller_children`) |
| *"Data type could not be found: SZG_IOL_Inclinometer"*         | UDT missing from target controller                 | Export UDT from donor project, import its L5X first              |
| Stations all collapsed into one Program                        | Old merger flattened at Routine level              | Re-merge with current `merge_l5x.py` (per-Program merge)         |
| Object variable / With block variable not set (VBA macro)      | Base L5X missing a container element               | Use `merge_l5x.py` instead — it auto-creates missing containers  |
| Tag prefix mismatch (e.g., transfer references `TS####` but `Sta####_TestStation.L5X` defines `ST####`) | `TYPE_PREFIX` mismatch with template's hardcoded self-tag | Align `TYPE_PREFIX[type]` with what the actual template emits    |

## Provenance

Built collaboratively April 23 - April 28, 2026 to replace the
Standard_Code_Maker VBA macros for the 1394 Wall Oven Expansion project.
See git history of the scripts (if checked into a repo) or the
modification dates on the .py files for change history.
