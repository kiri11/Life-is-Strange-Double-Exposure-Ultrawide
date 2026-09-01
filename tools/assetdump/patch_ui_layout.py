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

Delivery: the edited packages are published as their own small IoStore
container in `Content/Paks/Mods/`, which the engine mounts after `pakchunk0`
and which therefore shadows the copies in it. `pakchunk0` is only ever read -
so Steam's Verify Integrity has nothing to repair, a game update cannot
half-overwrite the fix, and --restore is three file deletions.

    python patch_ui_layout.py --width 5120 --height 2160
    python patch_ui_layout.py --restore
"""
import argparse, hashlib, json, os, shutil, struct, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def load_reader():
    """Import the container machinery, which is only needed to *build*.

    Reading a package out of `pakchunk0` needs Oodle; reporting what is
    installed, or removing it, needs nothing but the standard library. Keeping
    these imports out of module scope is what lets --verify answer on a machine
    that has no decompressor - which is exactly the machine most likely to be
    asking why the step was skipped.
    """
    global ctr, Toc, load_script_objects, ZenPackage, decode_slot
    import container as ctr
    from iostore import Toc, load_script_objects
    from zen import ZenPackage
    from slots import decode_slot


DESIGN_W, DESIGN_H = 3840.0, 2160.0
UI = 'Chronos/Content/UI/'
MOUNT = '../../../Chronos/Content/'
SOURCE = 'pakchunk0-Windows'

# `_P` is the engine's own marker for a patch pak: it mounts after the shipped
# containers, so every package in it wins over the stock copy.
MOD_NAME = 'LiSUltrawideUI_P'
MOD_DIR = 'Mods'
RECORD = MOD_NAME + '.json'
RECORD_VERSION = 1


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


class PatchError(Exception):
    """A problem the user can act on - one line, no traceback."""


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


def source_fingerprint(ucas):
    """Which build of the game the mod container was generated from."""
    return {'ucas_size': os.path.getsize(ucas), 'ucas_head': sha256_file(ucas, HEAD)}


# ---------------------------------------------------------------------------
# Removing the in-place patch older versions of this fix applied to pakchunk0
# ---------------------------------------------------------------------------

def undo_in_place_patch(paks):
    """Put a container an older release edited in place back to stock. -> note.

    Up to and including the release before this one, the fix appended the
    edited packages to `pakchunk0-Windows.ucas` and repointed its TOC. Anyone
    upgrading still has that on disk, and the mod container would sit on top of
    it, so it is undone first - which also means the packages read below come
    from a stock container.
    """
    utoc = os.path.join(paks, SOURCE + '.utoc')
    backup = utoc + '.original'
    sidecar = os.path.join(paks, SOURCE + '.uipatch.json')
    if not os.path.exists(backup):
        return ''

    ucas = os.path.join(paks, SOURCE + '.ucas')
    record = {}
    try:
        with open(sidecar) as f:
            record = json.load(f)
    except (IOError, OSError, ValueError):
        pass

    stale = (record.get('utoc_sha256') and sha256_file(backup) != record['utoc_sha256']) \
        or (record.get('ucas_head') and sha256_file(ucas, HEAD) != record['ucas_head'])
    if stale:
        # The game was updated over the top of it: that backup belongs to a
        # build that is no longer installed and writing it back would leave an
        # unbootable container. The update already replaced everything we wrote.
        for path in (backup, sidecar):
            if os.path.exists(path):
                os.remove(path)
        return ('removed a backup left by an older version of this fix - it was '
                'taken from a different build of the game and is of no use now')

    shutil.copyfile(backup, utoc)
    original = record.get('ucas_size')
    if original and os.path.getsize(ucas) > original:
        with open(ucas, 'r+b') as f:
            f.truncate(original)
    for path in (backup, sidecar):
        if os.path.exists(path):
            os.remove(path)
    return 'undid the in-place patch an older version of this fix applied'


# ---------------------------------------------------------------------------
# The mod container
# ---------------------------------------------------------------------------

def mod_paths(paks):
    base = os.path.join(paks, MOD_DIR, MOD_NAME)
    return [base + ext for ext in ('.utoc', '.ucas', '.pak')] + \
           [os.path.join(paks, MOD_DIR, RECORD)]


def remove_mod(paks):
    """-> how many of the mod's files were there to remove."""
    gone = 0
    for path in mod_paths(paks):
        if os.path.exists(path):
            os.remove(path)
            gone += 1
    return gone


