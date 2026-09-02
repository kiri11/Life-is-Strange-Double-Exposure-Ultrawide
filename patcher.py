#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# dependencies = ["blake3"]
# ///
#
# Copyright (C) 2026 Kiri11.  Free software under the GNU General Public
# License, version 3 or later - see LICENSE for the full terms.
#
# Additional term under GPL-3 section 7(b): every copy or modified version,
# in source or binary form, must preserve this notice and credit the
# original author, Kiri11, with a link to the original project at
# https://github.com/kiri11/Life-is-Strange-Double-Exposure-Ultrawide.
"""
Life is Strange: Double Exposure - Ultrawide Fix installer.

Run it with no arguments for an interactive install, or:

    uv run patcher.py --yes            # everything, auto-detected resolution
    python patcher.py --width 5120 --height 2160 --yes
    python patcher.py --restore        # undo everything

Four independent parts; the first three are on by default, part 4 is opt-in
(--sharpen):

  1. Ultrawide camera   - installs a loader library next to the executable
                          (crates/camera; see RESEARCH.md)
  2. Full-width UI      - patches the game data files via tools/assetdump
  3. Chromatic aberration off } both write a single managed block into the
  4. Anti-blur TSR settings   } user's Engine.ini, removed again by --restore

`uv run` is the easiest entry point: it needs no venv and fetches the one
optional dependency (blake3, used only by part 2) automatically - though
nothing needs it: without the compiled module part 2 falls back to the pure
Python BLAKE3 in tools/assetdump/, so the stdlib alone is enough.

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
import io
import json
import os
import re
import shutil
import struct
import sys
import time

class InstallError(Exception):
    """A problem the user can act on: reported as one line, without a traceback.

    Anything that is NOT this class is a bug in the fix, and keeps its full
    traceback - that is what a useful issue report needs.
    """


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
                 os.path.join(home, "snap", "steam", "common", ".local", "share", "Steam"),
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
# Ultrawide camera - the loader library
# ---------------------------------------------------------------------------
# The camera fix is applied to the game's code in memory, at every launch, by
# a small library the game loads by itself. It is installed as winhttp.dll
# next to the game executable: the game imports a DLL of that name, and
# Windows looks for it in the game's own folder first. (Not version.dll, the
# usual choice for this: Windows' compatibility shim engine, active as soon
# as a player sets any compatibility option on the executable, loads the
# System32 version.dll before the game's imports are resolved, and the game
# then reuses that copy.) The library forwards the real winhttp.dll's
# functions to the system copy and, before the game's own code runs, finds
# the three patch sites by signature and writes the bytes RESEARCH.md
# describes. The executable on disk is never modified, so Steam's
# Verify Integrity, game updates and reinstalls leave the fix in place, and
# there is nothing to back up or restore.
#
# The library reports what it did in LiSUltrawideCamera.log next to itself.
# That is the only place the installer can learn whether the game's build is
# one the fix knows: the signatures live in the library (crates/camera), not
# here.

DLL_SHIPPED = "LiSUltrawideCamera.dll"      # in the fix's own folder
# The name the loader is installed under. One, on purpose: a second name for
# when another mod holds this one would have to pass the load-order check
# that ruled version.dll out first. The loader is ready for it (one forward!
# block per name in crates/camera/src/forward.rs), and the refusal below
# names the other mod, so a report says what collided.
DLL_INSTALLED = "winhttp.dll"               # next to the game executable
DLL_MARKER = "LiSUltrawideCamera".encode("utf-16-le")  # in its version resource
CAMERA_INI = "LiSUltrawideCamera.ini"
CAMERA_LOG = "LiSUltrawideCamera.log"
# The per-application override winecfg would write, as it appears in user.reg.
WINE_OVERRIDE_KEY = "Software\\\\Wine\\\\AppDefaults\\\\" + EXE_NAME + "\\\\DllOverrides"
WINE_OVERRIDE_VALUE = '"{}"="native,builtin"'.format(DLL_INSTALLED[:-4])

VERIFY_HINT = ("Use Steam's 'Verify Integrity of Game Files' (right-click the "
               "game, Properties, Installed Files) to put a stock executable "
               "back, then run this again.")


def shipped_dll():
    """The loader next to this script, or None if the download is incomplete."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), DLL_SHIPPED)
    return path if os.path.isfile(path) else None


