"""
Regenerate tests/kraken/ - the Kraken streams tests/test_kraken.py decodes.

Research tool, not part of the fix. It needs a native Oodle library with
OodleLZ_Compress, and it has to be Oodle 2.9.10 - the build UE 5.2 ships and
the game was cooked with. Epic's current builds emit only the classic Kraken
codings; 2.9.10 also produces the TANS, RLE and multi-array literal codings,
the newer Huffman table coding and scaled offsets that make up most of the
game's data, and the whole point of the set is to reach those. The
redistributable is in the OodleUE repository under
Engine/Source/Programs/Shared/EpicGames.Oodle/Sdk/2.9.10/win/redist/.

    python make_kraken_vectors.py --dll path/to/oo2core_9_win64.dll

Inputs are this repository's own files plus synthetic tables shaped like
cooked assets - never game data, so the set can be redistributed. Every
input is compressed at every level and at the option settings that change
the on-disk coding; each stream is decoded by kraken.py and checked against
the library; then the smallest set of streams that between them reach every
decoder code path is kept, plus a fixed list of edge cases, and written with
a manifest recording each stream's decoded size, SHA-256 and code paths.
"""
import argparse, ctypes, hashlib, json, os, random, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import kraken  # noqa: E402


class Options(ctypes.Structure):
    """OodleLZ_CompressOptions, Oodle 2.9."""
    _fields_ = [('verbosity', ctypes.c_uint32), ('minMatchLen', ctypes.c_int32),
                ('seekChunkReset', ctypes.c_int32), ('seekChunkLen', ctypes.c_int32),
                ('profile', ctypes.c_int32), ('dictionarySize', ctypes.c_int32),
                ('spaceSpeedTradeoffBytes', ctypes.c_int32), ('unused', ctypes.c_int32),
                ('sendQuantumCRCs', ctypes.c_int32), ('maxLocalDictionarySize', ctypes.c_int32),
                ('makeLongRangeMatcher', ctypes.c_int32), ('matchTableSizeLog2', ctypes.c_int32),
                ('jobify', ctypes.c_int32), ('jobifyUserPtr', ctypes.c_void_p),
                ('farMatchMinLen', ctypes.c_int32), ('farMatchOffsetLog2', ctypes.c_int32),
                ('reserved', ctypes.c_uint32 * 4)]


