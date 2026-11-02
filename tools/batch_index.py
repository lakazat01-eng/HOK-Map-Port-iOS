#!/usr/bin/env python3
"""Batch inventory and cross-platform asset mapper for HOK map bundles.

Read-only: never edits inputs and never copies Android bundles into iOS output.
Produces machine-readable inventories and candidate mappings for later converters.
Python 3.9+, standard library only.
"""
import argparse
import csv
import hashlib
import json
import os
import re
from collections import defaultdict
from pathlib import Path

CHUNK = 1024 * 1024
UNITYFS = b"UnityFS\0"
CAB_RE = re.compile(rb"CAB-([0-9a-fA-F]{32})")
ARCHIVE_RE = re.compile(rb"archive:/CAB-([0-9a-fA-F]{32})")
PRINTABLE_RE = re.compile(rb"[ -~]{5,}")
ASSET_EXTS = (".assetbundle", ".bundle")


def sha256_file(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def cstring(data, pos):
    end = data.find(b"\0", pos)
    if end < 0:
        raise ValueError("unterminated header string")
    return data[pos:end].decode("ascii", "replace"), end + 1


def parse_unityfs(head):
    if not head.startswith(UNITYFS):
        return None
    try:
        pos = 8
        version = int.from_bytes(head[pos:pos + 4], "big")
        pos += 4
        unity_version, pos = cstring(head, pos)
        revision, pos = cstring(head, pos)
        raw = head[pos:pos + 20]
        if len(raw) != 20:
            raise ValueError("truncated UnityFS header")
        flags = int.from_bytes(raw[16:20], "big")
        m = re.match(r"^(\d+)\.(\d+)\.(\d+)", revision)
        mask = None
        if m:
            major, minor, patch = map(int, m.groups())
            old = (major < 2020 or
                   (major == 2020 and minor == 3 and patch <= 34) or
                   (major == 2021 and minor == 3 and patch <= 2) or
                   (major == 2022 and minor == 3 and patch <= 1))
            mask = 0x200 if old else 0x400
        comp = flags & 0x3f
        return {
            "format_version": version,
            "unity_version": unity_version,
            "unity_revision": revision,
            "flags": flags,
            "flags_hex": "0x%X" % flags,
            "compression_id": comp,
            "compression": {0:"none", 1:"LZMA", 2:"LZ4", 3:"LZ4HC", 4:"LZHAM"}.get(comp, "unknown"),
            "unitycn_mask": ("0x%X" % mask) if mask else None,
            "unitycn_flagged": bool(mask and flags & mask),
            "metadata_offset": pos + 20 if mask and flags & mask else None,
            "metadata_size_expected": 70 if mask and flags & mask else None,
            "header_raw_hex": raw.hex(),
            "header_fields_validated": not bool(mask and flags & mask),
        }
    except (ValueError, IndexError):
        return {"parse_error": "invalid_or_truncated_header"}


def strings_and_ids(path):
    # Read only first 8 MiB for textual leads; full-file SHA is calculated separately.
    with path.open("rb") as f:
        sample = f.read(8 * 1024 * 1024)
    strings = []
    seen = set()
    for raw in PRINTABLE_RE.findall(sample):
        value = raw.decode("ascii", "replace")
        if value not in seen:
            seen.add(value)
            strings.append(value)
        if len(strings) >= 5000:
            break
    cabs = sorted(set(x.decode("ascii").lower() for x in CAB_RE.findall(sample)))
    archives = sorted(set(x.decode("ascii").lower() for x in ARCHIVE_RE.findall(sample)))
    res_offsets = []
    pos = 0
    while len(res_offsets) < 10000:
        pos = sample.find(b".resS", pos)
        if pos < 0:
            break
        res_offsets.append(pos)
        pos += 4
    # Names likely to be useful as asset clues, but they are not proof of identity.
    names = sorted(set(s for s in strings if (
        "/" in s or "_" in s or s.endswith((".png", ".jpg", ".tga", ".asset", ".prefab"))
    )))
    return {
        "cab_ids": cabs,
        "archive_cab_ids": archives,
        "resS_offsets_sample": res_offsets,
        "strings_sample": strings[:500],
        "asset_name_candidates": names[:1000],
        "sampled_bytes": len(sample),
    }


def scan_root(root, label):
    rows = []
    if not root.is_dir():
        return rows
    for base, dirs, files in os.walk(str(root)):
        dirs.sort()
        for name in sorted(files):
            p = Path(base) / name
            try:
                with p.open("rb") as f:
                    head = f.read(256)
                size = p.stat().st_size
                row = {
                    "platform_root": label,
                    "relative_path": p.relative_to(root).as_posix(),
                    "basename": name,
                    "size": size,
                    "sha256": sha256_file(p),
                    "type": ("UnityFS" if head.startswith(UNITYFS) else
                             "UnityRaw" if head.startswith(b"UnityRaw") else
                             "UnityWeb" if head.startswith(b"UnityWeb") else
                             "other"),
                    "unityfs": parse_unityfs(head),
                }
                if row["type"] == "UnityFS" or name.lower().endswith(ASSET_EXTS):
                    row.update(strings_and_ids(p))
                else:
                    row.update({"cab_ids": [], "archive_cab_ids": [],
                                "resS_offsets_sample": [], "strings_sample": [],
                                "asset_name_candidates": [], "sampled_bytes": 0})
                rows.append(row)
            except OSError as e:
                rows.append({"platform_root": label,
                             "relative_path": p.relative_to(root).as_posix(),
                             "basename": name, "error": str(e)})
    return rows


def add_to_index(index, key, row):
    if key:
        index[key].append(row)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", required=True, help="project root")
    args = ap.parse_args()
    base = Path(args.base).resolve()
    inp = base / "input"
    roots = {
        "android_resources": inp / "android/map_hok_andr/Resources/1.64.1",
        "android_extra": inp / "android/map_hok_andr/Extra",
        "ios_resources": inp / "ios/map_ios_orgi/Resources/1.64.1",
        "ios_extra": inp / "ios/map_ios_orgi/Extra",
        "ios_scene_ref": inp / "hok_sence_ios",
    }
    for required in ("android_resources", "ios_resources", "ios_extra", "ios_scene_ref"):
        if not roots[required].is_dir():
            raise SystemExit("Missing required input root: %s (%s)" % (required, roots[required]))

    inventories = {k: scan_root(v, k) for k, v in roots.items()}
    ios_rows = inventories["ios_resources"] + inventories["ios_extra"] + inventories["ios_scene_ref"]

    by_path, by_name, by_cab = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in ios_rows:
        if "sha256" not in row:
            continue
        add_to_index(by_path, row["relative_path"], row)
        add_to_index(by_name, row["basename"].lower(), row)
        for cab in row.get("cab_ids", []):
            add_to_index(by_cab, cab, row)

    # CAB IDs found in many iOS bundles are usually shared dependencies and
    # are weak evidence for pairing. Only rare IDs (<=3 iOS files) generate
    # cross-name candidates; common IDs remain visible in the report.
    cab_frequency = {cab: len({(r["platform_root"], r["relative_path"])
                               for r in hits})
                     for cab, hits in by_cab.items()}
    RARE_CAB_MAX_FILES = 3

    mapping = []
    for group in ("android_resources", "android_extra"):
        for a in inventories[group]:
            if "sha256" not in a:
                continue
            exact_path = by_path.get(a["relative_path"], [])
            same_name = by_name.get(a["basename"].lower(), [])
            cab_hits = {}
            for cab in a.get("cab_ids", []):
                if cab_frequency.get(cab, 0) <= RARE_CAB_MAX_FILES:
                    for hit in by_cab.get(cab, []):
                        cab_hits[(hit["platform_root"], hit["relative_path"])] = hit
            candidates = {}
            for hit in exact_path + same_name + list(cab_hits.values()):
                key = (hit["platform_root"], hit["relative_path"])
                score = 0
                reasons = []
                if hit["relative_path"] == a["relative_path"]:
                    score += 100
                    reasons.append("same_relative_path")
                if hit["basename"].lower() == a["basename"].lower():
                    score += 40
                    reasons.append("same_basename")
                shared = sorted(set(a.get("cab_ids", [])) & set(hit.get("cab_ids", [])))
                rare_shared = [cab for cab in shared
                               if cab_frequency.get(cab, 0) <= RARE_CAB_MAX_FILES]
                common_shared = [cab for cab in shared if cab not in rare_shared]
                if rare_shared:
                    score += 50 * len(rare_shared)
                    reasons.append("shared_rare_CAB_ID")
                if common_shared:
                    reasons.append("shared_common_CAB_ID")
                if a["sha256"] == hit["sha256"]:
                    score += 1000
                    reasons.append("identical_SHA256")
                candidates[key] = {
                    "platform_root": hit["platform_root"],
                    "relative_path": hit["relative_path"],
                    "size": hit["size"],
                    "sha256": hit["sha256"],
                    "score": score,
                    "reasons": reasons,
                    "shared_cab_ids": shared,
                    "informative_shared_cab_ids": rare_shared,
                    "common_shared_cab_ids": common_shared,
                    "shared_cab_frequency": {cab: cab_frequency.get(cab, 0) for cab in shared},
                }
            ranked = sorted(candidates.values(), key=lambda x: (-x["score"], x["platform_root"], x["relative_path"]))
            best_reasons = set(ranked[0]["reasons"]) if ranked else set()
            strong_cab = any("shared_rare_CAB_ID" in c["reasons"] for c in ranked)
            same_path = any("same_relative_path" in c["reasons"] for c in ranked)
            same_name = any("same_basename" in c["reasons"] for c in ranked)
            if any("identical_SHA256" in c["reasons"] for c in ranked):
                status = "exact_identical"
            elif strong_cab:
                status = "shared_cab_id_needs_validation"
            elif same_path and same_name:
                status = "path_and_name_only_needs_validation"
            elif same_name:
                status = "basename_only_needs_validation"
            elif same_path:
                status = "path_only_needs_validation"
            else:
                status = "no_candidate"

            mapping.append({
                "android_root": group,
                "android_path": a["relative_path"],
                "android_size": a["size"],
                "android_sha256": a["sha256"],
                "android_unityfs": a.get("unityfs"),
                "android_cab_ids": a.get("cab_ids", []),
                "candidate_count": len(ranked),
                "candidates": ranked[:30],
                "status": status,
                "has_shared_cab_id_candidate": strong_cab,
                "android_common_cab_ids": [cab for cab in a.get("cab_ids", [])
                                           if cab_frequency.get(cab, 0) > RARE_CAB_MAX_FILES],
                "has_same_relative_path_candidate": same_path,
                "has_same_basename_candidate": same_name,
            })

    out = base / "output"
    out.mkdir(parents=True, exist_ok=True)
    (out / "batch_inventory.json").write_text(
        json.dumps(inventories, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "batch_mapping.json").write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out / "batch_mapping.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["android_root", "android_path", "android_size", "status",
                    "candidate_count", "best_candidate_root", "best_candidate_path",
                    "best_candidate_score", "best_candidate_reasons", "shared_cab_ids"])
        for row in mapping:
            best = row["candidates"][0] if row["candidates"] else {}
            w.writerow([row["android_root"], row["android_path"], row["android_size"],
                        row["status"], row["candidate_count"],
                        best.get("platform_root", ""), best.get("relative_path", ""),
                        best.get("score", ""), ";".join(best.get("reasons", [])),
                        ";".join(best.get("shared_cab_ids", []))])

    summary = {
        "android_files": sum(len(inventories[k]) for k in ("android_resources", "android_extra")),
        "ios_files": len(ios_rows),
        "android_unityfs": sum(1 for group in ("android_resources", "android_extra")
                               for r in inventories[group] if r.get("type") == "UnityFS"),
        "android_unitycn_flagged": sum(1 for group in ("android_resources", "android_extra")
                                      for r in inventories[group]
                                      if (r.get("unityfs") or {}).get("unitycn_flagged")),
        "exact_identical": sum(1 for r in mapping if r["status"] == "exact_identical"),
        "shared_rare_cab_id_needs_validation": sum(1 for r in mapping if r["status"] == "shared_cab_id_needs_validation"),
        "rare_cab_frequency_max_ios_files": RARE_CAB_MAX_FILES,
        "ios_cab_ids_total": len(cab_frequency),
        "ios_cab_ids_common_over_threshold": sum(1 for n in cab_frequency.values() if n > RARE_CAB_MAX_FILES),
        "path_and_name_only_needs_validation": sum(1 for r in mapping if r["status"] == "path_and_name_only_needs_validation"),
        "basename_only_needs_validation": sum(1 for r in mapping if r["status"] == "basename_only_needs_validation"),
        "path_only_needs_validation": sum(1 for r in mapping if r["status"] == "path_only_needs_validation"),
        "no_candidate": sum(1 for r in mapping if r["status"] == "no_candidate"),
        "staged_or_modified": 0,
        "note": "Read-only index. Cross-name CAB candidates use only IDs found in at most 3 iOS files; common CAB IDs are retained as weak evidence. No candidate proves port compatibility. UnityCN-encrypted bundles are not decrypted or rebuilt."
    }
    (out / "batch_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("Reports:")
    print(out / "batch_inventory.json")
    print(out / "batch_mapping.json")
    print(out / "batch_mapping.csv")
    print(out / "batch_summary.json")


if __name__ == "__main__":
    main()