def is_our_dll(path):
    """Does this file carry the loader's name in its version resource?

    A winhttp.dll next to the game could also be some other mod's loader, and
    the fix must neither overwrite nor delete that one.
    """
    try:
        with open(path, "rb") as f:
            return DLL_MARKER in f.read()
    except (IOError, OSError):
        return False


def describe_dll(path):
    """What a DLL's version resource says it is - "Ultimate ASI Loader
    (ThirteenAG)" - or None when it says nothing. Each string in the resource
    is UTF-16 and sits right after its key, padded to four bytes, so this needs
    no walk of the resource tree."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except (IOError, OSError):
        return None

    def string(key):
        needle = key.encode("utf-16-le") + b"\0\0"
        at = data.find(needle)
        if at < 0:
            return None
        at += len(needle)
        while data[at:at + 2] == b"\0\0":          # alignment padding
            at += 2
        chars = []
        while len(chars) < 100 and data[at:at + 2] not in (b"", b"\0\0"):
            chars.append(data[at:at + 2])
            at += 2
        text = b"".join(chars).decode("utf-16-le", "replace").strip()
        return text if text and text.isprintable() else None

    what = string("FileDescription") or string("ProductName")
    company = string("CompanyName")
    if what and company and company not in what:
        return "{} ({})".format(what, company)
    return what or company


def same_bytes(a, b):
    try:
        if os.path.getsize(a) != os.path.getsize(b):
            return False
        with open(a, "rb") as fa, open(b, "rb") as fb:
            while True:
                x, y = fa.read(1 << 20), fb.read(1 << 20)
                if x != y:
                    return False
                if not x:
                    return True
    except (IOError, OSError):
        return False


def camera_paths(exe_path):
    """(installed loader, its ini, its log), all next to the game executable."""
    win64 = os.path.dirname(os.path.abspath(exe_path))
    return (os.path.join(win64, DLL_INSTALLED), os.path.join(win64, CAMERA_INI),
            os.path.join(win64, CAMERA_LOG))


def last_launch(log_path):
    """The loader's verdict from the last launch, or None if it has not run."""
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = [line.strip() for line in f if line.strip()]
    except (IOError, OSError):
        return None
    for line in reversed(lines):
        if line.startswith(("applied", "not applied")):
            return line
    return lines[-1] if lines else None


def check_camera(exe_path):
    """Classify the camera part as (status, detail). status is one of:

      "installed" - the loader next to the game is the one shipped here
      "outdated"  - it is this fix's loader, but not the version shipped here
      "foreign"   - some other program's winhttp.dll is there; never touched
      "none"      - not installed
      "notgame"   - the path is not the game's executable
      "missing"   - nothing readable at that path
    """
    if not exe_path or not os.path.isfile(exe_path):
        return "missing", "there is no file at that path"
    if os.path.basename(exe_path).lower() != EXE_NAME.lower():
        return "notgame", "that is not {} - select the game's own executable".format(EXE_NAME)
    dll, ini, log = camera_paths(exe_path)
    if not os.path.isfile(dll):
        return "none", "not installed"
    if not is_our_dll(dll):
        what = describe_dll(dll)
        return "foreign", ("another program's {}{} is next to the game, and the "
                           "fix will not replace it".format(
                               DLL_INSTALLED, " ({})".format(what) if what else ""))
    launch = last_launch(log)
    tail = (" - last launch: " + launch) if launch \
        else " - the game has not been started since"
    shipped = shipped_dll()
    if shipped and not same_bytes(dll, shipped):
        return "outdated", "a different version of the loader is installed" + tail
    return "installed", "loader installed" + tail


