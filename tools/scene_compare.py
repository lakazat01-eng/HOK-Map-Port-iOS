#!/usr/bin/env python3
"""Compare HoK Android/iOS scene bundles without modifying source files.

Python 3.9+, standard library only. This is a structural/evidence report,
not a UnityCN decryptor and not a port converter.
"""
import argparse
import hashlib
import json
import os
import re
from collections import defaultdict
from pathlib import Path

UNITYFS = b"UnityFS\0"
CAB_RE = re.compile(rb"CAB-([0-9a-fA-F]{32})")
ARCHIVE_RE = re.compile(rb"archive:/CAB-([0-9a-fA-F]{32})")
STRING_RE = re.compile(rb"[ -~]{6,}")
CHUNK = 1024 * 1024
MAX_STRINGS = 12000


def cstring(data, pos):
    end = data.find(b"\0", pos)
    if end < 0:
        raise ValueError("unterminated UnityFS string")
    return data[pos:end].decode("ascii", "replace"), end + 1


def header_info(path):
    with path.open("rb") as f:
        head = f.read(512)
    if not head.startswith(UNITYFS):
        return {"type": "non_UnityFS", "header_hex": head[:64].hex()}
    try:
        pos = 8
        fmt = int.from_bytes(head[pos:pos + 4], "big")
        pos += 4
        unity_version, pos = cstring(head, pos)
        revision, pos = cstring(head, pos)
        raw = head[pos:pos + 20]
        if len(raw) != 20:
            raise ValueError("truncated fixed header")
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
        encrypted = bool(mask and flags & mask)
        meta_offset = pos + 20 if encrypted else None
        meta = head[meta_offset:meta_offset + 70] if encrypted else b""
        return {
            "type": "UnityFS",
            "format_version": fmt,
            "unity_version": unity_version,
            "unity_revision": revision,
            "fixed_header_offset": pos,
            "fixed_header_hex": raw.hex(),
            "flags": flags,
            "flags_hex": "0x%X" % flags,
            "compression_id": flags & 0x3f,
            "compression": {0:"none", 1:"LZMA", 2:"LZ4", 3:"LZ4HC", 4:"LZHAM"}.get(flags & 0x3f, "unknown"),
            "unitycn_flag_mask": ("0x%X" % mask) if mask else None,
            "unitycn_flagged": encrypted,
            "unitycn_metadata_offset": meta_offset,
            "unitycn_metadata_size": len(meta) if encrypted else 0,
            "unitycn_metadata_sha256": hashlib.sha256(meta).hexdigest() if meta else None,
            "unitycn_metadata_hex": meta.hex() if meta else None,
            "header_layout_validated": False,
        }
    except (ValueError, IndexError) as exc:
        return {"type": "UnityFS", "parse_error": str(exc)}


def scan_evidence(path):
    sha = hashlib.sha256()
    cabs, archives, strings = set(), set(), set()
    carry = b""
    total = 0
    with path.open("rb") as f:
        while True:
            block = f.read(CHUNK)
            if not block:
                break
            sha.update(block)
            total += len(block)
            data = carry + block
            for x in CAB_RE.findall(data):
                cabs.add(x.decode("ascii").lower())
            for x in ARCHIVE_RE.findall(data):
                archives.add(x.decode("ascii").lower())
            for raw in STRING_RE.findall(data):
                if len(strings) < MAX_STRINGS:
                    value = raw.decode("ascii", "replace")
                    if any(ch.isalpha() for ch in value):
                        strings.add(value)
            carry = data[-512:]
    # Keep asset-like strings only; common noise is discarded.
    clues = sorted(s for s in strings if (
        "/" in s or "_" in s or "." in s or
        any(k in s.lower() for k in ("scene", "map", "terrain", "light", "shader"))
    ))
    return {
        "size": total,
        "sha256": sha.hexdigest(),
        "cab_ids": sorted(cabs),
        "archive_cab_ids": sorted(archives),
        "string_count_capped": len(strings),
        "asset_string_clues": clues[:2500],
    }


