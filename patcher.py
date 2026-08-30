#!/usr/bin/env python3
"""
Life is Strange: Double Exposure - Native Ultrawide & Cutscene Aspect Patcher
Supports 21:9, 32:9, 32:10, 16:10, custom resolutions, and cutscene framing modes.
Compatible with Python 3.6+

Modes:
  horplus - True Hor+ ultrawide everywhere (exploration + cutscenes + dialogues).
            Zero vertical crop: keeps the full 16:9 vertical framing and expands
            the horizontal field of view to fill the monitor. Also removes the
            zoom-in jump when a cutscene hands control back to the player.
  clean   - Legacy: ultrawide exploration (Vert-) + pillarboxed 16:9 cutscenes.
  full    - Legacy: all 11 aspect constants patched (Vert- ~20% vertical crop).
  stock   - Restore the original 16:9 executable.
"""

import argparse
import os
import re
import shutil
import struct
import sys

# ---------------------------------------------------------------------------
# Patch definitions
# ---------------------------------------------------------------------------

# 11 Verified Camera Aspect Ratio float constant locations (legacy modes)
ALL_ASPECT_OFFSETS = [
    0x257BDEC, 0x23E5558, 0x23E5739, 0x23E665C, 0x43FEB0F,
    0x43FEB58, 0x43FEFD1, 0x44004BF, 0x440050B, 0x4401BBF, 0x69C8A8C
]

# Legacy 2-Offset Clean Mode: Player Exploration (0x23E665C) + Photo Table (0x69C8A8C)
CLEAN_OFFSETS = [0x23E665C, 0x69C8A8C]

# --- True Hor+ mode -------------------------------------------------------
# Rationale (see RESEARCH.md section "The Hor+ Breakthrough"):
# UE5's FMinimalViewInfo::CalculateProjectionMatrixGivenViewRectangle already
# contains perfect Hor+ math in its AspectRatio_MaintainYFOV branch: it derives
# the vertical FOV from the camera's AUTHORED aspect ratio
# (vFOV = 2*atan(tan(hFOV/2) / AspectRatio)) and then expands horizontally to
# the real viewport. To activate it for every camera we need two code patches:
#
# 1) UCameraComponent::GetCameraView copies bConstrainAspectRatio from the
#    component into the output FMinimalViewInfo. Replacing the 7-byte
#    "movzx eax, byte [rbx+0x2B4]" with "xor eax, eax" + 5-byte NOP makes the
#    subsequent bit-merge clear the flag instead -> no more 16:9 pillarbox
#    constraint for ANY camera (cinematic CineCameras included, since
#    UCineCameraComponent::GetCameraView calls Super::GetCameraView).
#
# 2) In CalculateProjectionMatrixGivenViewRectangle the axis-constraint enum
#    (dl) is compared against 2 (MajorAxisFOV) and 1 (MaintainXFOV); both jump
#    to the Vert- path. Rewriting both immediates to 0xFF makes every
#    perspective camera fall through into the MaintainYFOV Hor+ path,
#    regardless of LocalPlayer settings or per-camera overrides.
#
# The player camera aspect constant 0x23E665C must remain STOCK (1.7777778) in
# this mode: the engine's conversion uses it as "the aspect ratio the FOV was
# authored for". (Patching it to the monitor ratio is what caused the Vert-
# zoom-in when leaving cutscenes in the legacy clean mode.)
#
# On a 16:9 monitor these patches are behavior-neutral.

PATCH_UNCONSTRAIN = {
    "name": "Unconstrain cameras (UCameraComponent::GetCameraView)",
    # movzx eax,[rbx+2B4]; xor eax,[rdi+4C]; and eax,1  (flag copy preamble)
    "sig": "0F B6 83 B4 02 00 00 33 47 4C 83 E0 01",
    "expected": 0x441A14C,
    # xor eax,eax ; nop5  (exactly 7 bytes, instruction-boundary safe)
    "edits": [(0, bytes.fromhex("31C00F1F440000"))],
}

