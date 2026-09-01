"""
Widen the game's 16:9 UI layout to the real UMG design space.

Root cause (RESEARCH.md 9c): `BP_UIWindowManager`'s `WindowParent` - the panel
every game window is reparented into - is a fixed 3840x2160 box centred in the
viewport, so all UI is clipped to 16:9 on an ultrawide display. Edit #1 widens
it; the rest repair the handful of elements the audit (9c-2) found positioned by
absolute coordinates on the 3840 canvas, which would otherwise shift left.

Every edit here rewrites an *existing* float in place, so package sizes never
change. Structural edits (adding/removing serialized properties) are not
supported and are not needed for this set.

Method: append-only. Each modified package chunk is written as a new
uncompressed block at the end of the .ucas (existing bytes are never
overwritten), the TOC compression-block entry is repointed, and the chunk's
BLAKE3-20 meta hash is recomputed. `.utoc` is backed up, so --restore is exact.

    python patch_ui_layout.py --width 5120 --height 2160
    python patch_ui_layout.py --restore
"""
import argparse, hashlib, json, os, shutil, struct, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from iostore import Toc, load_script_objects
from zen import ZenPackage
from slots import decode_slot

DESIGN_W, DESIGN_H = 3840.0, 2160.0
UI = 'Chronos/Content/UI/'
def _default_paks():
    """Walk up from this script looking for the game's Content/Paks."""
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        cand = os.path.join(here, 'Chronos', 'Content', 'Paks')
        if os.path.isdir(cand):
            return cand
        parent = os.path.dirname(here)
        if parent == here:
            break
        here = parent
    return os.getcwd()


DEFAULT_PAKS = _default_paks()

L, T, R, B = 0, 1, 2, 3


def inset(v):
    """left-anchored: keep the element where the 3840 box used to put it"""
    return lambda w, h: v + (w - DESIGN_W) / 2.0


def outset(v):
    """right-anchored: same, mirrored"""
    return lambda w, h: v - (w - DESIGN_W) / 2.0


# (package, widget the slot holds, which Offsets field, old value, new value)
EDITS = [
    # --- the fix itself -----------------------------------------------------
    (UI + 'BP/BP_UIWindowManager.uasset', 'WindowParent', R, 3840.0, lambda w, h: w),

    # --- HIGH: centred by hardcoding half of 3840 ---------------------------
    (UI + 'BP/Window/BP_PauseWindow.uasset', 'Pause', L, 1920.0, lambda w, h: w / 2),

    # --- FIXW: fixed 3840-wide, not centred; must span the widened parent ---
    (UI + 'BP/Window/BP_SettingsWindow.uasset', 'Background', R, 3840.0, lambda w, h: w),
    (UI + 'BP/Window/BP_SaveSelectWindow.uasset', 'D9Image', R, 3840.0, lambda w, h: w),
    (UI + 'BP/Window/BP_SquareEnixAccountWindow.uasset', 'CanvasPanel_Background', R, 3840.0, lambda w, h: w),
    (UI + 'BP/Window/BP_SquareEnixAccountWindow.uasset', 'WidgetSwitcher_CurrentView', R, 3840.0, lambda w, h: w),
    (UI + 'BP/Controls/Settings/BP_UISettings.uasset', 'Buttons', R, 3840.0, lambda w, h: w),
    (UI + 'BP/Controls/PlayerMenu/Collectibles/BP_CollectiblePosterUI.uasset', 'D9Image', R, 3840.0, lambda w, h: w),
    (UI + 'BP/Controls/Choices/BP_ShiftChoiceUI.uasset', 'ChoiceButton', R, 3840.0, lambda w, h: w),

    # --- full-bleed 16:9 compositions: re-inset so they keep their authored
    #     framing instead of riding out to the physical screen edges.
    #     inset() shifts a left-anchored element right by (designW-3840)/2;
    #     outset() shifts a right-anchored one left by the same amount.
    (UI + 'BP/Window/BP_MainMenuWindow.uasset', 'MainButtons', L, 220.0, inset(220.0)),
    (UI + 'BP/Window/BP_MainMenuWindow.uasset', 'D9Image', L, 184.0, inset(184.0)),
    (UI + 'BP/Window/BP_MainMenuWindow.uasset', 'GamerTag', L, 220.0, inset(220.0)),
    (UI + 'BP/Window/BP_MainMenuWindow.uasset', 'InfocastPanel', L, -220.0, outset(-220.0)),
    (UI + 'BP/Window/BP_TitleWindow.uasset', 'GamerTag', L, 220.0, inset(220.0)),
    (UI + 'BP/Window/BP_TitleWindow.uasset', 'PressAnyKey', L, 220.0, inset(220.0)),
]

