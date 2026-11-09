#!/usr/bin/env python3
"""Add the four supplied Billow ultimate WEMs to the missing Play_Billow_S_Skill_C event.

Input: source.zip containing the original Billow skin ZIP and zstd_dict.bin.
Output: patched Hero_Billow_Skin_52807_SFX.bnk plus structural validation report.
Only HIRC is changed. Existing C1/C2, hit and VO event chains are preserved.
"""
from __future__ import annotations
import argparse, hashlib, io, json, struct, subprocess, tempfile, zipfile
from pathlib import Path

TARGET_WEMS = [3284298916, 3418519838, 3452075108, 1693947608]
PRESERVE_WEM = 3468852688
EVENT_NAME = "Play_Billow_S_Skill_C"


def u32(b, at): return struct.unpack_from("<I", b, at)[0]
def p32(v): return struct.pack("<I", v)


def fnv1_lower(s: str) -> int:
    h = 2166136261
    for x in s.lower().encode("utf-8"):
        h = ((h * 16777619) & 0xffffffff) ^ x
    return h


def parse_bnk(data: bytes):
    sections = {}
    pos = 0
    while pos < len(data):
        if pos + 8 > len(data):
            raise ValueError("Truncated BNK section header")
        tag = data[pos:pos + 4]
        size = u32(data, pos + 4)
        end = pos + 8 + size
        if end > len(data):
            raise ValueError(f"Truncated {tag!r} section")
        sections[tag] = (pos, size, data[pos + 8:end])
        pos = end
    if pos != len(data) or b"HIRC" not in sections or b"DIDX" not in sections:
        raise ValueError("Invalid BNK section layout")
    hpos, hsize, hbody = sections[b"HIRC"]
    count = u32(hbody, 0)
    p = 4
    objects = []
    for _ in range(count):
        if p + 5 > len(hbody):
            raise ValueError("Truncated HIRC object header")
        typ = hbody[p]
        size = u32(hbody, p + 1)
        raw = hbody[p + 5:p + 5 + size]
        if len(raw) != size or size < 4:
            raise ValueError("Truncated HIRC object")
        oid = u32(raw, 0)
        objects.append((typ, oid, raw))
        p += 5 + size
    if p != len(hbody):
        raise ValueError("HIRC object count/size mismatch")
    dpos, dsize, dbody = sections[b"DIDX"]
    if dsize % 12:
        raise ValueError("Invalid DIDX length")
    media = {}
    for i in range(0, dsize, 12):
        mid, off, n = struct.unpack_from("<III", dbody, i)
        media[mid] = (off, n)
    return sections, objects, media


def parse_container(raw: bytes):
    # AkChildren: u32 count + child IDs; playlist: u16 count + {PlayId,u32 weight}.
    candidates = []
    for at in range(4, len(raw) - 6):
        n = u32(raw, at)
        if n < 1 or n > 64:
            continue
        end = at + 4 + 4 * n
        if end + 2 > len(raw):
            continue
        pc = struct.unpack_from("<H", raw, end)[0]
        if end + 2 + 8 * pc != len(raw):
            continue
        children = [u32(raw, at + 4 + 4 * i) for i in range(n)]
        playlist = []
        for j in range(pc):
            play_id, weight = struct.unpack_from("<Ii", raw, end + 2 + 8 * j)
            playlist.append((play_id, weight))
        if pc and all(x[0] in children for x in playlist):
            candidates.append((at, children, end, playlist))
    if len(candidates) != 1:
        raise ValueError(f"Cannot uniquely parse container children/playlist: {candidates}")
    return candidates[0]


