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
import sys

import UnityPy_AOV

ROOT = pathlib.Path("/tmp/map-port-input")
OUT = pathlib.Path("uabe-aov-probe-report")
OUT.mkdir(parents=True, exist_ok=True)

CANDIDATE = re.compile(
    r"(allshared|battlecommon_raw|ugui_skill_raw|charbattle_soldier)",
    re.IGNORECASE,
)

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
            "load": "started",
            "object_count": 0,
            "object_types": {},
            "textures": [],
            "named_objects_sample": [],
        }
        try:
            env = UnityPy_AOV.load(str(path))
            objects = list(env.objects)
            counts = collections.Counter(obj.type.name for obj in objects)
            row["load"] = "success"
            row["object_count"] = len(objects)
            row["object_types"] = dict(sorted(counts.items()))
            platform_report["parsed"] += 1

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
            "object_count": row["object_count"],
            "texture_count": len(row["textures"]),
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