def walk_scenes(root, label):
    rows = []
    if not root.is_dir():
        return rows
    for base, dirs, files in os.walk(str(root)):
        dirs.sort()
        for name in sorted(files):
            low = name.lower()
            if not (low.endswith((".assetbundle", ".bundle")) and "scene" in low):
                continue
            p = Path(base) / name
            try:
                h = header_info(p)
                ev = scan_evidence(p)
                rows.append({
                    "source": label,
                    "relative_path": p.relative_to(root).as_posix(),
                    "basename": name,
                    "header": h,
                    **ev,
                })
            except OSError as exc:
                rows.append({"source": label, "relative_path": p.relative_to(root).as_posix(),
                             "basename": name, "error": str(exc)})
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", required=True)
    args = ap.parse_args()
    base = Path(args.base).resolve()
    inp = base / "input"
    roots = {
        "android": inp / "android/map_hok_andr/Resources/1.64.1/assetbundle/scene",
        "ios_resources": inp / "ios/map_ios_orgi/Resources/1.64.1/assetbundle/scene",
        "ios_extra": inp / "ios/map_ios_orgi/Extra/assetbundle/scene",
        "ios_scene_ref": inp / "hok_sence_ios/scene",
    }
    for key in ("android", "ios_resources", "ios_extra", "ios_scene_ref"):
        if not roots[key].is_dir():
            # Search recursively in the broader supplied root if the expected
            # scene subfolder is absent.
            if key == "ios_scene_ref":
                roots[key] = inp / "hok_sence_ios"
            if not roots[key].is_dir():
                raise SystemExit("Missing scene input: %s (%s)" % (key, roots[key]))

    inventories = {k: walk_scenes(v, k) for k, v in roots.items()}
    ios = inventories["ios_resources"] + inventories["ios_extra"] + inventories["ios_scene_ref"]
    ios_by_name = defaultdict(list)
    for row in ios:
        if row.get("header", {}).get("type") == "UnityFS" and row.get("size", 0) > 16:
            ios_by_name[row["basename"].lower()].append(row)

    comparisons = []
    for a in inventories["android"]:
        candidates = ios_by_name.get(a["basename"].lower(), [])
        for b in candidates:
            aset, bset = set(a.get("asset_string_clues", [])), set(b.get("asset_string_clues", []))
            ac, bc = set(a.get("cab_ids", [])), set(b.get("cab_ids", []))
            comparisons.append({
                "android_path": a["relative_path"],
                "ios_source": b["source"],
                "ios_path": b["relative_path"],
                "android_size": a.get("size"),
                "ios_size": b.get("size"),
                "size_ratio_ios_to_android": round(b["size"] / a["size"], 5) if a.get("size") else None,
                "same_sha256": a.get("sha256") == b.get("sha256"),
                "same_unity_revision": a.get("header", {}).get("unity_revision") == b.get("header", {}).get("unity_revision"),
                "same_flags": a.get("header", {}).get("flags") == b.get("header", {}).get("flags"),
                "shared_cab_ids": sorted(ac & bc),
                "shared_archive_cab_ids": sorted(set(a.get("archive_cab_ids", [])) & set(b.get("archive_cab_ids", []))),
                "shared_asset_string_clues": sorted(aset & bset)[:500],
                "android_only_asset_string_clues": sorted(aset - bset)[:500],
                "ios_only_asset_string_clues": sorted(bset - aset)[:500],
                "evidence_note": "String/CAB overlap is a lead only. UnityCN encrypted data may hide object/dependency structure; no compatibility conclusion is made."
            })

    report = {
        "tool": "HOK Scene Structural Comparator",
        "version": 1,
        "read_only": True,
        "roots": {k: str(v) for k, v in roots.items()},
        "counts": {k: len(v) for k, v in inventories.items()},
        "comparisons": comparisons,
        "inventories": inventories,
        "limitations": [
            "Does not decrypt UnityCN or reconstruct Unity serialized files.",
            "Readable strings and CAB identifiers are clues, not proof of matching objects.",
            "Object-level dependency comparison requires valid UnityCN decoding and a Unity serialized-file parser."
        ]
    }
    out = base / "output"
    out.mkdir(parents=True, exist_ok=True)
    target = out / "scene_compare.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"scene_counts": report["counts"],
                      "same_name_comparisons": len(comparisons),
                      "report": str(target)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
