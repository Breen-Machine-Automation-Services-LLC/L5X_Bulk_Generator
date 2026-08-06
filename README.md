# L5X Bulk Generator (Draft from Uncommented Code)

## Purpose
This project generates and merges Rockwell Studio 5000 import artifacts from TOML configuration and XML/L5X templates.
It automates three major outputs:
- Per-station Program L5X files
- Controller Program merge L5X files
- Module and IO simulation L5X exports
- FactoryTalk display XML regeneration for GO_Conv groups

## Inputs
- input/stations.toml: station topology, station types, optional behavior flags
- input/Backplane.toml: module hierarchy and addressing
- reference/*.L5X and reference/*.xml: source templates for station programs, modules, and HMI displays
- output/stationPrograms/*.L5X: per-station intermediate files consumed by merge step

## Outputs
- output/stationPrograms/Sta####_<Type>.L5X: generated per-station programs
- output/Program_<timestamp>.L5X or Program_<tag>_<timestamp>.L5X: merged controller-level import file
- output/IO_Simulate_<timestamp>.L5X: generated Program-target L5X with IO simulation routine and timer tags
- output/Backplane_<timestamp>.L5X style module-target exports from module generator
- output/Level1_<timestamp>.xml and output/Level2_<timestamp>.xml: regenerated GO_Conv HMI groups

## Script Responsibilities
- generate_programs.py
  - Loads station/template/prefix configuration from stations TOML.
  - Applies station-specific placeholder substitutions into template L5X text.
  - Rewrites CT adjacency logic and outfeed completion checks.
  - Optionally toggles MES behavior and route configuration bits.
  - Writes one station program file per station.

- merge_programs.py
  - Merges many station program L5X files into one controller import file.
  - De-duplicates controller-level objects by Name.
  - Replaces station-matched Tag objects when appropriate.
  - Preserves and backfills missing Tag comment operands.
  - Reorders Controller children to schema-safe order before writing.

- generate_io_l5x.py
  - Builds directed movement graph from station relationships.
  - Generates bidirectional simulation movements for internal station pairs.
  - Emits timer tags and an IO_Simulate routine with generated rung text.
  - Writes Program-target L5X for import.

- generate_modules_l5x.py
  - Loads flat modules list and type-to-template mapping from TOML.
  - Clones template module trees and applies parent/port/address binding.
  - Builds Module-target export rooted at selected target module.

- generate_HMI_object_xml.py
  - Loads stations and derives machine prefixes by station type.
  - Locates GO_Conv template group in display XML.
  - Rebuilds all GO_Conv groups from station list and popup mapping.
  - Writes timestamped Level1/Level2 display XML outputs.

## Execution Flow
1. Generate station programs with generate_programs.py.
2. Merge station programs with merge_programs.py.
3. Generate optional support artifacts:
   - generate_io_l5x.py for IO simulation routine package
   - generate_modules_l5x.py for module-target import package
   - generate_HMI_object_xml.py for display object regeneration

## Dependencies
- Python 3.11+
- lxml
- tomllib (stdlib in Python 3.11+)

## Data and Behavior Assumptions (Inferred)
- Station numbers are unique.
- Station types must match keys defined in [templates].
- Type prefixes must include all station template types plus Filler.
- Transfer stations use conveyor and chain upstream/downstream references.
- Missing or zero relationship integers represent absent connections.
- Unknown external neighbors are tolerated in some paths and skipped in others.
- Output filenames are timestamped with Windows-safe ISO formatting.
- L5X importer order sensitivity is handled by explicit Controller child reorder during merge.

## Typical Operator Use
- Edit TOML inputs under input/.
- Run station generation.
- Run merge to produce one importable program file.
- Run optional module/IO/HMI generators depending on commissioning need.