PATCH_AXIS = {
    "name": "Force Hor+ MaintainYFOV branch (CalculateProjectionMatrixGivenViewRectangle)",
    # cmp eax,ecx; jle +9; cmp dl,2; je Vert-; cmp dl,1; je Vert-
    "sig": "3B C1 7E 09 80 FA 02 0F 84 ?? ?? ?? ?? 80 FA 01 0F 84 ?? ?? ?? ??",
    "expected": 0x440ABC0,
    "edits": [(6, b"\xFF"), (15, b"\xFF")],  # cmp dl,2 -> cmp dl,0xFF ; cmp dl,1 -> cmp dl,0xFF
}

# UCineCameraComponent::GetCameraView contains the ONLY direct call to
# UCameraComponent::GetCameraView in the entire binary (every other invocation
# is virtual, used by non-cinematic cameras):
#   call UCameraComponent::GetCameraView   <- E8 at signature offset +13
#   ; rdi = FMinimalViewInfo& DesiredView (non-volatile, held across the call)
# We reroute that call through a code cave that calls the original and then
# clears bConstrainAspectRatio (bit 0 of DesiredView+0x4C). This unconstrains
# EVERY cinematic camera view regardless of where the flag came from
# (constructors, cooked asset data, or Blueprint logic) while leaving all
# non-cinematic cameras untouched.
CINE_GCV_CALLSITE = {
    "name": "Cine GetCameraView super-call site",
    # call UpdateCameraLens; mov r8,rdi; movaps xmm1,xmm6; mov rcx,rbx; call Super
    "sig": "E8 ?? ?? ?? ?? 4C 8B C7 0F 28 CE 48 8B CB E8",
    "expected": 0x4005B78,
    "call_at": 14,  # offset of the Super E8 within the signature
}

# cave B: sub rsp,0x28 ; call Super ; add rsp,0x28 ; or byte [rdi+0x4C],1 ; ret
# Forces bConstrainAspectRatio=TRUE on every UCineCameraComponent view. In this
# game cine cameras are the LOADING/transition views (field-tested v3.1: caving
# the cine path widened loading, not cutscenes) - they must stay pillarboxed
# even though cave A below would otherwise unconstrain their 16:9 aspect.
CAVE_PROLOGUE = bytes.fromhex("4883EC28")
CAVE_EPILOGUE = bytes.fromhex("4883C428" "804F4C01" "C3")
CAVE_SIZE = len(CAVE_PROLOGUE) + 5 + len(CAVE_EPILOGUE)  # 18 bytes

# --- cave A: aspect-gated unconstrain inside UCameraComponent::GetCameraView --
# The 7-byte "movzx eax, byte [rbx+2B4]" flag-copy preamble (the site whose
# unconditional clearing gave the field-proven great Hor+ cutscenes in v1) is
# replaced by "call caveA ; nop2". The cave re-executes the movzx, then clears
# bit 0 of eax ONLY when the component's AspectRatio member lies in
# (1.75, 1.8) - i.e. cameras authored for 16:9 (the cutscene cameras).
# Exploration/photo cameras carry the patched monitor aspect (>1.8) and square
# capture cameras (~1.0) fall below the window, so they keep their constraint.
# ecx is safe to clobber (reloaded immediately after the patched site).
GATE_SITE = {
    "name": "GetCameraView flag-copy gate site",
    "sig": "0F B6 83 B4 02 00 00 33 47 4C 83 E0 01",
    "expected": 0x441A14C,
}
CAVE_A = bytes.fromhex(
    "0FB683B4020000"    # movzx eax, byte [rbx+0x2B4]
    "8B8BB0020000"      # mov   ecx, [rbx+0x2B0]  (AspectRatio)
    "81F90000E03F"      # cmp   ecx, 0x3FE00000   (1.75f)
    "760B"              # jbe   done
    "81F96666E63F"      # cmp   ecx, 0x3FE66666   (1.8f)
    "7303"              # jae   done
    "83E0FE"            # and   eax, -2           (clear bConstrainAspectRatio)
    "C3"                # done: ret
)


