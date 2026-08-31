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
optional dependency (blake3, used only by part 2) automatically - though
nothing needs it: without the compiled module part 2 falls back to the pure
Python BLAKE3 in tools/assetdump/, so the stdlib alone is enough.

Advanced: --mode patches only the executable, in one of the legacy modes
(cine, horplus, hybrid, clean, full, stock). cine is the shipped behaviour.
Compatible with Python 3.6+ (3.8+ under uv).
"""

# Date plus build number, the same string as the release tag it ships under
# (tag v2026.08.31.1 -> VERSION 2026.08.31.1). The release build rewrites this
# line, and AssemblyVersion in LiSUltrawidePatcher.cs, with the version it is
# actually publishing - so a version quoted in a bug report names exactly one
# build of the whole fix. The value checked in here is the last release, which
# is what a copy run straight from the repository reports.
VERSION = "2026.08.31.1"

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import struct
import sys

class InstallError(Exception):
    """A problem the user can act on: reported as one line, without a traceback.

    Anything that is NOT this class is a bug in the fix, and keeps its full
    traceback - that is what a useful issue report needs.
    """


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
        raise InstallError(
            "this is not a build of the game the fix knows - the code it "
            "patches ('{}') is not in this executable. After a game update the "
            "fix needs updating too; please report it.".format(spec["name"]))
    raise InstallError(
        "the code site '{}' matches in {} places, so the fix cannot tell which "
        "one to patch. Please report this.".format(spec["name"], len(hits)))

# ---------------------------------------------------------------------------
# Checking an executable
# ---------------------------------------------------------------------------
# The same signatures the patcher writes through also identify what a given
# executable currently is, which is all the front-ends need to show a status:
# every patched site is recognisable in both its stock and its patched form.

# PATCH_AXIS applied: both "cmp dl,<enum>" immediates rewritten to 0xFF
AXIS_PATCHED_SIG = ("3B C1 7E 09 80 FA FF 0F 84 ?? ?? ?? ?? "
                    "80 FA FF 0F 84 ?? ?? ?? ??")
# cave A applied: the flag-copy preamble replaced by "call caveA ; nop2"
GATE_CAVE_SIG = "E8 ?? ?? ?? ?? 66 90 33 47 4C 83 E0 01"
# legacy horplus/clean modes: the same preamble replaced by "xor eax,eax ; nop5"
GATE_HORPLUS_SIG = "31 C0 0F 1F 44 00 00 33 47 4C 83 E0 01"

# offsets inside the cave A blob built by build_cave_a()
CAVE_A_UPPER_AT = 23
CAVE_A_PIN_AT = 35

STOCK_AUTHORED_ASPECT = 16.0 / 9.0


def _matches_at(data, sig, offset):
    pat, mask = parse_sig(sig)
    window = data[offset:offset + len(pat)]
    return len(window) == len(pat) and all(
        (not mask[j]) or window[j] == pat[j] for j in range(len(pat)))


def _find_sig(data, sig, expected=None):
    """Offset of the first match, expected offset first; None if absent."""
    if expected is not None and _matches_at(data, sig, expected):
        return expected
    pat, mask = parse_sig(sig)
    hits = masked_find_all(data, pat, mask, limit=1)
    return hits[0] if hits else None


def _float_at(data, offset):
    try:
        return struct.unpack_from("<f", data, offset)[0]
    except struct.error:
        return None


def _cave_a_gate(data, call_site):
    """Gate bound and pinned aspect of an installed cave A, or (None, None)."""
    rel = struct.unpack_from("<i", data, call_site + 1)[0]
    cave = call_site + 5 + rel
    if cave < 0 or cave + 40 > len(data):
        return None, None
    return (_float_at(data, cave + CAVE_A_UPPER_AT),
            _float_at(data, cave + CAVE_A_PIN_AT))


def _site_state(data, expected, variants):
    """{label: offset} for the one variant a patch site is currently in.

    Every variant is tried at the site's known offset before anything is
    scanned for, so the common cases cost nothing.
    """
    for label, sig in variants:
        if _matches_at(data, sig, expected):
            return {label: expected}
    for label, sig in variants:
        hit = _find_sig(data, sig)
        if hit is not None:
            return {label: hit}
    return {}


def check_exe(exe_path):
    """Classify an executable as (status, detail).

    status is one of:
      "original" - stock, and a build whose code this patcher recognises
      "patched"  - this fix is installed
      "unknown"  - the signatures are not there: a game update, or not the
                   game's executable at all
      "missing"  - nothing readable at that path
    """
    if not exe_path or not os.path.isfile(exe_path):
        return "missing", "there is no file at that path"
    try:
        with open(exe_path, "rb") as f:
            data = f.read()
    except (IOError, OSError) as exc:
        return "missing", "cannot read the file ({})".format(exc)
    if data[:2] != b"MZ":
        return "unknown", "not a Windows executable"

    axis = _site_state(data, PATCH_AXIS["expected"],
                       (("stock", PATCH_AXIS["sig"]),
                        ("patched", AXIS_PATCHED_SIG)))
    gate = _site_state(data, GATE_SITE["expected"],
                       (("stock", GATE_SITE["sig"]),
                        ("cave", GATE_CAVE_SIG),
                        ("horplus", GATE_HORPLUS_SIG)))
    axis_stock, axis_done = axis.get("stock"), axis.get("patched")
    gate_stock = gate.get("stock")
    gate_cave, gate_horplus = gate.get("cave"), gate.get("horplus")

    # legacy clean/full modes only rewrote aspect-ratio constants
    authored = _float_at(data, 0x23E665C)
    constants_touched = (authored is not None
                         and abs(authored - STOCK_AUTHORED_ASPECT) > 1e-6)

    if not (axis_stock or axis_done) or not (gate_stock or gate_cave or gate_horplus):
        return "unknown", ("the code this patcher works on is not in this file - "
                           "a game update, or not the game's executable")

    backup = " (an .original backup is present)" if \
        os.path.exists(exe_path + ".original") else ""

    parts = []
    if axis_done:
        parts.append("forced Hor+ projection branch")
    if gate_cave:
        upper, pin = _cave_a_gate(data, gate_cave)
        if upper:
            # the installer sets the bound to max(aspect, 1.8) * 1.002
            parts.append("aspect gate up to {:.4f} (~{:.2f}:1)".format(
                upper, upper / 1.002))
        else:
            parts.append("aspect gate cave")
        if pin and abs(pin - STOCK_AUTHORED_ASPECT) > 1e-6:
            parts.append("FOV divisor pinned to {:.4f}".format(pin))
    if gate_horplus:
        parts.append("cameras unconstrained (legacy Hor+ mode)")
    if constants_touched:
        parts.append("aspect constants rewritten to {:.4f} (legacy mode)".format(
            authored))
    if parts:
        return "patched", "already patched: " + ", ".join(parts) + backup

    return "original", "stock executable, and a build this patcher knows" + backup


# ---------------------------------------------------------------------------
# Locating the game
# ---------------------------------------------------------------------------

def find_exe():
    """First hit of a layered search; None if the game is nowhere to be found."""
    for path, source in _exe_candidates():
        print("Found game via {}".format(source))
        return path
    return None


STEAM_APPID = "1874000"
GAME_DIR_NAME = "LifeIsStrangeDoubleExposure"
EXE_NAME = "Chronos-Win64-Shipping.exe"
EXE_RELATIVE = os.path.join("Chronos", "Binaries", "Win64", EXE_NAME)


def _exe_candidates():
    """Yield (exe_path, how_it_was_found), most trustworthy first.

    The installer has to work no matter where it is run from, so after the
    obvious places next to it we ask Steam and Epic where the game lives, and
    only then fall back to looking around the usual game folders.
    """
    seen = set()
    for finder in (_local_candidates, _steam_candidates, _epic_candidates,
                   _generic_candidates):
        try:
            for path, source in finder():
                key = os.path.normcase(path)
                if key not in seen:
                    seen.add(key)
                    yield path, source
        except Exception:        # a broken launcher install must not be fatal
            continue


def _exe_under(game_root):
    """<game_root>/Chronos/Binaries/Win64/<exe>, if that file exists."""
    if not game_root:
        return None
    path = os.path.join(game_root, EXE_RELATIVE)
    return os.path.abspath(path) if os.path.isfile(path) else None


def _child(parent, name):
    """parent/name, matched case-insensitively (Linux has SteamApps/steamapps)."""
    direct = os.path.join(parent, name)
    if os.path.exists(direct):
        return direct
    try:
        entries = os.listdir(parent)
    except OSError:
        return direct
    for entry in entries:
        if entry.lower() == name.lower():
            return os.path.join(parent, entry)
    return direct


def _looks_like_game(name):
    return "doubleexposure" in re.sub(r"[^a-z0-9]", "", name.lower())


def _local_candidates():
    """Next to the installer, next to the shell's cwd, or one level up."""
    relatives = [EXE_NAME, EXE_RELATIVE,
                 os.path.join("..", EXE_RELATIVE),
                 os.path.join("..", "..", EXE_RELATIVE)]
    bases = ((os.path.dirname(os.path.abspath(__file__)), "the installer's own folder"),
             (os.getcwd(), "the current folder"))
    for base, label in bases:
        for rel in relatives:
            path = os.path.join(base, rel)
            if os.path.isfile(path):
                yield os.path.abspath(path), label