def build_identity(path):
    """(file size, PE timestamp, image size) - or None if it is not a PE file.

    What tells one build of the game from another: the old in-place patch kept
    both, so it says whether a backup belongs to the executable next to it.
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


def write_failure(path, ex):
    """Turn a write error into something the person at the keyboard can fix."""
    win = getattr(ex, "winerror", None)
    errno = getattr(ex, "errno", None)
    if win == 32:
        return ("{} is in use - close the game (and Steam) and try again."
                .format(os.path.basename(path)))
    if win == 5 or errno == 13:
        return ("The system refused permission to write {}. Close the game, and if "
                "it is installed under Program Files, run this installer as "
                "administrator.".format(path))
    if win == 112 or errno == 28:
        return "the drive holding {} is full.".format(path)
    return "could not write {} ({}).".format(path, ex)


def replace_file(path, data):
    """Write through a temporary file, so a failure never leaves half a file."""
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "wb") as f:
            f.write(data)
        os.replace(tmp_path, path)
    except (IOError, OSError) as ex:
        try:
            if os.path.isfile(tmp_path):
                os.remove(tmp_path)
        except (IOError, OSError):
            pass
        raise InstallError(write_failure(path, ex))


def retire_exe_patch(exe_path):
    """Undo what versions before 2026.09 did to the executable itself.

    Those versions edited Chronos-Win64-Shipping.exe in place and kept the
    stock file next to it as .exe.original. The stock file goes back and the
    backup goes, because the loader patches the game in memory and refuses an
    executable that is already patched on disk.
    """
    backup = exe_path + ".original"
    if not os.path.isfile(backup):
        return
    name = os.path.basename(backup)
    theirs = build_identity(backup)
    if theirs is None or theirs != build_identity(exe_path):
        print("  note: {} belongs to a different build of the game; it is not "
              "needed any more and can be deleted".format(name))
        return
    if same_bytes(exe_path, backup):
        print("  the executable is already the stock one")
    else:
        print("  an older version of this fix edited the executable - putting "
              "the stock file back from {}".format(name))
        with open(backup, "rb") as f:
            replace_file(exe_path, f.read())
    try:
        os.remove(backup)
        print("  removed {} - the loader needs no backup".format(name))
    except (IOError, OSError) as ex:
        print("  note: could not remove {} ({}) - it can be deleted by hand"
              .format(name, ex))


def disable_suwsf(exe_path):
    """SUWSF would re-apply in-memory aspect patches on top of the fix and
    poison its Hor+ maths; if it is there, it is switched off."""
    ini_path = os.path.join(os.path.dirname(exe_path), "SUWSF.ini")
    try:
        if os.path.isfile(ini_path):
            with open(ini_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            if "Enabled=true" in content:
                content = content.replace("Enabled=true", "Enabled=false")
                with open(ini_path, "w", encoding="utf-8") as f:
                    f.write(content)
                print("  disabled conflicting SUWSF.ini in-memory patches")
    except (IOError, OSError) as ex:
        print("  note: could not disable SUWSF.ini ({}) - if you have that "
              "tool, turn it off by hand.".format(ex))


def wine_prefix(exe_path, engine_ini=None):
    """The Proton or Wine prefix the game runs in: --engine-ini's, else the
    one Steam keeps for the game. None before the game has been started once.
    """
    if engine_ini:
        path = os.path.abspath(engine_ini)
        while True:
            parent = os.path.dirname(path)
            if parent == path:
                return None
            if os.path.basename(path).lower() == "drive_c":
                return parent
            path = parent
    libraries = []
    library = _library_of(exe_path)
    if library:
        libraries.append(library)
    libraries += _steam_libraries()
    for library in libraries:
        pfx = _proton_prefix(library)
        if os.path.isfile(os.path.join(pfx, "user.reg")):
            return pfx
    return None


def set_wine_dll_override(text, remove=False):
    """user.reg with winhttp.dll set to native for the game, or without that.

    Wine loads its own winhttp.dll unless told otherwise. This is the
    per-application override winecfg would write, scoped to the game's
    executable so nothing else in the prefix is affected. The file is
    line-based: a key is a "[path] time" line, then its values, up to a blank
    line; everything else is left exactly as it was.
    """
    lines = text.split("\n")
    header = "[" + WINE_OVERRIDE_KEY + "]"
    start = None
    for i, line in enumerate(lines):
        if line.startswith(header) and line[len(header):len(header) + 1] in ("", " "):
            start = i
            break
    if start is None:
        if remove:
            return text
        if text and not text.endswith("\n"):
            text += "\n"
        return (text + "\n" + header + " " + str(int(time.time())) + "\n"
                + WINE_OVERRIDE_VALUE + "\n")
    end = start + 1
    while end < len(lines) and lines[end] != "" and not lines[end].startswith("["):
        end += 1
    body = [l for l in lines[start + 1:end] if not l.startswith('"winhttp"=')]
    if remove:
        if any(l.startswith('"') for l in body):
            lines[start:end] = [lines[start]] + body
        else:
            # the key held nothing else: drop it and the blank line after it
            if end < len(lines) and lines[end] == "":
                end += 1
            del lines[start:end]
        return "\n".join(lines)
    if WINE_OVERRIDE_VALUE in lines[start + 1:end]:
        return text
    lines[start:end] = [lines[start]] + body + [WINE_OVERRIDE_VALUE]
    return "\n".join(lines)


def game_is_running():
    """Linux: is the game's process alive? Wine writes its registry back when
    it shuts down, over anything changed in the file while it ran."""
    try:
        pids = [pid for pid in os.listdir("/proc") if pid.isdigit()]
    except (IOError, OSError):
        return False
    needle = EXE_NAME.lower().encode()
    for pid in pids:
        try:
            with open("/proc/{}/cmdline".format(pid), "rb") as f:
                if needle in f.read().lower():
                    return True
        except (IOError, OSError):
            continue                # gone since the listing, or not ours to read
    return False


def wine_dll_override(exe_path, engine_ini=None, remove=False):
    """Tell the game's Wine prefix to load the fix's winhttp.dll, or stop."""
    pfx = wine_prefix(exe_path, engine_ini)
    if pfx is None:
        if remove:
            return
        print("  !! no Proton prefix found for the game, so Wine does not know to")
        print("     load the fix yet. Start the game once through Steam, quit, and")
        print("     run Install again - or add this to the game's launch options:")
        print('       WINEDLLOVERRIDES="{}=n,b" %command%'.format(DLL_INSTALLED[:-4]))
        return
    reg = os.path.join(pfx, "user.reg")
    if game_is_running():
        raise InstallError("the game is running - quit it and run this again "
                           "(Wine would overwrite the registry change when it exits)")
    try:
        with open(reg, "r", encoding="utf-8", errors="surrogateescape") as f:
            text = f.read()
    except (IOError, OSError) as ex:
        raise InstallError("could not read {} ({})".format(reg, ex))
    new = set_wine_dll_override(text, remove)
    if new == text:
        print("  Wine prefix: winhttp.dll {} already".format(
            "not overridden" if remove else "set to native for the game"))
        return
    replace_file(reg, new.encode("utf-8", "surrogateescape"))
    print("  Wine prefix: winhttp.dll {} in {}".format(
        "override removed" if remove else "set to native for the game", reg))


def install_camera(exe_path, width, height, explicit, engine_ini=None):
    """Put the loader next to the game and, under Proton, register it with Wine.

    |explicit| says the resolution was chosen by hand rather than detected:
    then it is written to the loader's ini, otherwise the loader reads the
    primary display itself at every launch, so a new monitor needs no reinstall.
    """
    shipped = shipped_dll()
    if shipped is None:
        raise InstallError("{} is not next to this program - the download is "
                           "incomplete".format(DLL_SHIPPED))
    retire_exe_patch(exe_path)
    dll, ini, log = camera_paths(exe_path)
    if os.path.isfile(dll) and not is_our_dll(dll):
        what = describe_dll(dll)
        raise InstallError(
            "there is already a {} next to the game that is not this fix's{}. "
            "The fix needs that name: move the other file away and run this "
            "again, and please report which mod it belongs to.".format(
                DLL_INSTALLED,
                " - it says it is " + what if what else " - another mod's loader, probably"))
    if os.path.isfile(dll) and same_bytes(dll, shipped):
        print("  loader already in place: {}".format(dll))
    else:
        with open(shipped, "rb") as f:
            replace_file(dll, f.read())
        print("  installed the loader as {}".format(dll))
    if explicit:
        replace_file(ini, (
            "; Written by the LiS Ultrawide Fix installer. Without this file the\n"
            "; loader reads the primary display's resolution at every launch.\n"
            "Width={}\nHeight={}\n".format(width, height)).encode("utf-8"))
        print("  {}: {}x{}".format(CAMERA_INI, width, height))
    elif os.path.isfile(ini):
        os.remove(ini)
        print("  removed {} - the loader reads the display at launch".format(CAMERA_INI))
    if os.path.isfile(log):
        try:
            os.remove(log)             # the next launch writes a fresh one
        except (IOError, OSError):
            pass
    if os.name != "nt":
        wine_dll_override(exe_path, engine_ini)
    disable_suwsf(exe_path)


def remove_camera(exe_path, engine_ini=None):
    retire_exe_patch(exe_path)
    dll, ini, log = camera_paths(exe_path)
    if os.path.isfile(dll):
        if is_our_dll(dll):
            try:
                os.remove(dll)
            except (IOError, OSError) as ex:
                raise InstallError(write_failure(dll, ex))
            print("  removed the loader {}".format(dll))
        else:
            print("  left alone: the winhttp.dll next to the game is not this fix's")
    for path in (ini, log):
        if os.path.isfile(path):
            try:
                os.remove(path)
            except (IOError, OSError):
                pass
    if os.name != "nt":
        wine_dll_override(exe_path, engine_ini, remove=True)


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


def _library_of(exe_path):
    """<library>/steamapps/common/<game>/.../x.exe -> <library>, or None."""
    path = os.path.dirname(os.path.abspath(exe_path))
    while True:
        parent = os.path.dirname(path)
        if parent == path:
            return None
        if os.path.basename(path).lower() == "steamapps":
            return parent
        path = parent


def _proton_prefix(library):
    """Where Steam keeps the game's Proton prefix for this library."""
    return os.path.join(_child(library, "steamapps"), "compatdata", STEAM_APPID, "pfx")


