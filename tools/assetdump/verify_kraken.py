"""
Compare kraken.py with a native Oodle library on the game's own blocks.

Research tool, not part of the fix. Every compressed block in a container is
decoded by both and the outputs compared byte for byte; the code paths the
sample went through are reported from kraken.stats, so a run also says what
it exercised. Run it after a game update, on a machine that has an Oodle
library - Epic's oodle-data-shared.dll or any oo2core_*_win64.dll.

    python verify_kraken.py --dll path/to/oodle-data-shared.dll \\
        --paks <game>/Chronos/Content/Paks [--sample 1500] [--seed 1] [--all]
"""
import argparse, ctypes, os, random, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kraken
from iostore import Toc


def load_native(path):
    dll = ctypes.CDLL(path)
    fn = dll.OodleLZ_Decompress
    fn.restype = ctypes.c_ssize_t
    fn.argtypes = [ctypes.c_char_p, ctypes.c_ssize_t, ctypes.c_char_p, ctypes.c_ssize_t,
                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_ssize_t,
                   ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_int]

    def decompress(src, n):
        dst = ctypes.create_string_buffer(n)
        r = fn(src, len(src), dst, n, 1, 0, 0, None, 0, None, None, None, 0, 3)
        if r != n:
            raise RuntimeError('native decoder returned %d, expected %d' % (r, n))
        return dst.raw
    return decompress


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dll', required=True, help='native Oodle library to compare against')
    ap.add_argument('--paks', required=True, help="the game's Content/Paks folder")
    ap.add_argument('--container', default='pakchunk0-Windows')
    ap.add_argument('--sample', type=int, default=1500, help='blocks to check (default 1500)')
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--all', action='store_true', help='every block, not a sample (slow)')
    a = ap.parse_args()

    native = load_native(a.dll)
    toc = Toc(os.path.join(a.paks, a.container + '.utoc'))
    blocks = [bi for bi in range(toc.cblocks) if toc.blocks[bi * 12 + 11]]
    print('%s: %d compressed blocks of %d' % (a.container, len(blocks), toc.cblocks))
    if not a.all:
        blocks = random.Random(a.seed).sample(blocks, min(a.sample, len(blocks)))

    kraken.stats.clear()
    ok = bad = 0
    total = 0
    started = time.time()
    for k, bi in enumerate(blocks):
        r = toc.blocks[bi * 12:(bi + 1) * 12]
        boff = int.from_bytes(r[0:5], 'little')
        csize = int.from_bytes(r[5:8], 'little')
        usize = int.from_bytes(r[8:11], 'little')
        toc.ucas.seek(boff)
        data = toc.ucas.read(csize)
        want = native(data, usize)
        try:
            got = kraken.decompress(data, usize)
        except kraken.KrakenError as ex:
            got = None
            print('  block %d (%d -> %d): %s' % (bi, csize, usize, ex))
        total += usize
        if got == want:
            ok += 1
        else:
            bad += 1
            if got is not None:
                first = next(i for i in range(usize) if got[i] != want[i])
                print('  block %d (%d -> %d): mismatch at byte %d' % (bi, csize, usize, first))
        if (k + 1) % 500 == 0:
            print('  ... %d checked, %d wrong' % (k + 1, bad))
    seconds = time.time() - started
    print('%d blocks identical, %d wrong; %.1f MB in %.1fs' % (ok, bad, total / 1e6, seconds))
    print('code paths exercised:')
    for key in sorted(kraken.stats):
        print('  %-22s %d' % (key, kraken.stats[key]))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
