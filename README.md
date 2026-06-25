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
L5X_Bulk_Generator/
   input/                 <- source templates + stations.toml
   output/                <- generated per-station L5X, merged L5X, HMI XML
   reference/             <- reference L5X/XML files and proposals
   generate_programs.py   <- per-station program generator
   merge_programs.py      <- merged Program_*.L5X builder
   generate_HMI_object_xml.py
   README.md
```

## Prerequisites

- Python 3.11+ (3.12 was used).
- `lxml` package: `pip install lxml`
- Template L5X files present under `input/`.

## Workflow

1. **Edit the station list and template paths** in `input/stations.toml` (see "Adding stations" below).
2. **Generate per-station L5X files**:
   ```powershell
   python generate_programs.py
   ```
   Outputs `Sta####_<Type>.L5X` into `output/stationPrograms/`.
3. **Merge** into a single importable L5X:
   ```powershell
   python merge_programs.py
   ```
   Outputs `Program_<timestamp>.L5X` in `output/`.
4. **Regenerate the FactoryTalk MAIN display groups** when station numbers
   change:
   ```powershell
   python generate_HMI_object_xml.py
   ```
   Uses `reference/MAIN.xml` as the template and writes a timestamped
   `MAIN_<timestamp>.xml` into `output/` with one `GO_Conv####` group per
   station from `input/stations.toml`.
5. **Import** that single file in Studio 5000 (right-click controller →
   Import Component → Program). All routines/tags/UDTs/AOIs land in one shot.

> **Always** back up the target ACD before importing. Imports are not
> reversible without it.

## Templates and tag prefixes

The generator reads template paths from the `[templates]` table in
`input/stations.toml`. Current defaults are:

| Station type | Template L5X                              | Self-tag prefix |
|--------------|-------------------------------------------|-----------------|
| Workstation  | `Lift_Standard_Code_Program.L5X`          | `Li`            |
| Queue        | `Staight_Track_Standard_Code_Program.L5X` | `ST`            |
| Transfer     | `Chain_Transfer_Standard_Code_Program.L5X`| `CT`            |
| TestStation  | `Bi_Directional_Track_Standard_Code_Program.L5X` | `ST` *    |
| Filler       | (not generated; reference only)           | `FI`            |
| Gravity      | (not generated; reference only)           | `GR`            |

\* TestStation filenames and Program names use the `TestStation` label, but
the generated self-tag prefix remains `ST####`.

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

Stations now come from `input/stations.toml` under `[stations].data`.
Examples:

```toml
# Queue
{ number = 4150, type = "Queue", prev = 4140, next = 4160, isWorkstation = true }

# Transfer
{ number = 4280, type = "Transfer", conv_upstream = 4270, conv_downstream = 4290,
   chain_upstream = 4281, chain_downstream = 4282 }

# Test station
{ number = 7301, type = "Queue", prev = 7166, next = 7166, isTestStation = true,
   safety_zone = 10 }
```

Fields:
- `number` — station number, used in the filename and `XXXX` substitution.
- `type` — `Queue`, `Transfer`, or `Lift`/other project label. Generator logic
   maps transfer rows to `Transfer`, `isWorkstation = true` rows to
   `Workstation`, and `isTestStation = true` rows to `TestStation`.
- `prev` / `next` — for queue-style rows.
- `conv_upstream` / `conv_downstream` / `chain_upstream` / `chain_downstream`
   — for transfer rows.
- `isWorkstation` — marks a queue-mechanics row that should be emitted as a
   `Workstation` file.
- `isTestStation` — marks a row that should be emitted as a `TestStation` file.
- `safety_zone` — optional integer override. Defaults to
   `[config].default_safety_zone`.

Use `0` for unresolved or intentionally missing neighbors. The generator
converts `0` to `None` and leaves the corresponding placeholder untouched.

If a neighbor is **outside the generated set** (different chunk or different
PLC), add an entry to `[external_type_hints]` so its tag gets the right prefix:

```toml
[external_type_hints]
7200 = "Transfer"  # CT7200, upstream feeder
7155 = "Filler"    # FI7155, between two transfers
```

## Generating in chunks

`generate_programs.py` still supports passing an `only` set into `main()` for
partial regeneration. The current file leaves that disabled by default so a
plain run regenerates everything.

For a one-off chunk, temporarily set `NEW_THIS_RUN` at the bottom of
`generate_programs.py` to a `set` of station
numbers to limit output:

```python
if __name__ == "__main__":
    NEW_THIS_RUN = {7164, 7166, 7168, 7170, 7172, 7174, 7176, 7178}
    main(only=NEW_THIS_RUN)
```

To regenerate everything, change to `main()` (no `only=` arg).

## Merger options (`merge_programs.py`)

Top-of-file constants:

| Constant       | Default                   | Purpose                                    |
|----------------|---------------------------|--------------------------------------------|
| `INPUT_DIR`    | `output/stationPrograms`  | Where to read per-station files            |
| `OUTPUT_DIR`   | `output`                  | Where to write the merged file             |
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
   the source library and the substring used as the Program Name inside the
   L5X. Renaming it would break the templates.

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
| *"String invalid"* on a `State_Desc_HMI` or similar tag        | CDATA stripped during merge                        | Re-merge with current `merge_programs.py` (uses `strip_cdata=False`)  |
| *"Element \<Modules\> is in the wrong order"*                  | Container appended after Tags                      | Re-merge with current `merge_programs.py` (calls `_reorder_controller_children`) |
| *"Data type could not be found: SZG_IOL_Inclinometer"*         | UDT missing from target controller                 | Export UDT from donor project, import its L5X first              |
| Stations all collapsed into one Program                        | Old merger flattened at Routine level              | Re-merge with current `merge_programs.py` (per-Program merge)         |
| Object variable / With block variable not set (VBA macro)      | Base L5X missing a container element               | Use `merge_programs.py` instead — it auto-creates missing containers  |
| Tag prefix mismatch (e.g., transfer references `TS####` but `Sta####_TestStation.L5X` defines `ST####`) | `TYPE_PREFIX` mismatch with template's hardcoded self-tag | Align `TYPE_PREFIX[type]` with what the actual template emits    |

## Provenance

Built collaboratively April 23 - April 28, 2026 to replace the
Standard_Code_Maker VBA macros for the 1394 Wall Oven Expansion project.
See git history of the scripts (if checked into a repo) or the
modification dates on the .py files for change history.
