#!/usr/bin/env python3
"""Read-only UABE_AOV compatibility probe for Android minion/UI bundles.

Only metadata and texture fingerprints are written to the report. No game assets
or rebuilt bundles are exported.
"""
import collections
import hashlib
import json
import pathlib
import re
import struct
import sys

import UnityPy_AOV

ROOT = pathlib.Path("/tmp/map-port-input")
OUT = pathlib.Path("uabe-aov-probe-report")
OUT.mkdir(parents=True, exist_ok=True)

CANDIDATE = re.compile(
    r"(allshared|battlecommon_raw|ugui_skill_raw|charbattle_soldier)",
    re.IGNORECASE,
)

def raw_bundle_header(path):
    data = path.read_bytes()
    pos = data.find(bytes([0]))
    if pos < 0:
        return {"header_error": "missing signature terminator"}
    signature = data[:pos].decode("ascii", "replace")
    cursor = pos + 1
    if cursor + 4 > len(data):
        return {"signature": signature, "header_error": "truncated format"}
    fmt = struct.unpack_from(">I", data, cursor)[0]
    cursor += 4
    strings = []
    for _ in range(2):
        end = data.find(bytes([0]), cursor)
        if end < 0:
            return {"signature": signature, "format": fmt, "header_error": "truncated version string"}
        strings.append(data[cursor:end].decode("ascii", "replace"))
        cursor = end + 1
    raw = data[cursor:cursor + 20]
    result = {"signature": signature, "format": fmt, "player_version": strings[0], "engine_version": strings[1], "header_offset": cursor, "header_hex": raw.hex()}
    if len(raw) >= 20:
        result["raw_size_fields_hex"] = raw[:16].hex()
        result["raw_data_flags"] = struct.unpack_from(">I", raw, 16)[0]
        result["raw_data_flags_hex"] = hex(result["raw_data_flags"])
    else:
        result["header_error"] = "truncated UnityFS header"
    return result

def texture_fingerprint(image):
    rgba = image.convert("RGBA")
    digest = hashlib.sha256(rgba.tobytes()).hexdigest()
    return digest, list(rgba.size)

report = {
    "tool": "UnityPy_AOV",
    "tool_version": getattr(UnityPy_AOV, "__version__", "unknown"),
    "mode": "read-only; no game assets exported or uploaded; no source files modified",
    "platforms": {},
    "matches": [],
}

indexes = {"android": collections.defaultdict(list), "ios": collections.defaultdict(list)}

for platform in ("android", "ios"):
    platform_root = ROOT / platform
    platform_report = {"candidate_bundles": [], "parsed": 0, "failed": 0}
    if not platform_root.exists():
        report["platforms"][platform] = platform_report
        continue

    paths = sorted(
        p for p in platform_root.rglob("*.assetbundle")
        if p.is_file() and CANDIDATE.search(p.name)
    )

    for path in paths:
        rel = path.relative_to(platform_root).as_posix()
        row = {
            "path": rel,
            "size": path.stat().st_size,
            "raw_header": raw_bundle_header(path),
            "load": "started",
            "object_count": 0,
            "object_types": {},
            "textures": [],
            "named_objects_sample": [],
        }
        try:
            env = UnityPy_AOV.load(str(path))
            root_file = getattr(env, "file", None)
            row["root_file_type"] = type(root_file).__name__ if root_file is not None else None
            row["root_nested_file_count"] = len(getattr(root_file, "files", {}) or {})
            objects = list(env.objects)
            counts = collections.Counter(obj.type.name for obj in objects)
            row["load"] = "success" if objects else "no_objects"
            row["object_count"] = len(objects)
            row["object_types"] = dict(sorted(counts.items()))
            if objects:
                platform_report["parsed"] += 1
            else:
                platform_report["no_objects"] = platform_report.get("no_objects", 0) + 1

            for obj in objects:
                try:
                    data = obj.read()
                    name = str(getattr(data, "m_Name", "") or "")
                    if name and len(row["named_objects_sample"]) < 250:
                        row["named_objects_sample"].append({
                            "type": obj.type.name,
                            "name": name[:180],
                            "path_id": str(obj.path_id),
                        })
                    if obj.type.name != "Texture2D":
                        continue
                    image = data.image
                    if image is None:
                        continue
                    fingerprint, dimensions = texture_fingerprint(image)
                    texture = {
                        "bundle": path.name,
                        "bundle_path": rel,
                        "name": name or "Unnamed",
                        "path_id": str(obj.path_id),
                        "width": dimensions[0],
                        "height": dimensions[1],
                        "rgba_sha256": fingerprint,
                    }
                    row["textures"].append(texture)
                    key = (path.name.lower(), texture["name"], dimensions[0], dimensions[1])
                    indexes[platform][key].append(texture)
                except Exception as exc:
                    if len(row.setdefault("object_read_errors", [])) < 20:
                        row["object_read_errors"].append({
                            "type": obj.type.name,
                            "error": type(exc).__name__ + ": " + str(exc)[:180],
                        })

        except Exception as exc:
            row["load"] = "failed"
            row["error_type"] = type(exc).__name__
            row["error"] = str(exc)[:1400]
            platform_report["failed"] += 1

        platform_report["candidate_bundles"].append(row)
        print("UABE_AOV_BUNDLE", json.dumps({
            "platform": platform,
            "path": rel,
            "load": row["load"],
            "root_file_type": row.get("root_file_type"),
            "object_count": row["object_count"],
            "texture_count": len(row["textures"]),
            "raw_header": row["raw_header"],
            "error": row.get("error"),
        }, ensure_ascii=False))

    report["platforms"][platform] = platform_report

for key, android_items in indexes["android"].items():
    ios_items = indexes["ios"].get(key, [])
    if not ios_items:
        continue
    for source in android_items:
        for target in ios_items:
            report["matches"].append({
                "bundle": source["bundle"],
                "texture_name": source["name"],
                "dimensions": [source["width"], source["height"]],
                "android_path_id": source["path_id"],
                "ios_path_id": target["path_id"],
                "same_pixels": source["rgba_sha256"] == target["rgba_sha256"],
                "status": (
                    "identical_texture"
                    if source["rgba_sha256"] == target["rgba_sha256"]
                    else "texture_replacement_candidate"
                ),
            })

report["summary"] = {
    "android_bundles_found": len(report["platforms"]["android"]["candidate_bundles"]),
    "ios_bundles_found": len(report["platforms"]["ios"]["candidate_bundles"]),
    "android_bundles_parsed": report["platforms"]["android"]["parsed"],
    "ios_bundles_parsed": report["platforms"]["ios"]["parsed"],
    "android_bundles_failed": report["platforms"]["android"]["failed"],
    "ios_bundles_failed": report["platforms"]["ios"]["failed"],
    "android_bundles_no_objects": report["platforms"]["android"].get("no_objects", 0),
    "ios_bundles_no_objects": report["platforms"]["ios"].get("no_objects", 0),
    "matched_texture_pairs": len(report["matches"]),
    "different_texture_pairs": sum(
        item["status"] == "texture_replacement_candidate"
        for item in report["matches"]
    ),
}
(OUT / "report.json").write_text(
    json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
)
print("UABE_AOV_SUMMARY", json.dumps(report["summary"], ensure_ascii=False))

if report["summary"]["android_bundles_found"] == 0:
    print("WARNING: no Android candidate bundles found", file=sys.stderr)
