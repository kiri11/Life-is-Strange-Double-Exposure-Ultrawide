#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# dependencies = ["blake3"]
# ///
"""
Life is Strange: Double Exposure - Ultrawide Fix installer.

Run it with no arguments for an interactive install, or:

    uv run patcher.py --yes            # everything, auto-detected resolution
    python patcher.py --width 5120 --height 2160 --yes
    python patcher.py --restore        # undo everything

Four independent parts, all on by default:

  1. Ultrawide camera   - patches the executable (see RESEARCH.md)
  2. Full-width UI      - patches the game data files via tools/assetdump
  3. Chromatic aberration off } both write a single managed block into the
  4. Anti-blur TSR settings   } user's Engine.ini, removed again by --restore

`uv run` is the easiest entry point: it needs no venv and fetches the one
optional dependency (blake3, used only by part 2) automatically.

Advanced: --mode patches only the executable, in one of the legacy modes
(cine, horplus, hybrid, clean, full, stock). cine is the shipped behaviour.
Compatible with Python 3.6+ (3.8+ under uv).
"""

import argparse
import io
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
# Gate: unconstrain any camera authored ~16:9 (AspectRatio in the open
# (1.75, 1.8) window) -> cutscenes/dialogues render Hor+. Exploration and
# photo cameras carry the patched monitor aspect (>1.8) and square capture
# cameras (~1.0) fall below the window, so they keep their constraint.
# Field-tested conclusion (see RESEARCH.md 4f/4g): the loading side-peek
# CANNOT be fixed by any camera gate - during loads the game holds the next
# scene's cutscene camera behind a 16:9-sized loading overlay, so boxing
# loading would box cutscenes too. The range gate (rather than an exact
# float match) is deliberate: it also widens the main menu and any cutscene
# shots whose 16:9 aspect was serialized with slightly different float bits.
# The upper bound is a parameter. Two settings are meaningful:
#
#   1.8 (default)   - only cameras authored ~16:9 are unconstrained.
#   monitor aspect  - every camera authored NARROWER than the screen is
#                     unconstrained ("wide gate", --wide-gate).
#
# The wide gate exists because leaving a dialogue does not hand over between two
# static cameras: Deck Nine drive camera state per frame from data assets, so the
# live component's AspectRatio member is ANIMATED from 16:9 up to the monitor
# ratio. With the 1.8 bound the camera falls out of the gate the moment that
# animation passes 1.8 and is pillarboxed for the rest of the move - bars at full
# width, then shrinking to nothing as the aspect reaches the monitor ratio. That
# sweep is the brief zoom seen when a dialogue hands control back. Carrying the
# bound up to the monitor aspect keeps the camera unconstrained for the whole
# animation; the exploration camera's final value - exactly the monitor aspect -
# still lands on `jae` and stays constrained, so nothing else changes.
GATE_DEFAULT_UPPER = bytes.fromhex("6666E63F")   # 1.8f


# AUTHORED_ASPECT is the value pinned into the view for every camera the gate
# unconstrains. It exists because the game animates a camera's AspectRatio when
# it hands control back from a dialogue: the member ramps from the authored 16:9
# up to the VIEWPORT aspect over about a second (measured - see RESEARCH 10).
# That ramp is the game's letterbox-open animation. In stock UE it is harmless,
# because a constrained camera takes the MaintainXFOV path where AspectRatio
# only sizes the view RECT. Under the forced MaintainYFOV branch, AspectRatio
# becomes the FOV divisor, so the same ramp is re-read as a vertical-FOV change:
# the view zooms in as the ramp climbs and then snaps back when the gameplay
# camera (still at 16:9) takes over. Pinning the divisor to the authored aspect
# restores the rule stated in RESEARCH 2a - the divisor must be the aspect the
# FOV was AUTHORED for - and makes the whole hand-off framing-neutral.
AUTHORED_ASPECT = bytes.fromhex("398EE33F")      # 1.7777778f (closest float to 16/9)