FIELD = {L: 'Left', T: 'Top', R: 'Right', B: 'Bottom'}


def design_space(w, h):
    """UE EUIScalingRule::ScaleToFit -> the UMG design space in slate units."""
    scale = min(w / DESIGN_W, h / DESIGN_H)
    return w / scale, h / scale


def slot_payload(pkg, widget):
    """-> (export, slot dict, payload bytes, chunk offset of the payload)"""
    byidx = {e['i']: e for e in pkg.exports}
    lay = pkg._bundle_layout()
    for e in pkg.exports:
        if str(pkg.resolve(e['cls'])).replace('/Script/', '') != 'UMG.CanvasPanelSlot':
            continue
        s = decode_slot(pkg.export_data(e))
        if byidx.get((s['Content'] or 0) - 1, {}).get('name') == widget:
            return e, s, pkg.export_data(e), lay[e['i']]
    return None, None, None, None


def find_unique_float(payload, value):
    hits = [o for o in range(len(payload) - 3)
            if struct.unpack_from('<f', payload, o)[0] == value]
    if len(hits) != 1:
        raise ValueError('%d occurrences of %g in the slot payload (need exactly 1)'
                         % (len(hits), value))
    return hits[0]


def toc_layout(path):
    with open(path, 'rb') as f:
        h = f.read(144)
    entries = struct.unpack_from('<I', h, 0x18)[0]
    seeds = struct.unpack_from('<I', h, 0x54)[0]
    nohash = struct.unpack_from('<I', h, 0x60)[0]
    return dict(entries=entries,
                blocks_off=144 + entries * 22 + seeds * 4 + nohash * 4,
                meta_off=os.path.getsize(path) - entries * 33)


class PatchError(Exception):
    """A problem the user can act on - one line, no traceback."""


SIDECAR_VERSION = 2
HEAD = 1 << 20          # bytes of .ucas hashed as its fingerprint


def sha256_file(path, limit=None):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        left = limit
        while left is None or left > 0:
            block = f.read(min(1 << 20, left) if left is not None else 1 << 20)
            if not block:
                break
            h.update(block)
            if left is not None:
                left -= len(block)
    return h.hexdigest()


def container_fingerprint(utoc, ucas):
    """What the backup is a backup OF.

    The patch only ever appends to the .ucas, so the head of that file is the
    same before and after - which makes it the thing to recognise a build by.
    A game update rewrites both files and changes it.
    """
    return {'version': SIDECAR_VERSION,
            'ucas_size': os.path.getsize(ucas),
            'ucas_head': sha256_file(ucas, HEAD),
            'utoc_size': os.path.getsize(utoc),
            'utoc_sha256': sha256_file(utoc)}


def backup_state(utoc, ucas, backup, sidecar):
    """-> ('none' | 'valid' | 'stale' | 'legacy', note).

    'stale' means the backup was taken from a different build of the game:
    restoring it would put an old table of contents on a new container and
    truncate 18 GB of data to the wrong length. It is never written back.
    """
    if not os.path.exists(backup):
        return 'none', ''
    if not os.path.exists(sidecar):
        return 'legacy', 'no record of which build the backup came from'
    try:
        with open(sidecar) as f:
            rec = json.load(f)
    except (IOError, OSError, ValueError):
        return 'stale', 'the record next to the backup is unreadable'
    if rec.get('version', 1) < SIDECAR_VERSION:
        return 'legacy', 'the backup predates build checking'
    if os.path.getsize(ucas) < rec['ucas_size']:
        return 'stale', 'the game data is smaller than when the backup was taken'
    if sha256_file(ucas, HEAD) != rec['ucas_head']:
        return 'stale', 'the game data has been replaced since the backup'
    if sha256_file(backup) != rec.get('utoc_sha256'):
        return 'stale', 'the backup file itself has changed'
    return 'valid', ''