class Oodle(object):
    KRAKEN = 8

    def __init__(self, path):
        dll = ctypes.CDLL(path)
        self._compress = dll.OodleLZ_Compress
        self._compress.restype = ctypes.c_ssize_t
        self._compress.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_ssize_t, ctypes.c_char_p,
                                   ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.c_void_p, ctypes.c_ssize_t]
        self._decompress = dll.OodleLZ_Decompress
        self._decompress.restype = ctypes.c_ssize_t
        self._decompress.argtypes = [ctypes.c_char_p, ctypes.c_ssize_t, ctypes.c_char_p, ctypes.c_ssize_t,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                                     ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                     ctypes.c_ssize_t, ctypes.c_int]
        self._defaults = dll.OodleLZ_CompressOptions_GetDefault
        self._defaults.restype = ctypes.c_void_p
        self._defaults.argtypes = [ctypes.c_int, ctypes.c_int]

    def options(self, level, space_speed=None, crc=0):
        o = Options()
        ctypes.memmove(ctypes.byref(o), self._defaults(self.KRAKEN, level), ctypes.sizeof(o))
        if space_speed is not None:
            o.spaceSpeedTradeoffBytes = space_speed
        o.sendQuantumCRCs = crc
        return o

    def compress(self, raw, level, opts=None):
        buf = ctypes.create_string_buffer(len(raw) + 274 * (len(raw) // 0x40000 + 1) + 64)
        n = self._compress(self.KRAKEN, raw, len(raw), buf, level,
                           ctypes.byref(opts) if opts else None, None, None, None, 0)
        if n <= 0:
            raise RuntimeError('OodleLZ_Compress returned %d' % n)
        return buf.raw[:n]

    def decompress(self, src, n):
        dst = ctypes.create_string_buffer(n)
        r = self._decompress(src, len(src), dst, n, 1, 0, 0, None, 0, None, None, None, 0, 3)
        if r != n:
            raise RuntimeError('OodleLZ_Decompress returned %d, expected %d' % (r, n))
        return dst.raw


# --- synthetic inputs --------------------------------------------------------

def records(seed, size, stride, kinds):
    """A table of |stride|-byte records with slowly varying fields, like a cooked asset."""
    rnd = random.Random(seed)
    out = bytearray()
    vals = [0.0] * (stride // 4)
    while len(out) < size:
        for i, k in enumerate(kinds):
            if k == 'f':
                vals[i] += rnd.uniform(-0.5, 0.5)
                out += struct.pack('<f', vals[i])
            elif k == 'i':
                vals[i] += rnd.randint(-3, 3)
                out += struct.pack('<i', int(vals[i]))
            else:
                out += struct.pack('<I', rnd.choice([0, 1, 0xFFFFFFFF, 0x3F800000, 7]))
    return bytes(out[:size])


def names(seed, size):
    """Length-prefixed object names with pointer-like values between them."""
    rnd = random.Random(seed)
    pool = ['BP_%sWindow_C' % w for w in ('Main', 'Pause', 'Title', 'Save', 'Settings', 'Loading', 'Notification')]
    pool += ['/Game/UI/BP/Window/%s' % p for p in pool] + ['CanvasPanelSlot_%d' % i for i in range(40)]
    pool += ['Offsets', 'Anchors', 'Alignment', 'LayoutData', 'ZOrder']
    out = bytearray()
    while len(out) < size:
        s = rnd.choice(pool).encode()
        out += struct.pack('<i', len(s) + 1) + s + b'\0' + struct.pack('<Q', rnd.getrandbits(20) << 3)
    return bytes(out[:size])


def rle_ish(seed, size):
    """Zero runs, noise and repeated bytes, which invites the RLE coding."""
    rnd = random.Random(seed)
    out = bytearray()
    while len(out) < size:
        out += bytes(rnd.randint(1, 300)) + bytes(rnd.getrandbits(8) for _ in range(rnd.randint(1, 20)))
        out += bytes([rnd.getrandbits(8)]) * rnd.randint(1, 500)
    return bytes(out[:size])


def inputs(out_dir):
    files = {}
    for rel in ('LICENSE', 'README.md', 'RESEARCH.md', 'patcher.py', 'LiSUltrawidePatcher.cs',
                'LiSUltrawidePatcher.ico', 'tools/assetdump/kraken.py'):
        with open(os.path.join(ROOT, rel), 'rb') as f:
            files[os.path.basename(rel)] = f.read()
    # Frozen copies, kept because the compressor's choice of coding is
    # sensitive to the exact bytes: the older Huffman table coding, for one,
    # is emitted for research-snapshot.md at level 8 and for no other input
    # tried, and a one-line edit to the live RESEARCH.md was enough to lose it.
    frozen = os.path.join(out_dir, 'inputs')
    if os.path.isdir(frozen):
        for name in sorted(os.listdir(frozen)):
            with open(os.path.join(frozen, name), 'rb') as f:
                files[name] = f.read()
    files['rec8'] = records(1, 120000, 8, 'ff')
    files['rec12'] = records(2, 120000, 12, 'fff')
    files['rec16'] = records(3, 120000, 16, 'ffff')
    files['rec16i'] = records(4, 120000, 16, 'iicc')
    files['rec24'] = records(5, 120000, 24, 'ffffff')
    files['names'] = names(6, 100000)
    files['rleish'] = rle_ish(7, 100000)
    files['mix'] = files['names'][:30000] + files['rec12'][:30000] + files['LICENSE'][:20000] + files['rleish'][:20000]
    files['big'] = (files['RESEARCH.md'] + files['rec16'] + files['patcher.py'] + files['names'])[:600000]
    files['zeros'] = bytes(300000)
    files['tiny1'] = b'L'
    files['tiny8'] = b'LiS:DE 8'
    files['tiny9'] = b'LiS:DE 9!'
    files['small'] = files['LICENSE'][:100]
    files['quantum+1'] = files['RESEARCH.md'][:0x40001]
    return files


# (level, spaceSpeedTradeoffBytes or None for the default, quantum CRCs)
VARIANTS = [(lvl, None, 0) for lvl in range(1, 10)]
VARIANTS += [(4, 1, 0), (7, 1, 0), (8, 1, 0), (9, 1, 0), (9, 4096, 0), (6, None, 1)]

# kept whatever the coverage says: the raw first eight bytes, a quantum
# boundary, memset and stored quanta, checksummed headers
FORCED = ['zeros-L4', 'tiny1-L4', 'tiny1-L9', 'tiny8-L4', 'tiny8-L9', 'tiny9-L4', 'tiny9-L9',
          'small-L4', 'small-L9', 'quantum+1-L6', 'LICENSE-L6-crc', 'LICENSE-L1', 'LICENSE-L4', 'LICENSE-L9']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dll', required=True, help='Oodle 2.9.10 library with OodleLZ_Compress')
    ap.add_argument('--out', default=os.path.join(ROOT, 'tests', 'kraken'))
    a = ap.parse_args()
    oodle = Oodle(a.dll)

    streams = {}
    for name, raw in inputs(a.out).items():
        for level, space_speed, crc in VARIANTS:
            opts = oodle.options(level, space_speed, crc) if (space_speed is not None or crc) else None
            data = oodle.compress(raw, level, opts)
            if oodle.decompress(data, len(raw)) != raw:
                raise RuntimeError('the library does not round-trip %s' % name)
            vname = '%s-L%d%s%s' % (name, level, '-sst%d' % space_speed if space_speed is not None else '',
                                    '-crc' if crc else '')
            kraken.stats.clear()
            out = kraken.decompress(data, len(raw))
            if out != raw:
                raise RuntimeError('kraken.py decodes %s wrongly - fix that first' % vname)
            streams[vname] = dict(name=vname, input=name, level=level, raw_size=len(raw), size=len(data),
                                  sha256=hashlib.sha256(raw).hexdigest(), keys=sorted(kraken.stats),
                                  data=data)
            print('%-32s %8d -> %8d  %s' % (vname, len(data), len(raw), ' '.join(sorted(kraken.stats))))

    every = set()
    for s in streams.values():
        every.update(s['keys'])
    print('\ncode paths reachable: %s' % ' '.join(sorted(every)))

    chosen, covered = [], set()
    while covered != every:
        best = min((n for n in streams if set(streams[n]['keys']) - covered),
                   key=lambda n: streams[n]['size'] / len(set(streams[n]['keys']) - covered))
        chosen.append(best)
        covered |= set(streams[best]['keys'])
    final = chosen + [f for f in FORCED if f in streams and f not in chosen]

    if not os.path.isdir(a.out):
        os.makedirs(a.out)
    for name in os.listdir(a.out):                # streams and manifest; inputs/ stays
        path = os.path.join(a.out, name)
        if os.path.isfile(path):
            os.remove(path)
    manifest = []
    for vname in sorted(final, key=lambda n: streams[n]['size']):
        s = streams[vname]
        with open(os.path.join(a.out, vname + '.kraken'), 'wb') as f:
            f.write(s['data'])
        manifest.append({k: v for k, v in s.items() if k != 'data'})
    with open(os.path.join(a.out, 'manifest.json'), 'w') as f:
        json.dump(manifest, f, indent=1)
        f.write('\n')
    print('\nwrote %d streams, %d KB, to %s' % (len(manifest), sum(m['size'] for m in manifest) // 1024, a.out))


if __name__ == '__main__':
    main()