def apply_aspect_gate_cave(data):
    site = locate(data, GATE_SITE)
    cave = find_code_cave(data, len(CAVE_A) + 8)
    data[cave:cave + len(CAVE_A)] = CAVE_A
    patch = b"\xE8" + struct.pack("<i", cave - (site + 5)) + b"\x66\x90"
    data[site:site + 7] = patch
    print("  patched: aspect-gated unconstrain cave @ {:#x} "
          "(GetCameraView site {:#x})".format(cave, site))


def find_text_section(data):
    pe_off = struct.unpack_from("<I", data, 0x3C)[0]
    num_sections = struct.unpack_from("<H", data, pe_off + 6)[0]
    opt_size = struct.unpack_from("<H", data, pe_off + 20)[0]
    sec_off = pe_off + 24 + opt_size
    for i in range(num_sections):
        o = sec_off + i * 40
        if data[o:o+8].rstrip(b"\0") == b".text":
            vsize, va, rawsize, rawptr = struct.unpack_from("<IIII", data, o + 8)
            return rawptr, rawsize
    raise RuntimeError(".text section not found")


def find_code_cave(data, need):
    """First int3 (0xCC) padding run in .text large enough to host the cave."""
    lo, size = find_text_section(data)
    hi = lo + size
    i = lo
    run = b"\xCC" * need
    while True:
        i = data.find(run, i, hi)
        if i == -1:
            raise RuntimeError("no int3 code cave of {} bytes found".format(need))
        # use only runs that start on an instruction boundary after a previous
        # run byte or function end; starting at the first CC of the run is safe
        if data[i - 1] != 0xCC:
            return i
        i += 1


def apply_cine_gcv_cave(data):
    site = locate(data, CINE_GCV_CALLSITE)
    call_off = site + CINE_GCV_CALLSITE["call_at"]
    if data[call_off] != 0xE8:
        raise RuntimeError("cine GetCameraView call site: expected E8")
    old_disp = struct.unpack_from("<i", data, call_off + 1)[0]
    super_off = call_off + 5 + old_disp  # file offset == VA delta within .text
    cave = find_code_cave(data, CAVE_SIZE + 8)
    # cave layout: prologue, call Super (rel32 from cave), epilogue
    cave_call_off = cave + len(CAVE_PROLOGUE)
    rel_to_super = super_off - (cave_call_off + 5)
    blob = CAVE_PROLOGUE + b"\xE8" + struct.pack("<i", rel_to_super) + CAVE_EPILOGUE
    data[cave:cave + len(blob)] = blob
    # reroute the original call to the cave
    struct.pack_into("<i", data, call_off + 1, cave - (call_off + 5))
    print("  patched: cine GetCameraView rerouted via cave @ {:#x} "
          "(super call at {:#x} -> original target {:#x})".format(
              cave, call_off, super_off))


PATCH_CINE_UNCONSTRAIN = {
    "name": "Unconstrain cinematic cameras only (UCineCameraComponent ctor)",
    # or [rdi+3A],2 ; xor eax,eax ; or [rdi+8A],2 ; or [rdi+2B4],1  <- imm of the
    # last OR is bConstrainAspectRatio=true in the CineCameraComponent constructor
    "sig": "80 4F 3A 02 33 C0 80 8F 8A 00 00 00 02 80 8F B4 02 00 00 01",
    "expected": 0x40049E9,
    "edits": [(19, b"\x00")],  # or byte [rdi+2B4], 1 -> or byte [rdi+2B4], 0 (no-op)
}

HORPLUS_PATCHES = [PATCH_UNCONSTRAIN, PATCH_AXIS]