def source_container_header(toc):
    for i in range(toc.entries):
        if toc.chunk_type(i) == 6:
            return ctr.parse_container_header(toc.read(i))
    raise PatchError('%s has no container header - it cannot be read.' % SOURCE)


def build_mod(paks, dw, dh, script_objects):
    """Edit the packages in memory and publish them as a mod container."""
    toc = Toc(os.path.join(paks, SOURCE + '.utoc'))
    _, stock_entries = source_container_header(toc)

    by_pkg = {}
    for pkg_path, widget, field, old, new in EDITS:
        by_pkg.setdefault(pkg_path, []).append((widget, field, old, new))

    container_id = ctr.container_id_for(MOD_NAME)
    chunks, entries = [], {}
    applied = failed = 0
    for pkg_path, edits in by_pkg.items():
        name = pkg_path.replace(UI, '').replace('.uasset', '')
        if pkg_path not in toc.index:
            print('  SKIP %-34s not in %s' % (name, SOURCE))
            failed += 1
            continue
        idx = toc.index[pkg_path]
        chunk_id = toc.chunkids[idx * 12:(idx + 1) * 12]
        package_id = struct.unpack_from('<Q', chunk_id, 0)[0]
        if package_id not in stock_entries:
            print('  SKIP %-34s no package store entry' % name)
            failed += 1
            continue

        data = toc.read(idx)
        pkg = ZenPackage(data, script_objects)
        buf = bytearray(data)
        notes, done = [], 0
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
            done += 1

        print('%s\n%s' % (name, '\n'.join(notes)))
        if not done:
            failed += 1
            continue
        chunks.append((chunk_id, bytes(buf), pkg_path[len('Chronos/Content/'):]))
        entries[package_id] = stock_entries[package_id]
        applied += 1

    if not chunks:
        raise PatchError('none of the UI packages could be read - nothing to install.')

    chunks.append((ctr.container_header_chunk_id(container_id),
                   ctr.build_container_header(container_id, entries), None))

    mods = os.path.join(paks, MOD_DIR)
    if not os.path.isdir(mods):
        os.makedirs(mods)
    written = ctr.write_container(os.path.join(mods, MOD_NAME), MOUNT, chunks,
                                  container_id)
    print('\nwrote %s/%s.utoc + .ucas + .pak (%.0f KB)'
          % (MOD_DIR, MOD_NAME, written / 1024.0))
    return by_pkg, applied, failed


def verify_mod(paks, by_pkg, dw, dh, script_objects):
    """Read every edit back out of the container the game will actually mount."""
    print('\nverifying through the container reader...')
    toc = Toc(os.path.join(paks, MOD_DIR, MOD_NAME + '.utoc'))
    bad = 0
    for pkg_path, edits in by_pkg.items():
        if pkg_path not in toc.index:
            continue
        pkg = ZenPackage(toc.read(toc.index[pkg_path]), script_objects)
        for widget, field, old, newfn in edits:
            _, s, _, _ = slot_payload(pkg, widget)
            want = newfn(dw, dh)
            got = s['Offsets'][field] if s else None
            if got is None or abs(got - want) > 0.5:
                print('  MISMATCH %s.%s = %s (want %g)'
                      % (widget, FIELD[field], got, want))
                bad += 1
    return bad