def _fixed_drives():
    if os.name != "nt":
        return ["/"]
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        mask = k32.GetLogicalDrives()
        drives = []
        for i in range(26):
            if not mask & (1 << i):
                continue
            root = "{}:\\".format(chr(ord("A") + i))
            if k32.GetDriveTypeW(root) == 3:          # DRIVE_FIXED
                drives.append(root)
        return drives
    except Exception:
        return [d for d in ("C:\\", "D:\\", "E:\\", "F:\\") if os.path.isdir(d)]


def _steam_installs():
    """Every Steam installation this machine knows about."""
    roots, seen = [], set()

    def add(path):
        if path and os.path.isdir(path) and os.path.normcase(path) not in seen:
            seen.add(os.path.normcase(path))
            roots.append(path)

    if os.name == "nt":
        try:
            import winreg
        except ImportError:
            winreg = None
        if winreg:
            keys = ((winreg.HKEY_CURRENT_USER, "Software\\Valve\\Steam"),
                    (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\WOW6432Node\\Valve\\Steam"),
                    (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\Valve\\Steam"))
            for hive, key in keys:
                try:
                    handle = winreg.OpenKey(hive, key)
                except OSError:
                    continue
                for value in ("SteamPath", "InstallPath"):
                    try:
                        add(winreg.QueryValueEx(handle, value)[0])
                    except OSError:
                        pass
                handle.Close()
    home = os.path.expanduser("~")
    for path in (os.path.join(home, ".steam", "steam"),
                 os.path.join(home, ".steam", "root"),
                 os.path.join(home, ".local", "share", "Steam"),
                 os.path.join(home, ".var", "app", "com.valvesoftware.Steam",
                              "data", "Steam"),
                 os.path.join(home, "Library", "Application Support", "Steam")):
        add(path)
    for drive in _fixed_drives():
        for rel in (os.path.join("Program Files (x86)", "Steam"),
                    os.path.join("Program Files", "Steam"), "Steam"):
            add(os.path.join(drive, rel))
    return roots


def _steam_libraries():
    """Steam installations plus every library folder they have registered."""
    libraries = _steam_installs()
    seen = set(os.path.normcase(p) for p in libraries)
    for install in list(libraries):
        vdf = os.path.join(_child(install, "steamapps"), "libraryfolders.vdf")
        try:
            with io.open(vdf, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except (IOError, OSError):
            continue
        # current format keys each entry "path"; the pre-2021 one used numbers
        found = re.findall('"path"\\s*"([^"]+)"', text)
        found += re.findall('"[0-9]+"\\s*"([^"]{3,})"', text)
        for raw in found:
            path = raw.replace("\\\\", "\\")
            if os.path.isdir(path) and os.path.normcase(path) not in seen:
                seen.add(os.path.normcase(path))
                libraries.append(path)
    return libraries


def _steam_candidates():
    for library in _steam_libraries():
        apps = _child(library, "steamapps")
        common = _child(apps, "common")
        if not os.path.isdir(common):
            continue
        names = []
        manifest = os.path.join(apps, "appmanifest_{}.acf".format(STEAM_APPID))
        try:
            with io.open(manifest, "r", encoding="utf-8", errors="replace") as f:
                match = re.search('"installdir"\\s*"([^"]+)"', f.read())
            if match:
                names.append(match.group(1))
        except (IOError, OSError):
            pass
        names.append(GAME_DIR_NAME)
        try:
            names += [d for d in os.listdir(common) if _looks_like_game(d)]
        except OSError:
            pass
        for name in names:
            exe = _exe_under(os.path.join(common, name))
            if exe:
                yield exe, "Steam library {}".format(library)


def _epic_candidates():
    manifests = os.path.join(os.environ.get("PROGRAMDATA", "C:\\ProgramData"),
                             "Epic", "EpicGamesLauncher", "Data", "Manifests")
    try:
        entries = os.listdir(manifests)
    except OSError:
        return
    for entry in entries:
        if not entry.lower().endswith(".item"):
            continue
        try:
            with io.open(os.path.join(manifests, entry), "r",
                         encoding="utf-8", errors="replace") as f:
                info = json.load(f)
        except (IOError, OSError, ValueError):
            continue
        exe = _exe_under(info.get("InstallLocation"))
        if exe:
            yield exe, "the Epic Games Launcher"


def _generic_candidates():
    """The usual places a game folder ends up when no launcher claims it."""
    for drive in _fixed_drives():
        roots = [drive]
        for rel in ("Games", "Program Files", "Program Files (x86)",
                    "GOG Games", "Epic Games",
                    os.path.join("SteamLibrary", "steamapps", "common"),
                    os.path.join("Games", "steamapps", "common")):
            roots.append(os.path.join(drive, rel))
        for root in roots:
            exe = _exe_under(os.path.join(root, GAME_DIR_NAME))
            if exe:
                yield exe, root
            try:
                entries = os.listdir(root)
            except OSError:
                continue
            for entry in entries:
                if not _looks_like_game(entry):
                    continue
                exe = _exe_under(os.path.join(root, entry))
                if exe:
                    yield exe, root


# ---------------------------------------------------------------------------
# Patching
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Backups, and the build they belong to
# ---------------------------------------------------------------------------
# A backup is only a backup of the game you have right now. Steam replaces the
# executable and the containers on every game update, and writing the previous
# build's files back over the new ones would quietly downgrade the game - or,
# for the 20 GB container, wreck it. So before anything is restored from a
# backup, the backup is checked against the build that is actually installed.
#
# The executable needs no bookkeeping for this: the fix only ever rewrites bytes
# in place, so a stock executable and that same executable after the fix has run
# share their size and their PE header. A build the backup does not belong to
# differs there.


def build_identity(path):
    """(file size, PE timestamp, image size) - or None if it is not a PE file.

    Unchanged by this patcher, changed by a game update: which is exactly what
    makes it a usable "is this still the same build?" test.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(0x400)
            f.seek(0, 2)
            size = f.tell()
    except (IOError, OSError):
        return None
    if len(head) < 0x40 or head[:2] != b"MZ":
        return None
    pe = struct.unpack_from("<I", head, 0x3C)[0]
    if pe + 0x60 > len(head) or head[pe:pe + 4] != b"PE\0\0":
        return None
    return (size,
            struct.unpack_from("<I", head, pe + 8)[0],        # TimeDateStamp
            struct.unpack_from("<I", head, pe + 24 + 56)[0])  # SizeOfImage


def backup_state(exe_path):
    """-> ("none" | "valid" | "stale", backup path)."""
    backup = exe_path + ".original"
    if not os.path.isfile(backup):
        return "none", backup
    mine, theirs = build_identity(exe_path), build_identity(backup)
    if mine is None or theirs is None:
        return "stale", backup
    return ("valid" if mine == theirs else "stale"), backup


def archive_stale(backup):
    """Move a backup of some other build aside - never delete it. -> new path."""
    for n in range(1, 100):
        target = "{}.old{}".format(backup, "" if n == 1 else n)
        if not os.path.exists(target):
            try:
                os.rename(backup, target)
            except (IOError, OSError) as ex:
                raise InstallError(
                    "{} was taken from a different build of the game and has to "
                    "be moved aside, but that failed ({}). Move or delete it by "
                    "hand and run this again."
                    .format(os.path.basename(backup), ex))
            return target
    raise InstallError("there are too many old backups next to {} - please "
                       "tidy them up.".format(os.path.basename(backup)))


VERIFY_HINT = ("Use Steam's 'Verify Integrity of Game Files' (right-click the "
               "game, Properties, Installed Files) to put a stock executable "
               "back, then run this again.")


def ensure_exe_backup(exe_path):
    """A backup that really is the stock executable of the installed build.

    This refuses rather than guesses: a backup taken from an already-patched
    executable would make Restore a no-op for ever, and one left over from a
    previous build would downgrade the game on the next install.
    """
    state, backup = backup_state(exe_path)
    if state == "valid":
        return backup

    status = check_exe(exe_path)[0]
    if state == "stale":
        if status != "original":
            raise InstallError(
                "the backup next to the game was taken from a different build "
                "of it, and this executable is not a stock one either, so there "
                "is nothing safe to patch from. " + VERIFY_HINT)
        archived = archive_stale(backup)
        print("The backup belonged to an older build of the game - set aside as "
              "{} and re-taken.".format(os.path.basename(archived)))
    elif status == "patched":
        raise InstallError(
            "this executable is already patched but its .original backup is "
            "gone, so there are no stock bytes left to patch from. "
            + VERIFY_HINT)

    try:
        shutil.copy2(exe_path, backup)
    except (IOError, OSError) as ex:
        raise InstallError(write_failure(backup, ex))
    print("Created original backup: {}".format(os.path.basename(backup)))
    return backup


def restore_exe(exe_path):
    """Put the stock executable back, or explain why that cannot be done."""
    state, backup = backup_state(exe_path)

    if state == "valid":
        print("\nRestoring the original stock 16:9 executable...")
        with open(backup, "rb") as f:
            write_exe(exe_path, f.read())
        return True

    if check_exe(exe_path)[0] == "original":
        if state == "stale":
            archive_stale(backup)
        print("\nThe executable is already the stock one - nothing to restore.")
        return True

    raise InstallError(
        ("the backup next to the game belongs to a different build of it"
         if state == "stale" else
         "there is no .original backup next to the game")
        + ", and this executable is not stock, so the fix cannot restore it. "
        + VERIFY_HINT)


def write_failure(path, ex):
    """Turn a write error into something the person at the keyboard can fix."""
    win = getattr(ex, "winerror", None)
    errno = getattr(ex, "errno", None)
    if win == 32:
        return ("{} is in use - close the game (and Steam) and try again."
                .format(os.path.basename(path)))
    if win == 5 or errno == 13:
        return ("Windows refused permission to write {}. Close the game, and if "
                "it is installed under Program Files, run this installer as "
                "administrator.".format(path))
    if win == 112 or errno == 28:
        return "the drive holding {} is full.".format(path)
    return "could not write {} ({}).".format(path, ex)


def write_exe(exe_path, data):
    """Write through a temporary file, so a failure never leaves half an exe."""
    tmp_path = exe_path + ".tmp"
    try:
        with open(tmp_path, "wb") as f:
            f.write(data)
        os.replace(tmp_path, exe_path)
    except (IOError, OSError) as ex:
        try:
            if os.path.isfile(tmp_path):
                os.remove(tmp_path)
        except (IOError, OSError):
            pass
        raise InstallError(write_failure(exe_path, ex))
    print("Successfully updated {}!".format(os.path.basename(exe_path)))


def patch_exe(exe_path, width, height, mode, gate_upper_aspect=None):
    if mode == "stock":
        return restore_exe(exe_path)

    backup_path = ensure_exe_backup(exe_path)
    # Always start from the checked backup, so modes and re-runs never stack.
    with open(backup_path, "rb") as f:
        data = bytearray(f.read())

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

    write_exe(exe_path, data)

    # The exe patch is self-contained: disable SUWSF so it cannot re-apply
    # in-memory aspect patches on top (it would poison Hor+ mode's math).
    ini_path = os.path.join(os.path.dirname(exe_path), "SUWSF.ini")
    try:
        if os.path.isfile(ini_path):
            with open(ini_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            if "Enabled=true" in content:
                content = content.replace("Enabled=true", "Enabled=false")
                with open(ini_path, "w", encoding="utf-8") as f:
                    f.write(content)
                print("Disabled conflicting SUWSF.ini in-memory patches.")
    except (IOError, OSError) as ex:
        print("  note: could not disable SUWSF.ini ({}) - if you have that "
              "tool, turn it off by hand.".format(ex))

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
    try:
        parent = os.path.dirname(path)
        if not os.path.isdir(parent):
            os.makedirs(parent)
        with io.open(path, "w", encoding="utf-8") as f:
            f.write(new)
    except (IOError, OSError) as ex:
        raise InstallError(write_failure(path, ex))
    print("  {} {}".format("removed the managed block from" if remove else "wrote", path))
    return True


# ---------------------------------------------------------------------------
# Oodle decompressor acquisition
# ---------------------------------------------------------------------------
# The game's containers are 97% Oodle-compressed and Oodle ships *statically
# linked* inside the game executable, so reading a package needs a standalone
# decompressor. It cannot be bundled here (proprietary, and redistributing it
# is governed by the Unreal Engine EULA), so it is located or fetched instead:
#
#   1. one already sitting in tools/assetdump/ or next to this script;
#   2. one shipped by another Unreal Engine game on this machine - most UE
#      titles carry oo2core_*_win64.dll, and it exports the same entry point;
#   3. failing that, Epic's Oodle-for-UE source build, downloaded automatically.
#
# Only step 3 touches the network, and only when the full-width UI step is
# actually going to run and steps 1-2 came up empty. --no-fetch-oodle turns
# that download off; the step is then skipped and reported.

# Pinned, not "latest": this downloads a binary that then runs on the user's
# machine, so it has to be a known one. The hash is of the DLL inside the zip
# (the zip itself is repacked by the build that made it), and a mismatch means
# the download is refused, never used. To move to a newer build, change both
# lines together and check the new hash by hand.
OODLE_RELEASE = "2026-06-04-1357"
OODLE_ZIP_URL = ("https://github.com/WorkingRobot/OodleUE/releases/download/"
                 "{}/msvc-x64-release.zip".format(OODLE_RELEASE))
OODLE_DLL_SHA256 = \
    "1f28ffecd7ad1b75be89ea5a85ad74b4e7f998994d7dcf5f69eddfd3bca4aeb2"

OODLE_NAMES = ("oodle-data-shared.dll", "oo2core_9_win64.dll",
               "oo2core_8_win64.dll", "liboodle-data-shared.so")


def user_cache_dir():
    """Per-user store for downloads, for when the fix's own folder is read-only."""
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        root = os.path.join(os.path.expanduser("~"), "Library", "Caches")
    else:
        root = (os.environ.get("XDG_CACHE_HOME")
                or os.path.join(os.path.expanduser("~"), ".cache"))
    return os.path.join(root, "LiSUltrawideFix")


def writable_dir(path):
    """True if a file can be created in path, creating the directory if needed."""
    try:
        if not os.path.isdir(path):
            os.makedirs(path)
        probe = os.path.join(path, ".write-probe")
        with open(probe, "wb"):
            pass
        os.remove(probe)
        return True
    except (IOError, OSError):
        return False


def oodle_dir(for_writing=False):
    """Where the DLL goes: beside the code that loads it, or - when the fix was
    unpacked somewhere read-only, Program Files or a shared drive - a per-user
    cache instead. Reads look in both; only writes need to pick one."""
    beside = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "tools", "assetdump")
    if not for_writing or writable_dir(beside):
        return beside
    return os.path.join(user_cache_dir(), "assetdump")


def find_oodle(exe_path=None):
    """-> path to a usable Oodle DLL, or None. Never touches the network."""
    import glob
    here = os.path.dirname(os.path.abspath(__file__))
    for d in (oodle_dir(), os.path.join(user_cache_dir(), "assetdump"), here):
        for n in OODLE_NAMES:
            p = os.path.join(d, n)
            if os.path.isfile(p):
                return p
    if not exe_path:
        return None
    # <library>/<game>/Chronos/Binaries/Win64/x.exe -> <library>
    library = os.path.abspath(exe_path)
    for _ in range(5):
        library = os.path.dirname(library)
    patterns = [
        os.path.join(library, "*", "Engine", "Binaries", "ThirdParty",
                     "Oodle", "*", "*", "*.dll"),
        os.path.join(library, "*", "Engine", "Binaries", "ThirdParty",
                     "Oodle", "*", "*.dll"),
        os.path.join(library, "*", "*", "Binaries", "Win64", "oo2core*.dll"),
    ]
    for pattern in patterns:
        for hit in sorted(glob.glob(pattern)):
            base = os.path.basename(hit).lower()
            if base.startswith("oo2core") or "oodle" in base:
                return hit
    return None


def fetch_oodle():
    """Download Epic's Oodle-for-UE build and keep the decompressor. -> path/None."""
    import zipfile
    try:
        from urllib.request import urlopen, Request
    except ImportError:                                   # Python 2 safety net
        from urllib2 import urlopen, Request

    dest_dir = oodle_dir(for_writing=True)
    zip_path = os.path.join(dest_dir, "_oodle_download.zip")
    try:
        if not os.path.isdir(dest_dir):
            os.makedirs(dest_dir)
        print("  downloading {} ...".format(OODLE_ZIP_URL))
        req = Request(OODLE_ZIP_URL, headers={"User-Agent": "LiSUltrawideFix"})
        data = urlopen(req).read()
        with open(zip_path, "wb") as f:
            f.write(data)
        print("  {:.1f} MB downloaded, extracting...".format(len(data) / 1e6))

        with zipfile.ZipFile(zip_path) as z:
            wanted = None
            for name in z.namelist():
                base = name.rsplit("/", 1)[-1].lower()
                if base == "oodle-data-shared.dll":
                    wanted = name
                    break
                if wanted is None and (base.startswith("oo2core")
                                       and base.endswith(".dll")):
                    wanted = name
            if wanted is None:
                print("  !! the archive contained no Oodle DLL")
                return None
            with z.open(wanted) as src:
                payload = src.read()
        digest = hashlib.sha256(payload).hexdigest()
        if digest != OODLE_DLL_SHA256:
            print("  !! the downloaded decompressor is not the expected one")
            print("     (sha256 {} - expected {})".format(digest, OODLE_DLL_SHA256))
            print("     Refusing to use it. Nothing else was affected.")
            return None
        out = os.path.join(dest_dir, "oodle-data-shared.dll")
        with open(out, "wb") as dst:
            dst.write(payload)
        print("  Oodle ready (sha256 verified): {}".format(out))
        return out
    except Exception as ex:
        print("  !! download failed: {}".format(ex))
        print("     Fetch it manually - see tools/assetdump/README.md")
        return None
    finally:
        try:
            if os.path.isfile(zip_path):
                os.remove(zip_path)
        except Exception:
            pass


def ensure_oodle(exe_path, allow_fetch=True):
    """Locate Oodle, downloading it when there is none. -> path or None."""
    found = find_oodle(exe_path)
    if found:
        return found
    if not allow_fetch:
        print("  no Oodle decompressor found, and --no-fetch-oodle was given")
        return None
    print("\n  No Oodle decompressor on this machine - fetching Epic's")
    print("  Oodle-for-UE build {} (~7 MB, once):".format(OODLE_RELEASE))
    return fetch_oodle()

# ---------------------------------------------------------------------------
# Game-file (UI layout) patch - delegates to tools/assetdump/patch_ui_layout.py
# ---------------------------------------------------------------------------

def paks_dir_for(exe_path):
    """<game>/Chronos/Binaries/Win64/x.exe -> <game>/Chronos/Content/Paks"""
    win64 = os.path.dirname(os.path.abspath(exe_path))
    chronos = os.path.dirname(os.path.dirname(win64))
    return os.path.join(chronos, "Content", "Paks")


def apply_game_files(exe_path, width, height, restore=False, oodle_dll=None):
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(here, "tools", "assetdump", "patch_ui_layout.py")
    if not os.path.isfile(script):
        print("  !! {} not found - skipping".format(script))
        return False
    cmd = [sys.executable, script, "--paks", paks_dir_for(exe_path)]
    cmd += ["--restore"] if restore else ["--width", str(width), "--height", str(height)]
    env = dict(os.environ)
    if oodle_dll:
        env["LISDE_OODLE_DLL"] = oodle_dll
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, env=env)
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
                do_sharpen, restore=False, fetch_oodle_ok=True):
    if restore:
        print("\nRestoring everything to stock...")
        ok = True
        if do_exe:
            patch_exe(exe_path, 16, 9, "stock")
        if do_files:
            ok = apply_game_files(exe_path, width, height, restore=True,
                                  oodle_dll=find_oodle(exe_path)) and ok
        if do_chromatic or do_sharpen:
            apply_engine_ini(width, height, False, False, remove=True)
        print("\nDone - the game is back to its shipped state." if ok else
              "\nDone - one part could not be undone, see above.")
        return ok

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
        oodle = ensure_oodle(exe_path, fetch_oodle_ok)
        if oodle:
            print("  using Oodle: {}".format(oodle))
            ok = apply_game_files(exe_path, width, height, oodle_dll=oodle) and ok
        else:
            print("  !! skipped - no Oodle decompressor is available.")
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


def run():
    parser = argparse.ArgumentParser(
        description="Life is Strange: Double Exposure - ultrawide installer")
    parser.add_argument("--version", action="version",
                        version="LiS:DE Ultrawide Fix " + VERSION)
    parser.add_argument("--exe", help="path to Chronos-Win64-Shipping.exe "
                                      "(found automatically when omitted)")
    parser.add_argument("--find-exe", action="store_true",
                        help="only report where the game was found, then exit")
    parser.add_argument("--check-exe", action="store_true",
                        help="only report whether the executable is stock, "
                             "already patched or unrecognised, then exit")
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
    parser.add_argument("--no-fetch-oodle", action="store_true",
                        help="never download the Oodle decompressor the full-width "
                             "UI step needs; skip that step instead")
    parser.add_argument("--fetch-oodle", action="store_true",
                        help=argparse.SUPPRESS)      # accepted; now the default
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
    print(" Life is Strange: Double Exposure - Ultrawide Fix v" + VERSION)
    print("=" * 60)

    exe_path = args.exe or find_exe()
    if not exe_path:
        print("Could not find the game automatically (searched next to this "
              "script, every Steam library, the Epic Games Launcher and the "
              "usual game folders).")
        if args.find_exe:
            sys.exit(1)
        exe_path = input("Enter path to Chronos-Win64-Shipping.exe: ").strip(" \"'")
    if not os.path.isfile(exe_path):
        print("Error: could not find file at '{}'".format(exe_path))
        sys.exit(1)
    print("Game executable: {}".format(exe_path))
    if args.find_exe:
        return

    status, detail = check_exe(exe_path)
    if args.check_exe:                       # machine-readable, for the GUI
        print("status: {}".format(status))
        print("detail: {}".format(detail))
        return
    print("Executable: {}".format(detail))

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
        run_install(exe_path, width, height,
                    not args.no_exe, not args.no_game_files,
                    not args.no_chromatic_fix, not args.no_sharpen, restore=True)
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

    run_install(exe_path, width, height, do_exe, do_files, do_chromatic,
                do_sharpen, fetch_oodle_ok=not args.no_fetch_oodle)


def main():
    """Expected problems get one line; anything else keeps its traceback."""
    try:
        run()
    except InstallError as ex:
        print("\nError: {}".format(ex))
        print("\nNothing was left half-applied - the game is as it was before "
              "this run.")
        sys.exit(2)
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(130)


if __name__ == "__main__":
    main()