# Photo projection static float table (shared by all modes): DF7CDB3D 5555553F <AR>
PHOTO_TABLE = {
    "name": "Photo projection table",
    "sig": "DF 7C DB 3D 55 55 55 3F 39 8E E3 3F",
    "expected": 0x69C8A84,
    "float_at": 8,
}

PRESETS = {
    "1": ("5120x2160 (21:9 WUHD 4K)", 5120, 2160),
    "2": ("3440x1440 (21:9 UWQHD)", 3440, 1440),
    "3": ("2560x1080 (21:9 UWD)", 2560, 1080),
    "4": ("3840x1600 (24:10 UW)", 3840, 1600),
    "5": ("5120x1440 (32:9 Super Ultrawide)", 5120, 1440),
    "6": ("3840x1080 (32:9 Super Ultrawide)", 3840, 1080),
    "7": ("7680x2160 (32:9 Super Ultrawide)", 7680, 2160),
    "8": ("3840x1200 (32:10)", 3840, 1200),
}

# ---------------------------------------------------------------------------
# Signature scanning
# ---------------------------------------------------------------------------

def parse_sig(sig):
    """'0F B6 ?? 02' -> (bytes, mask) where mask byte 0 = wildcard."""
    parts = sig.split()
    pat = bytearray()
    mask = bytearray()
    for p in parts:
        if p == "??":
            pat.append(0)
            mask.append(0)
        else:
            pat.append(int(p, 16))
            mask.append(1)
    return bytes(pat), bytes(mask)


def masked_find_all(data, pat, mask, limit=4):
    """Find all offsets matching the masked pattern (bounded)."""
    anchor = None
    for i, m in enumerate(mask):
        if m:
            anchor = i
            break
    results = []
    start = 0
    first_byte = pat[anchor:anchor + 1]
    while len(results) < limit:
        i = data.find(first_byte, start + anchor)
        if i == -1:
            break
        base = i - anchor
        start = base + 1
        if base < 0 or base + len(pat) > len(data):
            continue
        ok = True
        for j in range(len(pat)):
            if mask[j] and data[base + j] != pat[j]:
                ok = False
                break
        if ok:
            results.append(base)
    return results


def locate(data, spec):
    """Locate a patch site: prefer the known offset if its bytes match,
    otherwise fall back to a unique signature scan (game-update resilience)."""
    pat, mask = parse_sig(spec["sig"])
    exp = spec["expected"]
    window = data[exp:exp + len(pat)]
    if len(window) == len(pat) and all(
            (not mask[j]) or window[j] == pat[j] for j in range(len(pat))):
        return exp
    hits = masked_find_all(data, pat, mask)
    if len(hits) == 1:
        print("  note: '{}' moved to file offset {:#x} (game update?)".format(
            spec["name"], hits[0]))
        return hits[0]
    if not hits:
        raise RuntimeError(
            "Signature for '{}' not found. The game executable version is not "
            "supported by this patcher build.".format(spec["name"]))
    raise RuntimeError(
        "Signature for '{}' is ambiguous ({} matches).".format(
            spec["name"], len(hits)))

# ---------------------------------------------------------------------------
# Patching
# ---------------------------------------------------------------------------

def find_exe():
    possible_paths = [
        "Chronos-Win64-Shipping.exe",
        os.path.join("Chronos", "Binaries", "Win64", "Chronos-Win64-Shipping.exe"),
        os.path.join("..", "Chronos", "Binaries", "Win64", "Chronos-Win64-Shipping.exe"),
    ]
    for p in possible_paths:
        if os.path.isfile(p):
            return os.path.abspath(p)
    return None


def apply_edits(data, spec):
    base = locate(data, spec)
    for rel, payload in spec["edits"]:
        data[base + rel:base + rel + len(payload)] = payload
    print("  patched: {} @ {:#x}".format(spec["name"], base))


def patch_photo_table(data, target_bytes):
    base = locate(data, PHOTO_TABLE)
    off = base + PHOTO_TABLE["float_at"]
    data[off:off + 4] = target_bytes
    print("  patched: {} @ {:#x}".format(PHOTO_TABLE["name"], off))