def engine_ini_path(exe_path=None, override=None):
    """Locate the user's Engine.ini: native Windows first, then a Proton prefix.

    |override| is --engine-ini, for a copy of the game that runs in a prefix
    Steam does not manage (Heroic, Lutris, plain Wine).

    Under Proton the game writes its settings inside a prefix that Steam keeps
    in the same library as the game, so the game's own location is the best
    lead; every other library Steam knows about is tried after it. Steam
    creates the prefix the first time the game is started, so before that
    there is nowhere to write and this returns None.
    """
    if override:
        return os.path.abspath(override)
    base = os.environ.get("LOCALAPPDATA")
    if base:
        p = os.path.join(base, "Chronos", "Saved", "Config", "Windows", "Engine.ini")
        if os.path.isdir(os.path.dirname(p)):
            return p
    libraries = []
    if exe_path:
        library = _library_of(exe_path)
        if library:
            libraries.append(library)
    libraries += _steam_libraries()
    seen = set()
    for library in libraries:
        pfx = _proton_prefix(library)
        if os.path.normcase(pfx) in seen:
            continue
        seen.add(os.path.normcase(pfx))
        # drive_c is there once the game has run; the Chronos folders under
        # it may not be yet, and apply_engine_ini creates them
        if os.path.isdir(os.path.join(pfx, "drive_c")):
            return os.path.join(pfx, "drive_c", "users", "steamuser", "AppData",
                                "Local", "Chronos", "Saved", "Config", "Windows",
                                "Engine.ini")
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
    """Remove a previously written managed block, so re-runs never stack.

    Only a complete BEGIN..END pair is removed. A BEGIN whose END is missing -
    a hand-edited or truncated file - is left alone rather than swallowing
    every line after it, which could be the user's own settings.
    """
    lines = text.splitlines(True)
    out, i = [], 0
    while i < len(lines):
        if lines[i].strip() == INI_BEGIN:
            end = i + 1
            while end < len(lines) and lines[end].strip() != INI_END:
                end += 1
            if end < len(lines):
                # also drop the single blank separator line we insert before it,
                # so removing the block restores the file exactly as it was
                if out and not out[-1].strip():
                    out.pop()
                i = end + 1
                continue
            print("  !! an unfinished '{}' marker is in this file - leaving "
                  "everything after it untouched".format(INI_BEGIN.strip("; =")))
        out.append(lines[i])
        i += 1
    return "".join(out)


