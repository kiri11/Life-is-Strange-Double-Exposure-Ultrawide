"""
Absolute-offset audit for the WindowParent widening fix (RESEARCH.md 9c-1).

BP_UIWindowManager's `WindowParent` is a fixed 3840x2160 box centred in the
viewport. Widening it to `2160 * aspect` moves its local origin left by
(newWidth - 3840)/2. Anything positioned by a *fractional anchor* rides along
correctly; anything positioned by an *absolute horizontal offset* tuned to the
3840 canvas shifts with the origin and needs a manual adjustment.

This walks every UI widget package, decodes each UCanvasPanelSlot, resolves the
ancestor chain, and reports the slots that would actually move.

Usage (from Chronos/Content/Paks):
    python audit_offsets.py [monitor_width] [monitor_height]
"""
import os, sys, struct

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from iostore import Toc, load_script_objects
from zen import ZenPackage
from slots import decode_slot

DESIGN_W = 3840.0

def collect(toc, prefix='Chronos/Content/UI/'):
    return sorted(k for k in toc.index
                  if k.startswith(prefix) and k.endswith('.uasset'))

def analyse(pkg):
    """-> list of slot dicts with resolved parent/content names, plus a widget->slot map."""
    byidx = {e['i']: e for e in pkg.exports}
    out = []
    for e in pkg.exports:
        cls = str(pkg.resolve(e['cls'])).replace('/Script/', '')
        if cls != 'UMG.CanvasPanelSlot':
            continue
        try:
            s = decode_slot(pkg.export_data(e))
        except Exception:
            continue
        s['parent_i'] = (s['Parent'] or 0) - 1
        s['content_i'] = (s['Content'] or 0) - 1
        s['parent'] = byidx.get(s['parent_i'], {}).get('name', '?')
        s['content'] = byidx.get(s['content_i'], {}).get('name', '?')
        out.append(s)
    slot_of = {s['content_i']: s for s in out}
    return out, slot_of

def h_stretch(s):
    """slot stretches horizontally with its parent -> width-preserving"""
    return s['AnchorMin'][0] == 0.0 and s['AnchorMax'][0] == 1.0

def chain_preserves_width(s, slot_of, limit=24):
    """True if every ancestor between this slot and the window root is full-width."""
    cur, n = s['parent_i'], 0
    while cur is not None and cur >= 0 and n < limit:
        ps = slot_of.get(cur)
        if ps is None:
            return True                      # reached the window root
        if not h_stretch(ps):
            return False                     # an ancestor is itself fixed/anchored
        cur, n = ps['parent_i'], n + 1
    return True

def classify(s):
    """-> (severity, note) or None if the slot is safe."""
    amin, amax = s['AnchorMin'][0], s['AnchorMax'][0]
    left, right = s['Offsets'][0], s['Offsets'][2]
    alignx = s['Alignment'][0]

    if amin != amax:
        return None                          # horizontal stretch: scales with parent

    # fixed-width element sized to the whole 3840 canvas (point anchor -> Right is a width)
    if abs(right - DESIGN_W) < 8 and right > 0:
        if alignx == 0.5:
            return ('BOX', 'deliberate 3840 box, centred - stays a 16:9 island, content unaffected')
        return ('FIXW', 'fixed 3840-wide, not centred - will no longer span the widened parent')

    if amin != 0.0:
        return None                          # anchored to centre/right: rides the edge

    # anchor X == 0 -> horizontal position is an absolute coordinate from the left
    if abs(left) < 400 and alignx == 0.0:
        return None                          # genuine left-edge element
    if alignx == 0.5 and abs(left - DESIGN_W / 2) < 120:
        return ('HIGH', 'centred by hardcoding half of 3840')
    if alignx == 1.0 and abs(left - DESIGN_W) < 200:
        return ('HIGH', 'right-edge by hardcoding 3840')
    if abs(left) >= 400:
        return ('MED', 'absolute X=%g on the 3840 canvas' % left)
    return None

def main():
    mw = float(sys.argv[1]) if len(sys.argv) > 2 else 5120.0
    mh = float(sys.argv[2]) if len(sys.argv) > 2 else 2160.0
    new_w = 2160.0 * (mw / mh)
    shift = (new_w - DESIGN_W) / 2.0

    so = load_script_objects('global.utoc')
    toc = Toc('pakchunk0-Windows.utoc')
    paths = collect(toc)

    print('WindowParent: %g -> %g wide (%gx%g).  Absolutely-positioned content '
          'shifts %g px left.\n' % (DESIGN_W, new_w, mw, mh, shift))

    scanned = failed = nslots = 0
    findings = []
    for path in paths:
        try:
            data = toc.read(toc.index[path])
            pkg = ZenPackage(data, so)
            slots, slot_of = analyse(pkg)
        except Exception:
            failed += 1
            continue
        if not slots:
            continue
        scanned += 1
        nslots += len(slots)
        for s in slots:
            c = classify(s)
            if not c:
                continue
            sev, note = c
            live = chain_preserves_width(s, slot_of)
            findings.append((sev, live, path, s, note))

    order = {'HIGH': 0, 'FIXW': 1, 'MED': 2, 'BOX': 3}
    findings.sort(key=lambda f: (not f[1], order[f[0]], f[2]))

    live = [f for f in findings if f[1]]
    dead = [f for f in findings if not f[1]]

    def show(fs, title):
        print('=' * 100)
        print(title)
        print('=' * 100)
        if not fs:
            print('  (none)\n')
            return
        last = None
        for sev, _l, path, s, note in fs:
            short = path.replace('Chronos/Content/UI/', '').replace('.uasset', '')
            if short != last:
                print('\n  %s' % short)
                last = short
            print('    [%-4s] %-22s -> %-26s anchorX=%g alignX=%g offsets=(%g,%g,%g,%g)  %s'
                  % (sev, s['parent'], s['content'], s['AnchorMin'][0], s['Alignment'][0],
                     s['Offsets'][0], s['Offsets'][1], s['Offsets'][2], s['Offsets'][3], note))
        print()

    show(live, 'NEEDS FIXING - absolutely positioned and inside a full-width chain (%d)' % len(live))
    show(dead, 'INFORMATIONAL - absolute, but an ancestor is itself fixed/anchored, so it does '
               'not move independently (%d)' % len(dead))

    print('-' * 100)
    print('packages with canvas slots: %d   slots decoded: %d   unreadable packages: %d'
          % (scanned, nslots, failed))
    for sev in ('HIGH', 'FIXW', 'MED', 'BOX'):
        print('  %-5s %d' % (sev, sum(1 for f in live if f[0] == sev)))

if __name__ == '__main__':
    main()