def build_cave_a(upper_bytes, pin_bytes=AUTHORED_ASPECT):
    return (bytes.fromhex(
        "0FB683B4020000"    # movzx eax, byte [rbx+0x2B4]
        "8B8BB0020000"      # mov   ecx, [rbx+0x2B0]  (component AspectRatio)
        "81F90000E03F"      # cmp   ecx, 0x3FE00000   (1.75f)
        "7612"              # jbe   done  (+18)
        "81F9")             # cmp   ecx, <upper bound>
        + upper_bytes
        + bytes.fromhex(
        "730A"              # jae   done  (+10)
        "83E0FE"            # and   eax, -2           (clear bConstrainAspectRatio)
        "C74748")           # mov   dword [rdi+0x48], <authored aspect>
        + pin_bytes
        + bytes.fromhex(
        "C3"))              # done: ret


def apply_aspect_gate_cave(data, upper_bytes=GATE_DEFAULT_UPPER):
    cave_a = build_cave_a(upper_bytes)
    site = locate(data, GATE_SITE)
    cave = find_code_cave(data, len(cave_a) + 8)
    data[cave:cave + len(cave_a)] = cave_a
    patch = b"\xE8" + struct.pack("<i", cave - (site + 5)) + b"\x66\x90"
    data[site:site + 7] = patch
    print("  patched: aspect-gated unconstrain cave @ {:#x} "
          "(GetCameraView site {:#x}, gate upper bound {:.6f})".format(
              cave, site, struct.unpack("<f", upper_bytes)[0]))


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


def patch_exe(exe_path, width, height, mode, gate_upper_aspect=None):
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
            # Recommended. Three code changes, no aspect-ratio CONSTANTS at all:
            #
            #   1. force the Hor+ MaintainYFOV projection branch;
            #   2. cave A - unconstrain every camera authored narrower than the
            #      display, and pin the FOV divisor to the authored 16:9;
            #   3. cave B - keep the cine (loading/transition) views boxed.
            #
            # The aspect constants at 0x23E665C and the photo table are left
            # STOCK. Runtime measurement (RESEARCH 10) showed they do not govern
            # the cameras the old comment claimed: free-roam already renders Hor+
            # through cave A, and the photo pipeline is bit-identical to vanilla
            # when both constants keep their shipped 16:9 values. Patching them
            # only desynchronised the dialogue hand-off.
            apply_edits(data, PATCH_AXIS)
            gate_upper = struct.pack("<f", gate_upper_aspect
                                     or round(max(ratio, 1.8) * 1.002, 4))
            apply_aspect_gate_cave(data, gate_upper)
            apply_cine_gcv_cave(data)
            print("Applied Cine Hor+ Patch: true Hor+ ultrawide everywhere "
                  "(0% vertical crop) with unskewed photos and boxed loading "
                  "views - no zoom or snap when a dialogue hands control back.")
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

# ---------------------------------------------------------------------------
# Display detection
# ---------------------------------------------------------------------------

def detect_resolution():
    """-> (w, h) of the primary display, or None."""
    try:
        import ctypes
        u = ctypes.windll.user32
        try:
            u.SetProcessDPIAware()
        except Exception:
            pass
        w, h = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
        if w > 0 and h > 0:
            return w, h
    except Exception:
        pass
    try:                                               # Linux / Proton
        import subprocess
        out = subprocess.check_output(["xrandr"], stderr=subprocess.DEVNULL)
        m = re.search(br"current (\d+) x (\d+)", out)
        if m:
            return int(m.group(1)), int(m.group(2))
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Engine.ini tweaks
# ---------------------------------------------------------------------------

INI_BEGIN = "; ===== BEGIN LiS:DE Ultrawide Fix (managed block - safe to delete) ====="
INI_END = "; ===== END LiS:DE Ultrawide Fix ====="


def engine_ini_path():
    """Locate the user's Engine.ini (native Windows first, then a Proton prefix)."""
    base = os.environ.get("LOCALAPPDATA")
    if base:
        p = os.path.join(base, "Chronos", "Saved", "Config", "Windows", "Engine.ini")
        if os.path.isdir(os.path.dirname(p)):
            return p
    import glob
    home = os.path.expanduser("~")
    for root in (os.path.join(home, ".steam", "steam"),
                 os.path.join(home, ".local", "share", "Steam")):
        hits = glob.glob(os.path.join(
            root, "steamapps", "compatdata", "*", "pfx", "drive_c", "users",
            "steamuser", "AppData", "Local", "Chronos", "Saved", "Config",
            "Windows", "Engine.ini"))
        if hits:
            return hits[0]
    if base:
        return os.path.join(base, "Chronos", "Saved", "Config", "Windows", "Engine.ini")
    return None