def apply_engine_ini(exe_path, width, height, chromatic, sharpness, remove=False,
                     override=None):
    path = engine_ini_path(exe_path, override)
    if not path:
        print("  !! could not locate Engine.ini - skipping the display tweaks")
        if os.name != "nt":
            print("     It lives in the game's Proton prefix, which Steam creates "
                  "the first time the game is started. Start the game once, "
                  "quit, and run this installer again. If the game runs outside "
                  "Steam, pass --engine-ini with the path inside its prefix.")
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
        # through a temporary file, so a failure never truncates settings
        # the user had in there
        tmp_path = path + ".tmp"
        try:
            with io.open(tmp_path, "w", encoding="utf-8") as f:
                f.write(new)
            os.replace(tmp_path, path)
        except (IOError, OSError):
            try:
                if os.path.isfile(tmp_path):
                    os.remove(tmp_path)
            except (IOError, OSError):
                pass
            raise
    except (IOError, OSError) as ex:
        raise InstallError(write_failure(path, ex))
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


def ui_script():
    here = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(here, "tools", "assetdump", "patch_ui_layout.py")
    return script if os.path.isfile(script) else None


def check_game_files(exe_path):
    """Is the installed mod container still the one this game needs?

    -> (status, one-line detail), from `patch_ui_layout.py --verify`. Answering
    here rather than in this file keeps one description of what is installed,
    and the check itself reads a megabyte of the game's data - no decoding, no
    container parsing, nothing that could prompt.

    The one that matters is `stale`: a game update replaces the packages the
    container was built from, and unlike the old in-place patch - which an
    update simply overwrote - a container left behind keeps shadowing them with
    copies cooked for a build that is gone.
    """
    import subprocess
    script = ui_script()
    if script is None:
        return "noscript", "not checked - patch_ui_layout.py is not next to this program"
    paks = paks_dir_for(exe_path)
    if not os.path.isdir(paks):
        return "nopaks", "not checked - no {} next to that executable".format(
            os.path.basename(paks))
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.Popen([sys.executable, script, "--paks", paks, "--verify"],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                env=env)
        out, _ = proc.communicate()
    except Exception as ex:
        return "error", "not checked ({})".format(ex)
    status, detail = "error", "not checked"
    for line in out.decode("utf-8", "replace").splitlines():
        if line.startswith("status: "):
            status = line[8:].strip()
        elif line.startswith("detail: "):
            detail = line[8:].strip()
    return status, detail