def extract_inputs(source_zip: Path, work: Path):
    with zipfile.ZipFile(source_zip) as outer:
        dict_names = [n for n in outer.namelist() if n.endswith("zstd_dict.bin")]
        nested_names = [n for n in outer.namelist() if n.lower().endswith(".zip")]
        if len(dict_names) != 1:
            raise ValueError(f"Expected one zstd_dict.bin, found {len(dict_names)}")
        dictionary = work / "zstd_dict.bin"
        dictionary.write_bytes(outer.read(dict_names[0]))
        skin_zip_path = None
        for n in nested_names:
            blob = outer.read(n)
            try:
                with zipfile.ZipFile(io.BytesIO(blob)) as inner:
                    names = inner.namelist()
                    if (any(x.endswith("Hero_Billow_Skin_52807_SFX.bnk") for x in names)
                            and any(x.endswith("Actor_599_Actions.pkg.bytes") for x in names)):
                        skin_zip_path = work / "billow_skin.zip"
                        skin_zip_path.write_bytes(blob)
                        break
            except zipfile.BadZipFile:
                pass
    if skin_zip_path is None:
        raise ValueError("Could not find nested Billow source ZIP")
    with zipfile.ZipFile(skin_zip_path) as skin:
        bnk_name = next(n for n in skin.namelist()
                        if n.endswith("Sound/iOS/Hero_Billow_Skin_52807_SFX.bnk"))
        bnk = work / "original.bnk"
        bnk.write_bytes(skin.read(bnk_name))
        pkg_name = next(n for n in skin.namelist()
                        if n.endswith("Actor_599_Actions.pkg.bytes"))
        pkg = work / "Actor_599_Actions.pkg.bytes"
        pkg.write_bytes(skin.read(pkg_name))
    # Decode package entries and confirm U11 already calls the exact missing event.
    hits = []
    with zipfile.ZipFile(pkg) as z:
        for name in z.namelist():
            raw = z.read(name)
            if raw.startswith(b"\x22\x4a\x00\xef"):
                raw = raw[8:]
            if raw.startswith(b"\x28\xb5\x2f\xfd"):
                proc = subprocess.run(
                    ["zstd", "-d", "-q", "-D", str(dictionary), "-c"],
                    input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                if proc.returncode:
                    raise ValueError(f"zstd decode failed for {name}: "
                                     f"{proc.stderr.decode(errors='replace')}")
                raw = proc.stdout
            if EVENT_NAME.encode() in raw:
                hits.append(name)
    if not hits:
        raise ValueError(f"{EVENT_NAME} is not referenced in Actor_599_Actions")
    return bnk, hits


def build_patch(original: bytes):
    sections, objects, media = parse_bnk(original)
    bytype = {}
    byid = {}
    for typ, oid, raw in objects:
        if oid in byid:
            raise ValueError(f"Duplicate original HIRC object id {oid}")
        byid[oid] = (typ, raw)
        bytype.setdefault(typ, {})[oid] = raw
    for mid in TARGET_WEMS + [PRESERVE_WEM]:
        if mid not in media:
            raise ValueError(f"Missing DIDX WEM {mid}")
    if u32(original, 8) != 135:
        raise ValueError("Unexpected Wwise bank version")

    c2_event = fnv1_lower("Play_Billow_Skill_C_2")
    c1_event = fnv1_lower("Play_Billow_Skill_C_1")
    target_event = fnv1_lower(EVENT_NAME)
    if c2_event not in bytype.get(4, {}) or c1_event not in bytype.get(4, {}):
        raise ValueError("Known C1/C2 Event objects not found; refusing to guess template")
    if target_event in byid:
        raise ValueError("Target event already exists; refusing to duplicate")

    # C2 Event -> Action -> Container -> Sound is the known-good SFX template.
    ev = bytype[4][c2_event]
    if len(ev) != 9 or ev[4] != 1:
        raise ValueError("Unexpected C2 Event layout")
    c2_action_id = u32(ev, 5)
    action = bytype[3][c2_action_id]
    if len(action) != 18 or action[5] != 4:
        raise ValueError("Unexpected C2 Play Action layout")
    c2_container_id = u32(action, 6)
    container = bytype[5][c2_container_id]
    children_at, old_children, playlist_at, old_playlist = parse_container(container)
    if len(old_children) != 1:
        raise ValueError("Expected one-child C2 container template")
    old_sound_id = old_children[0]
    sound = bytype[2][old_sound_id]
    if len(sound) != 48:
        raise ValueError("Unexpected C2 Sound layout")
    if u32(sound, 9) != 3418519838:
        raise ValueError("C2 template no longer points to WEM 3418519838")
    if u32(sound, 25) != c2_container_id:
        raise ValueError("C2 Sound parent does not match container")

    labels = [f"Billow_4Sound_Ulti_Sound_{mid}" for mid in TARGET_WEMS]
    labels += ["Billow_4Sound_Ulti_RandomContainer", "Billow_4Sound_Ulti_PlayAction"]
    new_ids = [fnv1_lower(x) for x in labels]
    if len(set(new_ids + [target_event])) != len(new_ids) + 1:
        raise ValueError("Generated object ID collision")
    collisions = [x for x in new_ids + [target_event] if x in byid]
    if collisions:
        raise ValueError(f"Generated IDs already exist: {collisions}")
    new_sound_ids = new_ids[:4]
    new_container_id = new_ids[4]
    new_action_id = new_ids[5]

    new_objects = []
    # Clone the C2 Sound four times; leave every original Sound object untouched.
    for sid, mid in zip(new_sound_ids, TARGET_WEMS):
        sr = bytearray(sound)
        struct.pack_into("<I", sr, 0, sid)
        struct.pack_into("<I", sr, 9, mid)
        struct.pack_into("<I", sr, 13, media[mid][1])
        struct.pack_into("<I", sr, 25, new_container_id)
        new_objects.append((2, sid, bytes(sr)))

    # Clone C2's RandomSequenceContainer, preserving its settings and adding four children.
    prefix = container[:children_at]
    playlist_weight = old_playlist[0][1]
    playlist = struct.pack("<H", len(new_sound_ids)) + b"".join(
        struct.pack("<Ii", sid, playlist_weight) for sid in new_sound_ids)
    new_container = (prefix + struct.pack("<I", len(new_sound_ids))
                     + b"".join(p32(sid) for sid in new_sound_ids) + playlist)
    _, parsed_children, _, parsed_playlist = parse_container(new_container)
    if parsed_children != new_sound_ids or [x[0] for x in parsed_playlist] != new_sound_ids:
        raise ValueError("New container playlist validation failed")
    new_objects.append((5, new_container_id, new_container))

    # Clone the known-good C2 Play Action and point it at the new container.
    ar = bytearray(action)
    struct.pack_into("<I", ar, 0, new_action_id)
    struct.pack_into("<I", ar, 6, new_container_id)
    new_objects.append((3, new_action_id, bytes(ar)))

    # Add the missing event whose Wwise FNV-1 lowercase ID is already used by U11.xml.
    er = bytearray(ev)
    struct.pack_into("<I", er, 0, target_event)
    struct.pack_into("<I", er, 5, new_action_id)
    new_objects.append((4, target_event, bytes(er)))

    # Rebuild only the HIRC chunk.
    hpos, hsize, hbody = sections[b"HIRC"]
    new_records = b"".join(bytes([t]) + p32(len(raw)) + raw
                           for t, _, raw in new_objects)
    new_hbody = p32(len(objects) + len(new_objects)) + hbody[4:] + new_records
    new_chunk = b"HIRC" + p32(len(new_hbody)) + new_hbody
    patched = original[:hpos] + new_chunk + original[hpos + 8 + hsize:]

    # Deep structural validation.
    new_sections, new_all, new_media = parse_bnk(patched)
    new_map = {oid: (typ, raw) for typ, oid, raw in new_all}
    assert len(new_all) == len(objects) + 7
    assert all(mid in new_media for mid in TARGET_WEMS + [PRESERVE_WEM])
    for sid, mid in zip(new_sound_ids, TARGET_WEMS):
        typ, sr = new_map[sid]
        assert typ == 2 and u32(sr, 9) == mid
        assert u32(sr, 13) == new_media[mid][1] and u32(sr, 25) == new_container_id
    ctype, cr2 = new_map[new_container_id]
    _, children, _, playlist_items = parse_container(cr2)
    assert ctype == 5 and children == new_sound_ids
    assert [x[0] for x in playlist_items] == new_sound_ids
    atype, ar2 = new_map[new_action_id]
    assert atype == 3 and u32(ar2, 6) == new_container_id
    etype, er2 = new_map[target_event]
    assert etype == 4 and er2[4] == 1 and u32(er2, 5) == new_action_id
    # Every original HIRC object is copied byte-for-byte, including C1/C2 and hit chains.
    for typ, oid, raw in objects:
        assert new_map[oid] == (typ, raw), f"Original object changed: {oid}"
    # DIDX and DATA are byte-identical.
    for tag in (b"DIDX", b"DATA"):
        _, osz, obody = sections[tag]
        _, nsz, nbody = new_sections[tag]
        assert osz == nsz and obody == nbody, f"{tag!r} changed unexpectedly"
    preserve_sounds = [(oid, raw) for typ, oid, raw in objects
                       if typ == 2 and u32(raw, 9) == PRESERVE_WEM]
    assert preserve_sounds, "Preserved sound 3468852688 not found"

    report = {
        "status": "STRUCTURALLY_VALIDATED_TEST_CANDIDATE",
        "event_name": EVENT_NAME,
        "event_id": target_event,
        "event_id_hex": hex(target_event),
        "source_bank_sha256": hashlib.sha256(original).hexdigest(),
        "patched_bank_sha256": hashlib.sha256(patched).hexdigest(),
        "source_size": len(original),
        "patched_size": len(patched),
        "hirc_objects_before": len(objects),
        "hirc_objects_after": len(new_all),
        "added_objects": {
            "sound_ids": new_sound_ids,
            "container_id": new_container_id,
            "action_id": new_action_id,
            "event_id": target_event
        },
        "new_container_children": [
            {"sound_id": sid, "wem_id": mid, "wem_size": new_media[mid][1]}
            for sid, mid in zip(new_sound_ids, TARGET_WEMS)
        ],
        "preserved_existing_wem_id": PRESERVE_WEM,
        "preserved_existing_c1_event_id": c1_event,
        "preserved_existing_c2_event_id": c2_event,
        "checks": [
            "HIRC boundaries/count", "unique object IDs",
            "four Sound->DIDX links", "four child IDs", "four playlist IDs",
            "Action->Container", "Event->Action",
            "all original HIRC objects byte-identical",
            "DIDX/DATA byte-identical", "preserved 3468852688 link"
        ]
    }
    return patched, report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-zip", required=True)
    ap.add_argument("--out-dir", default="out")
    args = ap.parse_args()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        bnk, hits = extract_inputs(Path(args.source_zip), Path(td))
        patched, report = build_patch(bnk.read_bytes())
        report["action_xml_event_hits"] = hits
        target = out / "Hero_Billow_Skin_52807_SFX_4SOUND_TEST.bnk"
        target.write_bytes(patched)
        (out / "validation-report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8")
        with zipfile.ZipFile(out / "Billow_4Sound_Ulti_GitHub_Test.zip",
                             "w", zipfile.ZIP_DEFLATED) as z:
            z.write(target, target.name)
            z.write(out / "validation-report.json", "validation-report.json")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
