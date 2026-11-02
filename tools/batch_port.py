#!/usr/bin/env python3
"""Conservative batch port planner for HoK map resources.

Scans Android Resources + Extra against the original iOS Resources + Extra and
an optional third-party iOS reference. It stages only byte-identical files into
a separate candidate tree. Non-identical bundles are classified for manual,
format-specific conversion; this tool does not decrypt or rebuild protected
UnityCN archives.

Python 3.9+, standard library only.
"""
import argparse
import csv
import hashlib
import json
import os
import re
import shutil
from pathlib import Path

CHUNK = 1024 * 1024
UNITYFS = b"UnityFS\0"


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def cstring(data, pos):
    end = data.find(b"\0", pos)
    if end < 0:
        raise ValueError("unterminated UnityFS string")
    return data[pos:end].decode("ascii", "replace"), end + 1


def unityfs_info(path):
    with path.open("rb") as stream:
        head = stream.read(256)
    if not head.startswith(UNITYFS):
        return None
    try:
        pos = 8
        fmt = int.from_bytes(head[pos:pos + 4], "big")
        pos += 4
        unity_version, pos = cstring(head, pos)
        revision, pos = cstring(head, pos)
        fixed = head[pos:pos + 20]
        if len(fixed) != 20:
            raise ValueError("truncated fixed header")
        flags = int.from_bytes(fixed[16:20], "big")

        # Heuristic indicator only; this does not validate or decode UnityCN.
        match = re.match(r"^(\d+)\.(\d+)\.(\d+)", revision)
        if not match:
            raise ValueError("unrecognized Unity revision")
        version = tuple(int(x) for x in match.groups())
        old_layout = (
            version[0] < 2020 or
            (version[0] == 2020 and version[1] == 3 and version[2] <= 34) or
            (version[0] == 2021 and version[1] == 3 and version[2] <= 2) or
            (version[0] == 2022 and version[1] == 3 and version[2] <= 1)
        )
        mask = 0x200 if old_layout else 0x400
        return {
            "format_version": fmt,
            "unity_version": unity_version,
            "unity_revision": revision,
            "flags": flags,
            "flags_hex": "0x%X" % flags,
            "compression_id": flags & 0x3F,
            "unitycn_indicator_mask": "0x%X" % mask,
            "unitycn_indicator": bool(flags & mask),
            "indicator_is_heuristic": True,
        }
    except (ValueError, IndexError):
        return {"parse_error": "invalid_or_truncated_header"}


def scan(root, label):
    rows = []
    if not root.is_dir():
        return rows
    for base, dirs, files in os.walk(str(root)):
        dirs.sort()
        for name in sorted(files):
            path = Path(base) / name
            try:
                rows.append({
                    "root": label,
                    "path": path.relative_to(root).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": sha256(path),
                    "unityfs": unityfs_info(path),
                })
            except OSError as exc:
                rows.append({
                    "root": label,
                    "path": path.relative_to(root).as_posix(),
                    "error": str(exc),
                })
    return rows


