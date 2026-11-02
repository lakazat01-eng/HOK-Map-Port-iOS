# HOK Map Port iOS

A local analysis and conversion workspace for automatically porting Honor of Kings Android map resources to iOS.

## Actual project objective

Build a batch tool that understands the relationship between Android and iOS map resources, then converts/stages compatible assets into a separate iOS candidate output. This is not merely a filename/CAB-ID matcher and must not require manually editing files one by one.

## Input roles

- `input/android/map_hok_andr`: Android map resources; primary source for assets to port.
- `input/ios/map_ios_orgi`: original iOS map resources; target baseline, dependency reference, and compatibility reference.
- `input/hok_sence_ios`: third-party iOS scene reference; compare scene structure and map composition against Android and the original iOS build. It is a reference source, not automatically a destination.

## Required workflow

1. Inventory all Android and iOS Resources/Extra assets.
2. Compare Android map scenes with corresponding iOS original scenes and the separate iOS scene reference.
3. Identify scene structure, serialized objects, asset references, CAB dependencies, and platform-specific differences.
4. Determine which Android assets can be reused directly, which require conversion/rebuilding, and which cannot yet be handled.
5. Implement batch conversion and dependency-aware staging.
6. Validate output structure and report unsupported/error cases.
7. Keep originals untouched; export all candidates to a separate output directory for in-game testing.

## Current technical status

- `tools/batch_index.py`: read-only inventory and candidate mapper. Filename/path/CAB matches are evidence leads only.
- `tools/scene_compare.py`: read-only scene comparator. Compares UnityFS headers, UnityCN metadata fingerprints, CAB/archive IDs, and readable asset-string clues for same-name scenes.
- `tools/auto_port.py`: conservative analyzer/stager; not yet a general Android-to-iOS converter.
- Android/iOS scene bundles currently show UnityFS 2022.3.5f1 and UnityCN indicators. Object-level comparison requires a valid UnityCN decoding path and a Unity serialized-file parser.

## Safety and data policy

- Never overwrite source files.
- Never treat matching filenames, CAB IDs, or string overlap as proof of compatibility.
- Never copy an Android UnityCN bundle into iOS output merely because it has a matching iOS filename.
- Do not commit game archives, extracted proprietary assets, account data, or other copyrighted game resources. Commit scripts, documentation, and small diagnostic text reports only.

## Runtime

Initial tools use Python 3 standard library only and target Python 3.9+. Optional external tools may be detected at runtime, but the workflow must remain explicit about unsupported operations.
