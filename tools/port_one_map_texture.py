#!/usr/bin/env python3
"""Create one isolated Android-to-iOS map texture trial.

Only replaces a streamed Texture2D payload when both extracted records have
identical dimensions, Unity format, and byte length. The source bundles and
the iOS baseline are never modified.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def get_entry(report, platform, name):
    matches = [x for x in report[platform] if x["name"] == name]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one {platform} record for {name}")
    return matches[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--metadata", default="output/map5_textures/metadata.json")
    ap.add_argument("--android-bundle", required=True)
    ap.add_argument("--ios-bundle", required=True)
    ap.add_argument("--texture", required=True)
    ap.add_argument("--out", default="output/map5_texture_trials")
    args = ap.parse_args()

    with open(args.metadata, "r", encoding="utf-8") as f:
        report = json.load(f)

    src = get_entry(report, "android", args.texture)
    dst = get_entry(report, "ios", args.texture)

    fields = ("width", "height", "format", "stream_size")
    mismatches = [k for k in fields if src[k] != dst[k]]
    if mismatches:
        raise SystemExit(
            "REFUSED: incompatible texture metadata: " + ", ".join(
                f"{k} android={src[k]} ios={dst[k]}" for k in mismatches
            )
        )

    with open(src["file"], "rb") as f:
        payload = f.read()
    if len(payload) != src["stream_size"]:
        raise SystemExit("REFUSED: extracted Android payload size mismatch")
    if sha256(payload) != src["sha256"]:
        raise SystemExit("REFUSED: Android payload hash mismatch")

    with open(args.ios_bundle, "rb") as f:
        original = f.read()

    # The extraction report stores the absolute stream offset in the bundle.
    start = dst["stream_offset"]
    end = start + dst["stream_size"]
    if start < 0 or end > len(original):
        raise SystemExit("REFUSED: iOS stream range is outside the bundle")

    # Make sure the current iOS range still matches the extracted baseline.
    current = original[start:end]
    if sha256(current) != dst["sha256"]:
        raise SystemExit("REFUSED: iOS bundle does not match extraction metadata")

    patched = bytearray(original)
    patched[start:end] = payload

    os.makedirs(args.out, exist_ok=True)
    out_path = os.path.join(args.out, f"ios_with_android_{args.texture}.assetbundle")
    if os.path.exists(out_path):
        raise SystemExit(f"REFUSED: output already exists; remove it deliberately first: {out_path}")

    with open(out_path, "xb") as f:
        f.write(patched)

    # Re-read the candidate and prove only the selected stream range changed.
    with open(out_path, "rb") as f:
        check = f.read()
    if check[:start] != original[:start] or check[end:] != original[end:]:
        os.remove(out_path)
        raise SystemExit("FAIL: bytes outside the selected texture changed")
    if sha256(check[start:end]) != src["sha256"]:
        os.remove(out_path)
        raise SystemExit("FAIL: candidate texture hash mismatch")

    print("TRIAL CREATED (not yet validated in-game)")
    print("texture:", args.texture)
    print("format:", src["format"], "size:", src["width"], "x", src["height"])
    print("stream bytes:", src["stream_size"])
    print("Android SHA256:", src["sha256"])
    print("iOS before SHA256:", dst["sha256"])
    print("candidate:", out_path)
    print("bundle bytes changed:", sum(a != b for a, b in zip(original, check)))


if __name__ == "__main__":
    main()
