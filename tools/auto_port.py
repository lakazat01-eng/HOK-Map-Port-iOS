#!/usr/bin/env python3
"""HOK map batch analyzer / conservative auto-port staging tool.

This first stage inventories all inputs and stages only byte-identical Android
files when a matching iOS relative path exists. It never overwrites originals
or guesses that same-name Unity bundles are compatible.
Python 3.9+, standard library only.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
from pathlib import Path

CHUNK = 1024 * 1024
UNITYFS = b"UnityFS\0"
MARKER = b"ZMODSKIN"


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def cstring(data, pos):
    end = data.find(b"\\0", pos)
    if end < 0:
        raise ValueError("unterminated UnityFS version string")
    return data[pos:end].decode("ascii", "replace"), end + 1


def unityfs_header(data):
    """Read the visible UnityFS header and report UnityCN indicators.

    UnityCN-protected bundles can contain transformed header fields. Keep the
    raw values for diagnostics; do not treat them as validated sizes.
    """
    if not data.startswith(UNITYFS):
        return None
    try:
        pos = 8
        format_version = int.from_bytes(data[pos:pos + 4], "big")
        pos += 4
        unity_version, pos = cstring(data, pos)
        unity_revision, pos = cstring(data, pos)
        start = pos
        raw = data[start:start + 20]
        if len(raw) != 20:
            raise ValueError("truncated UnityFS header")
        raw_size = int.from_bytes(raw[0:8], "big")
        raw_compressed_info = int.from_bytes(raw[8:12], "big")
        raw_uncompressed_info = int.from_bytes(raw[12:16], "big")
        flags = int.from_bytes(raw[16:20], "big")

        match = re.match(r"^(\\d+)\\.(\\d+)\\.(\\d+)", unity_revision)
        mask = None
        if match:
            major, minor, patch = map(int, match.groups())
            old_layout = (
                major < 2020 or
                (major == 2020 and minor == 3 and patch <= 34) or
                (major == 2021 and minor == 3 and patch <= 2) or
                (major == 2022 and minor == 3 and patch <= 1)
            )
            mask = 0x200 if old_layout else 0x400

        encrypted = bool(mask is not None and flags & mask)
        envelope_offset = start + 20
        envelope_size = 70 if encrypted else 0
        aligned_after_envelope = (
            (envelope_offset + envelope_size + 15) & ~15
        )
        compression = flags & 0x3F
        compression_names = {
            0: "none", 1: "LZMA", 2: "LZ4", 3: "LZ4HC",
            4: "LZHAM"
        }
        return {
            "format_version": format_version,
            "unity_version": unity_version,
            "unity_revision": unity_revision,
            "header_offset": start,
            "header_raw_hex": raw.hex(),
            "raw_size_field": raw_size,
            "raw_compressed_info_size": raw_compressed_info,
            "raw_uncompressed_info_size": raw_uncompressed_info,
            "flags": flags,
            "flags_hex": "0x{:X}".format(flags),
            "compression_id": compression,
            "compression": compression_names.get(
                compression, "unknown"
            ),
            "blocks_and_directory_combined": bool(flags & 0x40),
            "blocks_info_at_end": bool(flags & 0x80),
            "block_info_padding_flag": bool(flags & 0x200),
            "unitycn_flag_mask": (
                "0x{:X}".format(mask) if mask is not None else None
            ),
            "unitycn_detected": encrypted,
            "unitycn_envelope_offset": (
                envelope_offset if encrypted else None
            ),
            "unitycn_envelope_size": envelope_size,
            "aligned_offset_after_unitycn_envelope": (
                aligned_after_envelope if encrypted else None
            ),
            "size_fields_validated": False if encrypted else None,
            "note": (
                "UnityCN indicator found; encrypted fields are diagnostic "
                "only. AES key is required for decryption."
                if encrypted else
                "Header fields have not yet been validated against file size."
            ),
        }
    except (IndexError, ValueError, OverflowError) as exc:
        return {"error": str(exc)}


def inspect(path, root):
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            head = f.read(160)
        if head.startswith(UNITYFS):
            kind = "UnityFS"
        elif head.startswith(MARKER):
            kind = "ZMODSKIN-marker"
        elif head.startswith(b"UnityRaw"):
            kind = "UnityRaw"
        elif head.startswith(b"UnityWeb"):
            kind = "UnityWeb"
        else:
            kind = "unknown"
        return {
            "path": path.relative_to(root).as_posix(),
            "size": size,
            "sha256": sha256(path),
            "type": kind,
            "header_hex": head[:48].hex(),
            "unityfs_header": unityfs_header(head),
        }
    except OSError as exc:
        return {"path": path.relative_to(root).as_posix(), "error": str(exc)}


def scan(root):
    root = Path(root).resolve()
    rows = []
    if not root.is_dir():
        return rows
    for base, dirs, files in os.walk(str(root)):
        dirs.sort()
        for name in sorted(files):
            rows.append(inspect(Path(base) / name, root))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", required=True, help="hok_map_port project root")
    ap.add_argument("--stage", action="store_true",
                    help="stage only exact byte-identical path matches")
    args = ap.parse_args()

    base = Path(args.base).resolve()
    inp = base / "input"
    roots = {
        "android": inp / "android" / "map_hok_andr" / "Resources" / "1.64.1",
        "ios_res": inp / "ios" / "map_ios_orgi" / "Resources" / "1.64.1",
        "ios_extra": inp / "ios" / "map_ios_orgi" / "Extra",
        "ios_scene_ref": inp / "hok_sence_ios",
    }
    for name, root in roots.items():
        if not root.is_dir():
            raise SystemExit("Missing input root: {} ({})".format(name, root))

    data = {name: scan(root) for name, root in roots.items()}
    indexes = {}
    for name in ("ios_res", "ios_extra"):
        indexes[name] = {row["path"]: row for row in data[name] if "sha256" in row}

    staged, identical, conflicts, android_only = [], [], [], []
    output = base / "output" / "ios_candidate"
    for row in data["android"]:
        rel = row["path"]
        if "sha256" not in row:
            conflicts.append({"path": rel, "status": "unreadable"})
            continue
        matches = []
        for source in ("ios_res", "ios_extra"):
            candidate = indexes[source].get(rel)
            if candidate:
                matches.append((source, candidate))
        exact = [(source, item) for source, item in matches
                 if item.get("sha256") == row["sha256"]]
        if exact:
            identical.append({"path": rel, "matched_in": exact[0][0],
                              "status": "identical"})
            if args.stage:
                dst = output / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2((roots["android"] / rel), dst)
                staged.append(rel)
        elif matches:
            conflicts.append({
                "path": rel,
                "status": "same_path_different_content",
                "android_size": row["size"],
                "ios_matches": [
                    {"root": source, "size": item["size"],
                     "sha256": item["sha256"]}
                    for source, item in matches
                ],
            })
        else:
            # Same basename elsewhere is recorded as a lead, never treated as a match.
            basename_hits = []
            for source in ("ios_res", "ios_extra", "ios_scene_ref"):
                for item in data[source]:
                    if item.get("path", "").split("/")[-1] == rel.split("/")[-1]:
                        basename_hits.append({"root": source, "path": item["path"],
                                              "size": item.get("size"),
                                              "sha256": item.get("sha256")})
            android_only.append({"path": rel, "size": row["size"],
                                 "type": row["type"],
                                 "same_name_candidates": basename_hits})

    report = {
        "tool": "HOK Map Port iOS auto-port analyzer",
        "version": 1,
        "mode": "stage-identical-only" if args.stage else "analyze-only",
        "summary": {
            "android_files": len(data["android"]),
            "ios_res_files": len(data["ios_res"]),
            "ios_extra_files": len(data["ios_extra"]),
            "reference_files": len(data["ios_scene_ref"]),
            "identical_path_and_hash": len(identical),
            "different_content_same_path": len(conflicts),
            "android_only_needing_analysis": len(android_only),
            "staged": len(staged),
        },
        "identical": identical,
        "conflicts": conflicts,
        "android_only": android_only,
        "inventories": data,
    }
    report_dir = base / "output"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "auto_port_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print("Report:", report_path)
    if args.stage:
        print("Candidate output:", output)


if __name__ == "__main__":
    main()