def tsr_settings(width, height):
    """Recommended TSR values for this resolution.

    TSR - UE5's temporal upscaler - is what makes this game look soft. The two
    settings that matter most are rendering at 100% of the output resolution
    rather than upscaling from a lower one, and giving TSR a history buffer
    above output resolution to resolve detail from. The history multiplier is
    the expensive one, so it is scaled back at very high pixel counts.

    These are a sane starting point, not gospel - every line is a normal UE
    console variable and can be edited in Engine.ini afterwards.
    """
    megapixels = (width * height) / 1e6
    if megapixels < 8.0:            # up to ~3840x1600 / 3440x1440
        history, sharpen = 200, 0.5
    else:                           # 5120x2160, 7680x2160, ...
        history, sharpen = 150, 0.7
    return [
        (None, "Render at 100% of the output resolution instead of upscaling from lower"),
        ("r.ScreenPercentage", 100),
        (None, "Highest temporal-upsampler quality"),
        ("r.PostProcessAAQuality", 6),
        (None, "TSR history buffer above output resolution - the main anti-blur knob"),
        (None, "200 = sharpest, 100 = cheapest; %.1f MP here" % megapixels),
        ("r.TSR.History.ScreenPercentage", history),
        (None, "Mild output sharpening to counter the temporal filter"),
        ("r.Tonemapper.Sharpen", sharpen),
        (None, "Slightly sharper texture mips"),
        ("r.MipMapLODBias", -0.5),
    ]


def build_ini_block(width, height, chromatic, sharpness):
    lines = [INI_BEGIN, "[SystemSettings]"]
    if chromatic:
        lines.append("; Chromatic aberration is far more obvious at the widened screen edges")
        lines.append("r.SceneColorFringeQuality=0")
    if sharpness:
        if chromatic:
            lines.append("")
        for key, val in tsr_settings(width, height):
            lines.append("; " + val if key is None else "%s=%s" % (key, val))
    lines.append(INI_END)
    return "\n".join(lines) + "\n"


def strip_ini_block(text):
    """Remove a previously written managed block, so re-runs never stack."""
    out, skipping = [], False
    for line in text.splitlines(True):
        if line.strip() == INI_BEGIN:
            skipping = True
            # also drop the single blank separator line we insert before it,
            # so removing the block restores the file byte-for-byte
            if out and not out[-1].strip():
                out.pop()
            continue
        if skipping:
            if line.strip() == INI_END:
                skipping = False
            continue
        out.append(line)
    return "".join(out)


def apply_engine_ini(width, height, chromatic, sharpness, remove=False):
    path = engine_ini_path()
    if not path:
        print("  !! could not locate Engine.ini - skipping the display tweaks")
        return False
    old = ""
    if os.path.isfile(path):
        with io.open(path, encoding="utf-8", errors="replace") as f:
            old = f.read()
    new = strip_ini_block(old)
    if not remove:
        if new and not new.endswith("\n"):
            new += "\n"
        new += ("\n" if new.strip() else "") + build_ini_block(
            width, height, chromatic, sharpness)
    if new == old:
        print("  already up to date: {}".format(path))
        return True
    parent = os.path.dirname(path)
    if not os.path.isdir(parent):
        os.makedirs(parent)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(new)
    print("  {} {}".format("removed the managed block from" if remove else "wrote", path))
    return True


# ---------------------------------------------------------------------------
# Game-file (UI layout) patch - delegates to tools/assetdump/patch_ui_layout.py
# ---------------------------------------------------------------------------

def paks_dir_for(exe_path):
    """<game>/Chronos/Binaries/Win64/x.exe -> <game>/Chronos/Content/Paks"""
    win64 = os.path.dirname(os.path.abspath(exe_path))
    chronos = os.path.dirname(os.path.dirname(win64))
    return os.path.join(chronos, "Content", "Paks")