def classify_asset(path):
    name = path.lower()
    if "/scene/" in "/" + name or "terrain" in name:
        return "MAP_TERRAIN"
    if "minimap_support" in name or "minimap-support" in name:
        return "MINIMAP_SUPPORT_UI"
    if "ugui_map" in name or "minimap" in name:
        return "MINIMAP_UI"
    if "shop" in name:
        return "SHOP_UI"
    if "tower" in name:
        return "TOWERS"
    if "monster" in name:
        return "MONSTERS"
    if "soldier" in name or "minion" in name:
        return "MINIONS"
    if "ugui" in name or "/ui" in "/" + name or "uisystem" in name:
        return "OTHER_UI"
    return "OTHER"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="project root")
    parser.add_argument(
        "--stage-identical", action="store_true",
        help="copy only exact path + SHA256 matches to output/ios_candidate"
    )
    args = parser.parse_args()
    base = Path(args.base).resolve()
    inp = base / "input"

    roots = {
        "android_resources": inp / "android/map_hok_andr/Resources/1.64.1",
        "android_extra": inp / "android/map_hok_andr/Extra",
        "ios_resources": inp / "ios/map_ios_orgi/Resources/1.64.1",
        "ios_extra": inp / "ios/map_ios_orgi/Extra",
        "ios_reference": inp / "hok_sence_ios",
    }
    required = ("android_resources", "ios_resources", "ios_extra")
    for key in required:
        if not roots[key].is_dir():
            raise SystemExit("Missing input root: %s (%s)" % (key, roots[key]))

    inventory = {key: scan(path, key) for key, path in roots.items()}
    indexes = {}
    for key in ("ios_resources", "ios_extra"):
        indexes[key] = {
            row["path"]: row for row in inventory[key] if "sha256" in row
        }

    ios_by_hash = {}
    for key in ("ios_resources", "ios_extra"):
        for row in inventory[key]:
            if "sha256" in row:
                ios_by_hash.setdefault(row["sha256"], []).append(row)

    reference_by_name = {}
    for row in inventory["ios_reference"]:
        if "sha256" in row and row.get("size", 0) > 16 and row.get("unityfs"):
            reference_by_name.setdefault(Path(row["path"]).name.lower(), []).append(row)

    mapping = []
    staged = []
    collisions = []
    for source_root in ("android_resources", "android_extra"):
        target_root = "ios_resources" if source_root == "android_resources" else "ios_extra"
        for row in inventory[source_root]:
            if "sha256" not in row:
                mapping.append({
                    "source_root": source_root,
                    "source_path": row["path"],
                    "status": "unreadable",
                    "error": row.get("error"),
                })
                continue

            # HoK can place a scene under iOS Extra even when Android ships it
            # under Resources. Search both iOS tiers before declaring no match.
            same_candidates = [
                (key, indexes[key][row["path"]])
                for key in ("ios_resources", "ios_extra")
                if row["path"] in indexes[key]
            ]
            exact_candidates = [
                (key, hit) for key, hit in same_candidates
                if hit.get("sha256") == row["sha256"]
            ]
            same_root = exact_candidates[0][1] if exact_candidates else (
                same_candidates[0][1] if same_candidates else None
            )
            matched_tier = (
                exact_candidates[0][0] if exact_candidates
                else same_candidates[0][0] if same_candidates
                else None
            )
            reference_hits = reference_by_name.get(Path(row["path"]).name.lower(), [])
            source_unity = row.get("unityfs") or {}
            protected = bool(source_unity.get("unitycn_indicator"))

            item = {
                "source_root": source_root,
                "source_path": row["path"],
                "asset_category": classify_asset(row["path"]),
                "source_size": row["size"],
                "source_sha256": row["sha256"],
                "source_unityfs": source_unity,
                "target_root": matched_tier or target_root,
                "target_path": row["path"] if same_root else None,
                "reference_candidates": [
                    {"path": hit["path"], "size": hit["size"],
                     "sha256": hit["sha256"]}
                    for hit in reference_hits
                ],
            }

            if exact_candidates:
                item["status"] = "exact_identical"
                if args.stage_identical:
                    relative = Path(
                        "Resources/1.64.1" if matched_tier == "ios_resources" else "Extra"
                    ) / row["path"]
                    destination = base / "output/ios_candidate" / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if destination.exists():
                        if sha256(destination) != row["sha256"]:
                            item["stage_status"] = "skipped_existing_conflict"
                            collisions.append(str(destination))
                        else:
                            item["stage_status"] = "already_staged"
                    else:
                        shutil.copy2(roots[source_root] / row["path"], destination)
                        item["stage_status"] = "staged"
                        staged.append(str(relative))
            elif same_root:
                target_unity = same_root.get("unityfs") or {}
                item["target_size"] = same_root.get("size")
                item["target_sha256"] = same_root.get("sha256")
                item["target_unityfs"] = target_unity
                item["status"] = (
                    "unitycn_review_required"
                    if protected or target_unity.get("unitycn_indicator")
                    else "format_specific_conversion_required"
                )
            elif ios_by_hash.get(row["sha256"]):
                item["status"] = "exact_content_other_path_needs_review"
                item["exact_content_candidates"] = [
                    {"root": hit["root"], "path": hit["path"]}
                    for hit in ios_by_hash[row["sha256"]]
                ]
            elif reference_hits:
                item["status"] = "reference_only_candidate"
            elif protected:
                item["status"] = "unitycn_source_no_target"
            else:
                item["status"] = "no_ios_candidate"
            mapping.append(item)

    counts = {}
    category_counts = {}
    for item in mapping:
        status = item["status"]
        counts[status] = counts.get(status, 0) + 1
        category = item.get("asset_category")
        if category:
            category_counts[category] = category_counts.get(category, 0) + 1

    report = {
        "tool": "HOK Map Port batch planner",
        "version": 1,
        "mode": "stage_exact_identical" if args.stage_identical else "analyze_only",
        "roots": {key: str(value) for key, value in roots.items()},
        "summary": {
            "android_resources_files": len(inventory["android_resources"]),
            "android_extra_files": len(inventory["android_extra"]),
            "ios_resources_files": len(inventory["ios_resources"]),
            "ios_extra_files": len(inventory["ios_extra"]),
            "ios_reference_files": len(inventory["ios_reference"]),
            "status_counts": counts,
            "category_counts": category_counts,
            "staged": len(staged),
            "stage_collisions": len(collisions),
        },
        "staged_paths": staged,
        "stage_collisions": collisions,
        "mapping": mapping,
        "inventory": inventory,
        "limitations": [
            "UnityCN indicator is heuristic metadata, not proof of encryption.",
            "No protected archive is decrypted, unpacked, or rebuilt.",
            "A matching path, name, CAB ID, or Unity version is not proof of compatibility.",
            "Only byte-identical files with the same relative path are staged; Android Resources may map to either iOS Resources or iOS Extra.",
            "Non-identical assets require a format-specific, supported converter before staging."
        ],
    }

    out = base / "output"
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "batch_port_report.json"
    csv_path = out / "batch_port_report.csv"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow([
            "source_root", "source_path", "asset_category", "status", "target_root",
            "target_path", "source_size", "target_size", "stage_status"
        ])
        for item in mapping:
            writer.writerow([
                item.get("source_root"), item.get("source_path"),
                item.get("asset_category"), item.get("status"), item.get("target_root"),
                item.get("target_path"), item.get("source_size"),
                item.get("target_size"), item.get("stage_status")
            ])

    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print("JSON:", json_path)
    print("CSV:", csv_path)
    if args.stage_identical:
        print("Candidate:", out / "ios_candidate")


if __name__ == "__main__":
    main()
