#!/usr/bin/env python3
"""Inventory extracted Android/iOS map-resource directories (Python 3.9+).

Read-only: never modifies source files. Writes a JSON report to the chosen path.
"""
import argparse
import hashlib
import json
import os
import struct
from pathlib import Path

CHUNK = 1024 * 1024

def sha256_file(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(CHUNK)
            if not block:
                break
            h.update(block)
    return h.hexdigest()

def signature(path):
    try:
        with path.open("rb") as f:
            head = f.read(32)
    except OSError:
        return "UNREADABLE"
    if head.startswith(b"UnityFS"):
        return "UnityFS"
    if head.startswith(b"UnityRaw"):
        return "UnityRaw"
    if head.startswith(b"UnityWeb"):
        return "UnityWeb"
    if head.startswith(b"PK\x03\x04"):
        return "ZIP"
    if head.startswith(b"\x7fELF"):
        return "ELF"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if head.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if head.startswith(b"OggS"):
        return "OGG"
    if head.startswith(b"BKHD"):
        return "Wwise-BNK"
    if head.startswith(b"RIFF"):
        return "RIFF"
    if head[:4] == b"\x22\x4a\x67\x00":
        return "Possible-Zstd-custom-header"
    return head[:8].hex(" ") if head else "EMPTY"

def inventory(root):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError("Not a directory: {}".format(root))
    rows = []
    for base, dirs, files in os.walk(str(root)):
        dirs.sort()
        files.sort()
        for name in files:
            p = Path(base) / name
            rel = p.relative_to(root).as_posix()
            try:
                st = p.stat()
                rows.append({
                    "path": rel,
                    "size": st.st_size,
                    "sha256": sha256_file(p),
                    "signature": signature(p),
                })
            except OSError as exc:
                rows.append({"path": rel, "error": str(exc)})
    return rows

def main():
    ap = argparse.ArgumentParser(description="Read-only Android/iOS asset inventory")
    ap.add_argument("--android", required=True, help="Extracted Android map directory")
    ap.add_argument("--ios", required=True, help="Extracted iOS map directory")
    ap.add_argument("--out", default="map_inventory.json", help="Output JSON report")
    args = ap.parse_args()

    android = inventory(args.android)
    ios = inventory(args.ios)
    a_by_path = {x["path"]: x for x in android}
    i_by_path = {x["path"]: x for x in ios}
    common = sorted(set(a_by_path) & set(i_by_path))
    report = {
        "tool": "HOK-Map-Port-iOS inventory",
        "version": 1,
        "android_root": str(Path(args.android).resolve()),
        "ios_root": str(Path(args.ios).resolve()),
        "summary": {
            "android_files": len(android),
            "ios_files": len(ios),
            "same_path": len(common),
            "same_path_same_hash": sum(
                1 for p in common
                if a_by_path[p].get("sha256") == i_by_path[p].get("sha256")
            ),
            "same_path_different_hash": sum(
                1 for p in common
                if a_by_path[p].get("sha256") != i_by_path[p].get("sha256")
            ),
            "android_only": len(set(a_by_path) - set(i_by_path)),
            "ios_only": len(set(i_by_path) - set(a_by_path)),
        },
        "android": android,
        "ios": ios,
        "comparison": [
            {
                "path": p,
                "android_size": a_by_path[p].get("size"),
                "ios_size": i_by_path[p].get("size"),
                "android_signature": a_by_path[p].get("signature"),
                "ios_signature": i_by_path[p].get("signature"),
                "same_hash": a_by_path[p].get("sha256") == i_by_path[p].get("sha256"),
            }
            for p in common
        ],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print("Report:", out.resolve())

if __name__ == "__main__":
    main()