def oodle_present():
    here = os.path.dirname(os.path.abspath(__file__))
    names = ("oodle-data-shared.dll", "oo2core_9_win64.dll", "liboodle-data-shared.so")
    return any(os.path.isfile(os.path.join(here, "tools", "assetdump", n))
               or os.path.isfile(os.path.join(here, n)) for n in names)


def game_files_requirements():
    """-> (ok, [missing]) for the UI-layout patch's extra dependencies."""
    missing = []
    try:
        import blake3  # noqa: F401
    except ImportError:
        missing.append("the 'blake3' module - run this installer with "
                       "'uv run patcher.py' and it is fetched automatically, "
                       "or 'pip install blake3'")
    if not oodle_present():
        missing.append("an Oodle decompressor DLL in tools/assetdump/ "
                       "(see tools/assetdump/README.md - it cannot be redistributed)")
    return (not missing), missing


def apply_game_files(exe_path, width, height, restore=False):
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(here, "tools", "assetdump", "patch_ui_layout.py")
    if not os.path.isfile(script):
        print("  !! {} not found - skipping".format(script))
        return False
    cmd = [sys.executable, script, "--paks", paks_dir_for(exe_path)]
    cmd += ["--restore"] if restore else ["--width", str(width), "--height", str(height)]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out, _ = proc.communicate()
    except Exception as ex:
        print("  !! could not run the UI-layout patcher: {}".format(ex))
        return False
    for line in out.decode("utf-8", "replace").splitlines():
        print("  | " + line)
    return proc.returncode == 0


# ---------------------------------------------------------------------------
# Installer
# ---------------------------------------------------------------------------

def ask_yes(prompt, default=True):
    try:
        answer = input(prompt + (" [Y/n]: " if default else " [y/N]: ")).strip().lower()
    except EOFError:
        return default
    return default if not answer else answer.startswith("y")


def choose_resolution(detected):
    print("\nSelect your display resolution:")
    for k in sorted(PRESETS):
        name, w, h = PRESETS[k]
        print("  [{}] {}{}".format(k, name,
                                   "   <- detected" if detected == (w, h) else ""))
    print("  [C] Custom")
    if detected:
        print("\n  Detected display: {}x{}".format(*detected))
        prompt = "Enter choice [1-8, C, or Enter for the detected resolution]: "
    else:
        prompt = "Enter choice [1-8 or C]: "
    try:
        choice = input("\n" + prompt).strip().upper()
    except EOFError:
        choice = ""
    if not choice and detected:
        return detected
    if choice in PRESETS:
        return PRESETS[choice][1], PRESETS[choice][2]
    if choice == "C":
        return int(input("Width: ").strip()), int(input("Height: ").strip())
    if detected:
        print("Unrecognised choice - using the detected resolution.")
        return detected
    raise SystemExit("No resolution selected.")


def run_install(exe_path, width, height, do_exe, do_files, do_chromatic,
                do_sharpen, restore=False):
    if restore:
        print("\nRestoring everything to stock...")
        patch_exe(exe_path, 16, 9, "stock")
        apply_game_files(exe_path, width, height, restore=True)
        apply_engine_ini(width, height, False, False, remove=True)
        print("\nDone - the game is back to its shipped state.")
        return True

    ok = True
    print("\nInstalling for {}x{} ({:.4f}:1)".format(
        width, height, width / float(height)))

    print("\n[1/3] Ultrawide camera (executable)")
    if do_exe:
        patch_exe(exe_path, width, height, "cine")
    else:
        print("  skipped")

    print("\n[2/3] Full-width UI (game files)")
    if do_files:
        good, missing = game_files_requirements()
        if good:
            ok = apply_game_files(exe_path, width, height) and ok
        else:
            print("  !! skipped - this step also needs:")
            for m in missing:
                print("       - " + m)
            print("     Everything else was still applied.")
            ok = False
    else:
        print("  skipped")

    print("\n[3/3] Display tweaks (Engine.ini)")
    if do_chromatic or do_sharpen:
        apply_engine_ini(width, height, do_chromatic, do_sharpen)
    else:
        print("  skipped")

    print("\n" + "=" * 60)
    print(" Done." if ok else " Done - one step was skipped, see above.")
    print(" Launch the game through Steam.")
    print("=" * 60)
    return ok


