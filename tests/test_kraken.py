"""
Checks tools/assetdump/kraken.py against streams made by Epic's own Kraken
compressor. Plain Python, no test framework:

    python tests/test_kraken.py            # all vectors
    python tests/test_kraken.py -v         # ... naming each one

Every file in tests/kraken/ is a Kraken stream produced by Oodle's own
OodleLZ_Compress from an input that is not game data - this repository's own
files, and synthetic tables shaped like cooked assets - at a range of levels
and option settings. The compressor is Oodle 2.9.10, the build Unreal Engine
5.2 ships and the game was cooked with: it matters, because Epic's current
builds emit only the classic Kraken codings, while 2.9.10 also produces the
TANS, RLE and multi-array literal codings, the newer Huffman table coding
and scaled offsets that make up most of the game's data. The set was chosen
so that between them the streams exercise every decoder code path the
compressor could be made to emit, plus edge cases: one-byte, eight-byte and
nine-byte inputs (the first eight bytes of a stream are stored raw), a
stream that is one byte over a quantum, an all-zero input (a memset quantum),
an incompressible one (a stored block), and one with quantum checksums.

manifest.json records each stream's decoded size and SHA-256, and which of
kraken.stats' code paths it went through when the set was generated; the test
decodes every stream, compares the digest, and fails if a code path recorded
in the manifest was not hit - which is what would happen if a change quietly
routed decoding somewhere else.

tools/assetdump/verify_kraken.py is the other half: it compares the decoder
with a native Oodle library over the game's own blocks, on a machine that
has both. It cannot run here, because game data is not redistributable.
"""
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'tools', 'assetdump'))
import kraken  # noqa: E402

VECTORS = os.path.join(HERE, 'kraken')


def main():
    verbose = '-v' in sys.argv
    with open(os.path.join(VECTORS, 'manifest.json')) as f:
        manifest = json.load(f)
    expected_keys = set()
    for entry in manifest:
        expected_keys.update(entry['keys'])

    kraken.stats.clear()
    failures = 0
    started = time.time()
    total = 0
    for entry in manifest:
        path = os.path.join(VECTORS, entry['name'] + '.kraken')
        with open(path, 'rb') as f:
            data = f.read()
        before = dict(kraken.stats)
        try:
            out = kraken.decompress(data, entry['raw_size'])
        except kraken.KrakenError as ex:
            print('FAIL %s: %s' % (entry['name'], ex))
            failures += 1
            continue
        digest = hashlib.sha256(out).hexdigest()
        if digest != entry['sha256']:
            print('FAIL %s: decoded %d bytes, wrong content' % (entry['name'], len(out)))
            failures += 1
            continue
        hit = set(k for k in kraken.stats if kraken.stats[k] != before.get(k, 0))
        missing = set(entry['keys']) - hit
        if missing:
            print('FAIL %s: decoded correctly but without %s' % (entry['name'], sorted(missing)))
            failures += 1
            continue
        total += len(out)
        if verbose:
            print('ok   %-40s %8d -> %8d  %s' % (entry['name'], len(data), len(out), ' '.join(sorted(hit))))

    seconds = time.time() - started
    print('%d vectors, %d failed, %.1f MB decoded in %.1fs' % (len(manifest), failures, total / 1e6, seconds))
    print('code paths exercised: ' + ' '.join(sorted(kraken.stats)))
    not_hit = expected_keys - set(kraken.stats)
    if not_hit:
        print('FAIL: code paths in the manifest that no vector reached: %s' % sorted(not_hit))
        failures += 1

    # error paths: a stream of another Oodle codec is refused by name, and
    # truncation is an error rather than a crash or wrong output
    try:
        kraken.decompress(b'\x8c\x0a' + b'\0' * 32, 100)
    except kraken.KrakenError as ex:
        if 'Mermaid' not in str(ex):
            print('FAIL: Mermaid stream refused without naming the codec: %s' % ex)
            failures += 1
    else:
        print('FAIL: a Mermaid stream was not refused')
        failures += 1
    largest = max(manifest, key=lambda m: m['size'])
    with open(os.path.join(VECTORS, largest['name'] + '.kraken'), 'rb') as f:
        data = f.read()
    for cut in (1, 2, 5, 100, len(data) // 2, len(data) - 1):
        try:
            kraken.decompress(data[:cut], largest['raw_size'])
        except kraken.KrakenError:
            continue
        print('FAIL: truncated stream (%d of %d bytes) decoded without error' % (cut, len(data)))
        failures += 1

    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