def patch_exe(exe_path, width, height, mode):
    backup_path = exe_path + ".original"
    if not os.path.exists(backup_path):
        shutil.copy2(exe_path, backup_path)
        print("Created original backup: {}".format(os.path.basename(backup_path)))

    # Always start from the clean original backup so modes never stack.
    with open(backup_path, "rb") as f:
        data = bytearray(f.read())

    if mode == "stock":
        print("\nRestoring original stock 16:9 executable...")
    else:
        ratio = float(width) / float(height)
        target_bytes = struct.pack("<f", ratio)
        hex_str = " ".join("{:02X}".format(b) for b in target_bytes)
        print("\nTarget Resolution: {}x{}".format(width, height))
        print("Target Aspect Ratio: {:.6f} (Hex: {})".format(ratio, hex_str))

        if mode == "cine":
            # Recommended: exactly the proven legacy-clean behavior for
            # exploration, photos and loading (constrained full-width player
            # camera + matching photo table), plus Hor+ cutscenes:
            # cinematic CineCameraComponents alone default to unconstrained
            # (1-byte constructor patch) and the forced MaintainYFOV branch
            # renders them full-width with the complete 16:9 vertical framing.
            for off in CLEAN_OFFSETS:
                data[off:off + 4] = target_bytes
            apply_edits(data, PATCH_AXIS)
            apply_aspect_gate_cave(data)
            apply_cine_gcv_cave(data)
            # note: the UCineCameraComponent ctor default (0x40049FC) stays
            # STOCK in this mode - cine cameras are the loading views and must
            # remain constrained (cave B enforces it regardless).
            print("Applied Cine Hor+ Patch: ultrawide exploration + unskewed "
                  "photos + pillarboxed loading (as the classic fix) + true "
                  "Hor+ ultrawide cutscenes and dialogues (0% vertical crop).")
        elif mode == "horplus":
            for spec in HORPLUS_PATCHES:
                apply_edits(data, spec)
            patch_photo_table(data, target_bytes)
            # 0x23E665C intentionally stays STOCK (authored aspect feeds the
            # engine's Hor+ FOV conversion).
            print("Applied True Hor+ Patch: full-width rendering everywhere with "
                  "0% vertical crop (cutscenes, dialogues, exploration) + "
                  "no zoom jump after cutscenes.")
            print("NOTE: photo mode and loading views are also unconstrained in "
                  "this mode; use --mode hybrid + the UE4SS UltrawideCameraFix "
                  "mod to keep those pillarboxed 16:9.")
        elif mode == "hybrid":
            # Hybrid mode (recommended when UE4SS is installed):
            # - exe only forces the Hor+ MaintainYFOV branch (neutral for
            #   constrained cameras; also fixes the sequencer MaintainXFOV leak)
            # - the UE4SS Lua mod decides at runtime WHICH cameras are
            #   unconstrained: cutscenes/exploration Hor+, while photo mode and
            #   the post-load grace period stay pillarboxed 16:9.
            # - photo table stays STOCK: constrained photo mode is then
            #   bit-identical to vanilla -> photos can never skew.
            apply_edits(data, PATCH_AXIS)
            print("Applied Hybrid Patch: Hor+ projection branch forced in the exe; "
                  "camera constraint control delegated to the UE4SS "
                  "UltrawideCameraFix mod. Photo table left stock.")
        elif mode == "clean":
            for off in CLEAN_OFFSETS:
                data[off:off + 4] = target_bytes
            print("Applied Legacy Clean Patch: ultrawide exploration + unskewed "
                  "photos + pillarboxed 16:9 cutscenes.")
        elif mode == "full":
            for off in ALL_ASPECT_OFFSETS:
                data[off:off + 4] = target_bytes
            print("Applied Legacy Full Patch: all 11 locations (edge-to-edge with "
                  "~20% vertical crop).")
        else:
            raise ValueError("unknown mode: " + mode)

    tmp_path = exe_path + ".tmp"
    with open(tmp_path, "wb") as f:
        f.write(data)
    os.replace(tmp_path, exe_path)
    print("Successfully updated {}!".format(os.path.basename(exe_path)))

    # The exe patch is self-contained: disable SUWSF so it cannot re-apply
    # in-memory aspect patches on top (it would poison Hor+ mode's math).
    ini_path = os.path.join(os.path.dirname(exe_path), "SUWSF.ini")
    if os.path.isfile(ini_path):
        with open(ini_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        if "Enabled=true" in content:
            content = content.replace("Enabled=true", "Enabled=false")
            with open(ini_path, "w", encoding="utf-8") as f:
                f.write(content)
            print("Disabled conflicting SUWSF.ini in-memory patches.")

# ---------------------------------------------------------------------------
# CLI / interactive
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="LiS: Double Exposure ultrawide patcher")
    parser.add_argument("--exe", help="path to Chronos-Win64-Shipping.exe")
    parser.add_argument("--mode", choices=["cine", "horplus", "hybrid", "clean", "full", "stock"],
                        help="patch mode (skips interactive prompts)")
    parser.add_argument("--width", type=int, help="target width, e.g. 5120")
    parser.add_argument("--height", type=int, help="target height, e.g. 2160")
    args = parser.parse_args()

    print("=" * 60)
    print(" Life is Strange: Double Exposure - Ultrawide Patcher")
    print("=" * 60)

    exe_path = args.exe or find_exe()
    if not exe_path:
        exe_path = input("Enter path to Chronos-Win64-Shipping.exe: ").strip(" \"'")
    if not os.path.isfile(exe_path):
        print("Error: Could not find file at '{}'".format(exe_path))
        sys.exit(1)
    print("Found game executable: {}\n".format(exe_path))

    if args.mode:
        if args.mode not in ("stock", "hybrid") and not (args.width and args.height):
            print("Error: --mode {} requires --width and --height".format(args.mode))
            sys.exit(1)
        patch_exe(exe_path, args.width or 16, args.height or 9, args.mode)
        return

    print("Select target aspect ratio:")
    for k in sorted(PRESETS):
        print("  [{}] {}".format(k, PRESETS[k][0]))
    print("  [C] Custom Resolution")
    print("  [R] Restore Original Stock (16:9)")

    choice = input("\nEnter choice [1-8, C or R]: ").strip().upper()
    if choice == "R":
        patch_exe(exe_path, 16, 9, "stock")
        return
    if choice in PRESETS:
        _, w, h = PRESETS[choice]
    elif choice == "C":
        w = int(input("Enter Width (e.g. 5120): ").strip())
        h = int(input("Enter Height (e.g. 2160): ").strip())
    else:
        print("Invalid resolution option.")
        return

    print("\nSelect Mode:")
    print("  [1] Recommended: Hor+ Cutscenes + Classic Ultrawide Exploration")
    print("      Cutscenes and dialogues render true Hor+ ultrawide (full 16:9")
    print("      vertical framing + expanded sides, no black bars). Exploration,")
    print("      photos and loading behave exactly like the proven classic fix.")
    print("  [2] True Hor+ Ultrawide Everywhere (experimental)")
    print("      Hor+ for every camera, but photo mode and loading views are")
    print("      unconstrained too (photos may skew, loading pop-in visible).")
    print("  [3] Legacy: Uncropped 16:9 Cutscenes (pillarboxed cinematics)")
    print("  [4] Legacy: Full Ultrawide, Vert- (~20% vertical crop in cutscenes)")

    cm_choice = input("\nEnter choice [1-4, default 1]: ").strip()
    mode = {"2": "horplus", "3": "clean", "4": "full"}.get(cm_choice, "cine")
    patch_exe(exe_path, w, h, mode)


if __name__ == "__main__":
    main()