def main():
    parser = argparse.ArgumentParser(
        description="Life is Strange: Double Exposure - ultrawide installer")
    parser.add_argument("--exe", help="path to Chronos-Win64-Shipping.exe")
    parser.add_argument("--width", type=int, help="display width, e.g. 5120")
    parser.add_argument("--height", type=int, help="display height, e.g. 2160")
    parser.add_argument("--restore", action="store_true",
                        help="undo everything this installer applied")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="accept all defaults, no prompts")
    parser.add_argument("--no-exe", action="store_true",
                        help="skip the ultrawide camera patch (the executable)")
    parser.add_argument("--no-game-files", action="store_true",
                        help="skip the full-width UI patch (the game data files)")
    parser.add_argument("--no-chromatic-fix", action="store_true",
                        help="skip disabling chromatic aberration")
    parser.add_argument("--no-sharpen", action="store_true",
                        help="skip the recommended anti-blur TSR settings")
    parser.add_argument("--mode", choices=["cine", "horplus", "hybrid", "clean",
                                           "full", "stock"],
                        help="advanced: patch only the executable, in a given mode")
    parser.add_argument("--gate-upper", type=float, metavar="ASPECT",
                        help="advanced: explicit cave A upper bound")
    args = parser.parse_args()

    print("=" * 60)
    print(" Life is Strange: Double Exposure - Ultrawide Fix")
    print("=" * 60)

    exe_path = args.exe or find_exe()
    if not exe_path:
        exe_path = input("Enter path to Chronos-Win64-Shipping.exe: ").strip(" \"'")
    if not os.path.isfile(exe_path):
        print("Error: could not find file at '{}'".format(exe_path))
        sys.exit(1)
    print("Game executable: {}".format(exe_path))

    # advanced escape hatch: executable only, explicit mode
    if args.mode:
        if args.mode != "stock" and not (args.width and args.height):
            print("Error: --mode {} requires --width and --height".format(args.mode))
            sys.exit(1)
        patch_exe(exe_path, args.width or 16, args.height or 9, args.mode,
                  gate_upper_aspect=args.gate_upper)
        return

    detected = detect_resolution()
    if args.width and args.height:
        width, height = args.width, args.height
    elif args.restore:
        width, height = detected or (1920, 1080)   # irrelevant when restoring
    elif args.yes:
        if not detected:
            print("Error: could not detect the display - pass --width and --height")
            sys.exit(1)
        width, height = detected
        print("Display: {}x{} (detected)".format(width, height))
    else:
        width, height = choose_resolution(detected)

    if args.restore:
        run_install(exe_path, width, height, False, False, False, False, restore=True)
        return

    do_exe = not args.no_exe
    do_files = not args.no_game_files
    do_chromatic = not args.no_chromatic_fix
    do_sharpen = not args.no_sharpen

    if not args.yes:
        print("\nWhat to install (all four are recommended):")
        do_exe = ask_yes(
            "\n  Ultrawide camera - Hor+ cutscenes, dialogue and exploration with no\n"
            "  black bars and no zoom when a dialogue ends. Patches the executable.",
            do_exe)
        do_files = ask_yes(
            "\n  Full-width UI - loading screens cover the whole screen and the HUD\n"
            "  sits on the real screen edge. Patches the game's data files.",
            do_files)
        do_chromatic = ask_yes(
            "\n  Disable chromatic aberration - removes the colour fringing that is\n"
            "  most visible at the widened edges. Writes Engine.ini.",
            do_chromatic)
        do_sharpen = ask_yes(
            "\n  Reduce blurriness - recommended TSR settings for this resolution.\n"
            "  Writes Engine.ini.",
            do_sharpen)

    if not any((do_exe, do_files, do_chromatic, do_sharpen)):
        print("\nNothing selected - exiting.")
        return

    run_install(exe_path, width, height, do_exe, do_files, do_chromatic, do_sharpen)


if __name__ == "__main__":
    main()