def apply_game_files(exe_path, width, height, restore=False):
    import subprocess
    script = ui_script()
    if script is None:
        print("  !! tools/assetdump/patch_ui_layout.py not found - skipping")
        return False
    cmd = [sys.executable, script, "--paks", paks_dir_for(exe_path)]
    cmd += ["--restore"] if restore else ["--width", str(width), "--height", str(height)]
    env = dict(os.environ)
    # The child's output is decoded as UTF-8 below, so it has to be written as
    # UTF-8. Without this the child encodes a pipe in the machine's ANSI code
    # page, and a game path with a non-ASCII character in it comes back as
    # mojibake - or raises UnicodeEncodeError before printing at all. The GUI
    # sets the same pair for patcher.py itself; this covers a plain console run.
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
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
                do_sharpen, restore=False, engine_ini=None, explicit=True):
    if restore:
        print("\nRestoring everything to stock...")
        ok = True
        if do_exe:
            remove_camera(exe_path, engine_ini)
        if do_files:
            ok = apply_game_files(exe_path, width, height, restore=True) and ok
        if do_chromatic or do_sharpen:
            apply_engine_ini(exe_path, width, height, False, False, remove=True,
                             override=engine_ini)
        print("\nDone - the game is back to its shipped state." if ok else
              "\nDone - one part could not be undone, see above.")
        return ok

    ok = True
    print("\nInstalling for {}x{} ({:.4f}:1)".format(
        width, height, width / float(height)))

    print("\n[1/3] Ultrawide camera (loader library)")
    if do_exe:
        install_camera(exe_path, width, height, explicit, engine_ini)
    else:
        print("  skipped")

    print("\n[2/3] Full-width UI (game files)")
    if do_files:
        ok = apply_game_files(exe_path, width, height) and ok
    else:
        print("  skipped")

    print("\n[3/3] Display tweaks (Engine.ini)")
    if do_chromatic or do_sharpen:
        apply_engine_ini(exe_path, width, height, do_chromatic, do_sharpen,
                         override=engine_ini)
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
                        help="only report whether the ultrawide camera loader "
                             "is installed next to the executable, then exit")
    parser.add_argument("--width", type=int, help="display width, e.g. 5120")
    parser.add_argument("--height", type=int, help="display height, e.g. 2160")
    parser.add_argument("--restore", action="store_true",
                        help="undo everything this installer applied")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="accept all defaults, no prompts")
    parser.add_argument("--no-exe", action="store_true",
                        help="skip the ultrawide camera (the loader library "
                             "next to the executable)")
    parser.add_argument("--no-game-files", action="store_true",
                        help="skip the full-width UI patch (the game data files)")
    parser.add_argument("--no-chromatic-fix", action="store_true",
                        help="skip disabling chromatic aberration")
    parser.add_argument("--sharpen", action="store_true",
                        help="also write the recommended anti-blur TSR "
                             "settings (off by default)")
    parser.add_argument("--engine-ini", metavar="PATH",
                        help="write the display tweaks to this Engine.ini instead "
                             "of the one found automatically - for a copy of the "
                             "game that runs in a prefix Steam does not manage")
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

    status, detail = check_camera(exe_path)
    files_status, files_detail = check_game_files(exe_path)
    if args.check_exe:                       # machine-readable, for the GUI
        print("status: {}".format(status))
        print("detail: {}".format(detail))
        print("files: {}".format(files_status))
        print("filesdetail: {}".format(files_detail))
        return
    print("Ultrawide camera: {}".format(detail))
    print("Full-width UI: {}".format(files_detail))
    if files_status == "stale":
        print("  !! the game has been updated since this was installed - "
              "install again before playing.")

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
    # chosen by hand, or not what this machine's display says: the loader
    # then gets told, instead of reading the display itself at launch
    explicit = detected is None or (width, height) != tuple(detected)

    if args.restore:
        run_install(exe_path, width, height,
                    not args.no_exe, not args.no_game_files,
                    not args.no_chromatic_fix, True, restore=True,
                    engine_ini=args.engine_ini)
        return

    do_exe = not args.no_exe
    do_files = not args.no_game_files
    do_chromatic = not args.no_chromatic_fix
    do_sharpen = args.sharpen

    if not args.yes:
        print("\nWhat to install:")
        do_exe = ask_yes(
            "\n  Ultrawide camera - Hor+ cutscenes, dialogue and exploration with no\n"
            "  black bars and no zoom when a dialogue ends. Installs a small library\n"
            "  the game loads at start; the executable itself is not changed.",
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
                do_sharpen, engine_ini=args.engine_ini, explicit=explicit)


def survive_odd_characters():
    """Never let an unprintable character in a path end the install.

    Windows gives stdout the machine's ANSI code page, which cannot represent
    every path it will happily hand out - a game folder or a user name in a
    script the local code page does not cover raises UnicodeEncodeError from
    an ordinary print(), halfway through the work. The encoding stays as the
    environment set it (the GUI sets UTF-8 on both ends); only the reaction to
    a character it cannot spell changes, from raising to showing an escape.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except Exception:              # Python 3.6, or a stream without it
            pass


def main():
    """Expected problems get one line; anything else keeps its traceback."""
    survive_odd_characters()
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