def container_state(paks):
    """What is installed, and is it still for the game that is installed?

    -> (status, one-line detail).

    `current`     the container matches the build of the game on disk
    `stale`       the game was updated after the container was built, so it now
                  shadows ten packages with copies cooked for an older build
    `unrecorded`  the files are there but nothing says what they were built
                  from - an older release of this fix, or a hand copy
    `incomplete`  some of the three files are missing; the game may load a
                  container it cannot resolve
    `none`        not installed
    """
    present = [p for p in mod_paths(paks)[:3] if os.path.exists(p)]
    if not present:
        return 'none', 'not installed'
    if len(present) < 3:
        return ('incomplete',
                'incomplete - %d of the 3 files are there; re-run the installer'
                % len(present))

    try:
        with open(os.path.join(paks, MOD_DIR, RECORD)) as f:
            record = json.load(f)
    except (IOError, OSError, ValueError):
        record = {}
    display = ('%dx%d' % tuple(record['display'])) if record.get('display') else None
    if not record.get('ucas_head'):
        return ('unrecorded',
                'installed, but nothing records which build it was made from - '
                're-run the installer')

    ucas = os.path.join(paks, SOURCE + '.ucas')
    if (record.get('ucas_size') != os.path.getsize(ucas)
            or record['ucas_head'] != sha256_file(ucas, HEAD)):
        return ('stale',
                'built for a different build of the game - the game has been '
                'updated since, and the installer must be run again')
    return 'current', 'installed for %s' % (display or 'an unrecorded display')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--paks', default=DEFAULT_PAKS)
    ap.add_argument('--width', type=int, default=5120)
    ap.add_argument('--height', type=int, default=2160)
    ap.add_argument('--restore', action='store_true')
    ap.add_argument('--verify', action='store_true',
                    help='report what is installed and change nothing')
    a = ap.parse_args()

    if not os.path.isdir(a.paks):
        raise PatchError("the game's data folder is not where it was expected "
                         "(%s)." % a.paks)
    paks = a.paks
    for f in (SOURCE + '.utoc', SOURCE + '.ucas'):
        if not os.path.exists(os.path.join(paks, f)):
            raise PatchError('%s is missing from %s.' % (f, paks))

    if a.verify:                             # machine-readable, for patcher.py
        status, detail = container_state(paks)
        if os.path.exists(os.path.join(paks, SOURCE + '.utoc.original')):
            detail += '; %s still carries the in-place patch of an older '                       'version, which the next install removes' % SOURCE
        print('status: %s' % status)
        print('detail: %s' % detail)
        return

    note = undo_in_place_patch(paks)
    if note:
        print('%s\n' % note)

    if a.restore:
        gone = remove_mod(paks)
        print('stock state restored.' if gone or note else
              'nothing to restore - the full-width UI was never installed.')
        return

    dw, dh = design_space(a.width, a.height)
    print('%dx%d -> UMG design space %.0fx%.0f\n' % (a.width, a.height, dw, dh))
    if abs(dw - DESIGN_W) < 1 and abs(dh - DESIGN_H) < 1:
        remove_mod(paks)
        print('already 16:9 - nothing to do.')
        return

    remove_mod(paks)                     # never build on top of an older one
    load_reader()
    script_objects = load_script_objects(os.path.join(paks, 'global.utoc'))
    by_pkg, applied, failed = build_mod(paks, dw, dh, script_objects)
    bad = verify_mod(paks, by_pkg, dw, dh, script_objects)

    print('%d packages published, %d skipped, %d mismatches' % (applied, failed, bad))
    if bad:
        remove_mod(paks)
        print('FAILED - the container was removed; the game is untouched.')
        sys.exit(2)
    record = source_fingerprint(os.path.join(paks, SOURCE + '.ucas'))
    record.update(version=RECORD_VERSION, display=[a.width, a.height])
    with open(os.path.join(paks, MOD_DIR, RECORD), 'w') as f:
        json.dump(record, f)
    print('OK - safe to launch.')


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