def set_aside(backup, sidecar):
    """Keep a stale backup, out of the way. -> the name it now has."""
    for n in range(1, 100):
        target = '%s.old%s' % (backup, '' if n == 1 else n)
        if not os.path.exists(target):
            os.rename(backup, target)
            if os.path.exists(sidecar):
                os.remove(sidecar)
            return target
    raise PatchError('too many old container backups in this folder - '
                     'please tidy them up.')


def take_backup(utoc, ucas, backup, sidecar):
    print('backing up %s (%.1f MB)...' % (utoc, os.path.getsize(utoc) / 1e6))
    shutil.copyfile(utoc, backup)
    with open(sidecar, 'w') as f:
        json.dump(container_fingerprint(backup, ucas), f)


def reset_to_stock(utoc, ucas, backup, sidecar):
    """Undo a previous run: the stock .utoc back, the appended blocks dropped."""
    shutil.copyfile(backup, utoc)
    with open(sidecar) as f:
        orig = json.load(f)['ucas_size']
    if os.path.getsize(ucas) > orig:
        with open(ucas, 'r+b') as f:
            f.truncate(orig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--paks', default=DEFAULT_PAKS)
    ap.add_argument('--width', type=int, default=5120)
    ap.add_argument('--height', type=int, default=2160)
    ap.add_argument('--restore', action='store_true')
    ap.add_argument('--verify', action='store_true',
                    help='report the backup state and change nothing')
    a = ap.parse_args()

    if not os.path.isdir(a.paks):
        raise PatchError("the game's data folder is not where it was expected "
                         "(%s)." % a.paks)
    os.chdir(a.paks)
    utoc, ucas = 'pakchunk0-Windows.utoc', 'pakchunk0-Windows.ucas'
    backup, sidecar = utoc + '.original', 'pakchunk0-Windows.uipatch.json'
    for f in (utoc, ucas):
        if not os.path.exists(f):
            raise PatchError('%s is missing from %s.' % (f, a.paks))

    state, why = backup_state(utoc, ucas, backup, sidecar)

    if a.verify:
        print('container: %s' % state + ((' (%s)' % why) if why else ''))
        return

    if state == 'legacy':
        # A backup from before this check existed. It cannot be proven to match
        # the container, but it is the only stock copy there is, so it is kept
        # and fingerprinted now - from here on it is checkable.
        print('note: %s - recording one now' % why)
        record = container_fingerprint(backup, ucas)
        try:                       # a v1 record still knew the stock .ucas size
            with open(sidecar) as f:
                record['ucas_size'] = json.load(f)['ucas_size']
        except (IOError, OSError, ValueError, KeyError):
            pass                   # else: today's size, appended blocks and all
        with open(sidecar, 'w') as f:
            json.dump(record, f)
        state = 'valid'

    if state == 'stale':
        # The game was updated underneath us. Writing this backup back would put
        # an old table of contents on a new container: the game would not start,
        # and the only repair is a full re-download. So it is set aside instead.
        aside = set_aside(backup, sidecar)
        print('the backup was taken from a different build of the game (%s);' % why)
        print('it has been set aside as %s.' % aside)
        if a.restore:
            print('the current game data is whatever the update installed - '
                  'there is nothing of ours left in it to undo.')
            return
        take_backup(utoc, ucas, backup, sidecar)
        state = 'valid'

    if state == 'valid':
        reset_to_stock(utoc, ucas, backup, sidecar)
        print('reset to stock container')

    if a.restore:
        if state == 'none':
            print('nothing to restore - this container was never patched.')
            return
        for p in (backup, sidecar):
            if os.path.exists(p):
                os.remove(p)
        print('stock state restored.')
        return

    if state == 'none':
        take_backup(utoc, ucas, backup, sidecar)

    dw, dh = design_space(a.width, a.height)
    print('%dx%d -> UMG design space %.0fx%.0f\n' % (a.width, a.height, dw, dh))
    if abs(dw - DESIGN_W) < 1 and abs(dh - DESIGN_H) < 1:
        print('already 16:9 - nothing to do.')
        return

    try:
        from blake3 import blake3          # the compiled one, if installed
    except ImportError:
        from blake3_pure import blake3     # stdlib fallback, same digest

    so = load_script_objects('global.utoc')
    toc = Toc(utoc)
    lay = toc_layout(utoc)

    by_pkg = {}
    for pkg_path, widget, field, old, new in EDITS:
        by_pkg.setdefault(pkg_path, []).append((widget, field, old, new))

    applied = failed = 0
    for pkg_path, edits in by_pkg.items():
        name = pkg_path.replace(UI, '').replace('.uasset', '')
        if pkg_path not in toc.index:
            print('  SKIP %-34s not in container' % name)
            failed += 1
            continue
        idx = toc.index[pkg_path]
        data = toc.read(idx)
        pkg = ZenPackage(data, so)
        buf = bytearray(data)
        notes = []
        for widget, field, old, newfn in edits:
            e, s, payload, base = slot_payload(pkg, widget)
            if e is None:
                notes.append('  !! %s: slot not found' % widget)
                continue
            if s['Offsets'][field] != old:
                notes.append('  !! %s.%s is %g, expected %g - skipped'
                             % (widget, FIELD[field], s['Offsets'][field], old))
                continue
            try:
                off = find_unique_float(payload, old)
            except ValueError as ex:
                notes.append('  !! %s: %s' % (widget, ex))
                continue
            val = newfn(dw, dh)
            struct.pack_into('<f', buf, base + off, val)
            notes.append('  %-26s %-6s %g -> %g' % (widget, FIELD[field], old, val))

        if not any(n.startswith('  ' ) and not n.startswith('  !!') for n in notes):
            print('%s\n%s' % (name, '\n'.join(notes)))
            failed += 1
            continue

        # repoint the chunk's single compression block at a fresh appended block
        b = toc.offlen[idx * 10:(idx + 1) * 10]
        coff = int.from_bytes(b[0:5], 'big')
        clen = int.from_bytes(b[5:10], 'big')
        first, last = coff // toc.blocksize, (coff + clen - 1) // toc.blocksize
        if first != last or coff % toc.blocksize or clen != len(buf):
            print('%s\n  !! unexpected block layout - skipped' % name)
            failed += 1
            continue

        payload_bytes = bytes(buf)
        new_off = (os.path.getsize(ucas) + 15) & ~15
        with open(ucas, 'r+b') as f:
            f.seek(new_off)
            f.write(payload_bytes)
        entry = (new_off.to_bytes(5, 'little') + len(payload_bytes).to_bytes(3, 'little')
                 + len(payload_bytes).to_bytes(3, 'little') + bytes([0]))
        with open(utoc, 'r+b') as f:
            f.seek(lay['blocks_off'] + first * 12)
            f.write(entry)
            f.seek(lay['meta_off'] + idx * 33)
            f.write(blake3(payload_bytes).digest(length=32)[:20] + b'\0' * 12)
        print('%s\n%s' % (name, '\n'.join(notes)))
        applied += 1

    # ------------------------------------------------------------ verify pass
    print('\nverifying through the container reader...')
    toc2 = Toc(utoc)
    bad = 0
    for pkg_path, edits in by_pkg.items():
        if pkg_path not in toc2.index:
            continue
        pkg2 = ZenPackage(toc2.read(toc2.index[pkg_path]), so)
        for widget, field, old, newfn in edits:
            _, s, _, _ = slot_payload(pkg2, widget)
            want = newfn(dw, dh)
            got = s['Offsets'][field] if s else None
            if got is None or abs(got - want) > 0.5:
                print('  MISMATCH %s.%s = %s (want %g)' % (widget, FIELD[field], got, want))
                bad += 1
    print('%d packages patched, %d skipped, %d mismatches' % (applied, failed, bad))
    print('OK - safe to launch.' if not bad else 'FAILED - run --restore')


def cli():
    """Expected problems get one line; anything else keeps its traceback."""
    # A path the machine's code page cannot spell must not turn an ordinary
    # print() into UnicodeEncodeError halfway through the patch; see the same
    # guard in patcher.py, which is what usually runs this.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors='backslashreplace')
        except Exception:
            pass
    try:
        main()
    except PatchError as ex:
        print('error: %s' % ex)
        sys.exit(2)
    except (IOError, OSError) as ex:
        win = getattr(ex, 'winerror', None)
        if win == 32:
            print('error: the game data is in use - close the game and Steam, '
                  'then try again.')
        elif win == 5 or getattr(ex, 'errno', None) == 13:
            print('error: Windows refused permission to write the game data. '
                  'Close the game, and if it is installed under Program Files, '
                  'run the installer as administrator.')
        elif win == 112 or getattr(ex, 'errno', None) == 28:
            print('error: the drive holding the game is full.')
        else:
            raise
        sys.exit(2)


if __name__ == '__main__':
    cli()
