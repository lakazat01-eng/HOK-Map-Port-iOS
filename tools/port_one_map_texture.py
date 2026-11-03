#!/usr/bin/env python3
"""Create one isolated Android-to-iOS map texture trial.

Only replaces a streamed Texture2D payload when both extracted records have
identical dimensions, Unity format, and byte length. Source bundles are untouched.
"""
import argparse
import hashlib
import json
import os
import re
import struct


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def get_entry(report, platform, name):
    matches = [x for x in report[platform] if x["name"] == name]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one {platform} record for {name}")
    return matches[0]


def stream_base(bundle):
    for match in re.finditer(rb"20\d\d\.\d+\.\d+f1\x00", bundle[:0x4000]):
        start = match.start() - 0x30
        if start < 0 or len(bundle) < start + 0x30:
            continue
        header = struct.unpack(">IIIIB3xIqqq", bundle[start:start + 0x30])
        if header[2] != 22:
            continue
        serialized_size = header[6]
        if serialized_size > 0 and start + serialized_size <= len(bundle):
            return start + ((serialized_size + 15) // 16 * 16)
    raise ValueError("Could not locate supported plaintext SerializedFile v22")


def read_stream(bundle, entry):
    start = stream_base(bundle) + entry["stream_offset"]
    end = start + entry["stream_size"]
    if start < 0 or end > len(bundle):
        raise ValueError("Texture stream range is outside bundle")
    return bundle[start:end], start, end


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
        raise SystemExit("REFUSED: incompatible metadata: " + ", ".join(
            f"{k}: Android={src[k]} iOS={dst[k]}" for k in mismatches
        ))

    with open(args.android_bundle, "rb") as f:
        android = f.read()
    source_stream, _, _ = read_stream(android, src)
    if sha256(source_stream) != src["sha256"]:
        raise SystemExit("REFUSED: Android bundle differs from extraction metadata")

    with open(args.ios_bundle, "rb") as f:
        original = f.read()
    current, start, end = read_stream(original, dst)
    if sha256(current) != dst["sha256"]:
        raise SystemExit("REFUSED: iOS bundle differs from extraction metadata")

    patched = bytearray(original)
    patched[start:end] = source_stream

    os.makedirs(args.out, exist_ok=True)
    out_path = os.path.join(args.out, f"ios_with_android_{args.texture}.assetbundle")
    if os.path.exists(out_path):
        raise SystemExit(f"REFUSED: output already exists: {out_path}")

    with open(out_path, "xb") as f:
        f.write(patched)
    with open(out_path, "rb") as f:
        check = f.read()

    if check[:start] != original[:start] or check[end:] != original[end:]:
        os.remove(out_path)
        raise SystemExit("FAIL: bytes outside selected texture changed")
    if sha256(check[start:end]) != src["sha256"]:
        os.remove(out_path)
        raise SystemExit("FAIL: candidate texture hash mismatch")

    print("TRIAL CREATED (not yet validated in-game)")
    print("texture:", args.texture)
    print("format:", src["format"], "dimensions:", f'{src["width"]}x{src["height"]}')
    print("stream bytes:", src["stream_size"])
    print("Android SHA256:", src["sha256"])
    print("iOS before SHA256:", dst["sha256"])
    print("candidate:", out_path)
    print("changed bytes:", sum(a != b for a, b in zip(original, check)))


if __name__ == "__main__":
    main()
