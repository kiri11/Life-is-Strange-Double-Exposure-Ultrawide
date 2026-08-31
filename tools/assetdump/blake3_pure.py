"""BLAKE3 in the standard library alone - the fallback for the `blake3` module.

IoStore chunk metadata carries a BLAKE3 hash of the chunk payload, so writing a
package back means recomputing one.  The PyPI `blake3` package is a compiled
extension: fine under `uv run` or pip, unavailable on a machine that has no
Python of its own and is running the interpreter this fix downloaded.  Rather
than make the full-width UI step depend on being able to install a wheel, this
computes the same hash in pure Python.

It is ~200x slower than the Rust one and nobody cares: a run hashes about a
dozen widget packages of a few dozen KB each.  `patch_ui_layout.py` prefers the
real module and falls back to this one.

Single-threaded, unkeyed, matching the BLAKE3 spec (chunks of 1024 bytes,
binary tree of chaining values, extendable output).  Verified byte-for-byte
against the reference implementation - see selftest() at the bottom.
"""

OUT_LEN = 32
KEY_LEN = 32
BLOCK_LEN = 64
CHUNK_LEN = 1024

CHUNK_START = 1 << 0
CHUNK_END = 1 << 1
PARENT = 1 << 2
ROOT = 1 << 3

IV = [0x6A09E667, 0xBB67AE85, 0x3C6EF372, 0xA54FF53A,
      0x510E527F, 0x9B05688C, 0x1F83D9AB, 0x5BE0CD19]

MSG_PERMUTATION = [2, 6, 3, 10, 7, 0, 4, 13, 1, 11, 12, 5, 9, 14, 15, 8]

MASK = 0xFFFFFFFF


def _rotr(x, n):
    return ((x >> n) | (x << (32 - n))) & MASK


def _g(s, a, b, c, d, mx, my):
    s[a] = (s[a] + s[b] + mx) & MASK
    s[d] = _rotr(s[d] ^ s[a], 16)
    s[c] = (s[c] + s[d]) & MASK
    s[b] = _rotr(s[b] ^ s[c], 12)
    s[a] = (s[a] + s[b] + my) & MASK
    s[d] = _rotr(s[d] ^ s[a], 8)
    s[c] = (s[c] + s[d]) & MASK
    s[b] = _rotr(s[b] ^ s[c], 7)


def _round(s, m):
    _g(s, 0, 4, 8, 12, m[0], m[1])
    _g(s, 1, 5, 9, 13, m[2], m[3])
    _g(s, 2, 6, 10, 14, m[4], m[5])
    _g(s, 3, 7, 11, 15, m[6], m[7])
    _g(s, 0, 5, 10, 15, m[8], m[9])
    _g(s, 1, 6, 11, 12, m[10], m[11])
    _g(s, 2, 7, 8, 13, m[12], m[13])
    _g(s, 3, 4, 9, 14, m[14], m[15])


def _compress(cv, block_words, counter, block_len, flags):
    """The 7-round compression function -> 16 words."""
    state = list(cv[:8]) + IV[:4] + [counter & MASK, (counter >> 32) & MASK,
                                     block_len, flags]
    m = list(block_words)
    for i in range(7):
        _round(state, m)
        if i < 6:
            m = [m[MSG_PERMUTATION[j]] for j in range(16)]
    for i in range(8):
        state[i] ^= state[i + 8]
        state[i + 8] ^= cv[i]
    return state


def _words(block):
    """64 bytes -> 16 little-endian words (short blocks are zero-padded)."""
    if len(block) < BLOCK_LEN:
        block = block + b"\0" * (BLOCK_LEN - len(block))
    return [int.from_bytes(block[i:i + 4], "little") for i in range(0, BLOCK_LEN, 4)]


class _Output(object):
    """A node's final compression, deferred so the root can add the ROOT flag."""

    def __init__(self, cv, block_words, counter, block_len, flags):
        self.cv = cv
        self.block_words = block_words
        self.counter = counter
        self.block_len = block_len
        self.flags = flags

    def chaining_value(self):
        return _compress(self.cv, self.block_words, self.counter,
                         self.block_len, self.flags)[:8]

    def root_bytes(self, length):
        out = bytearray()
        counter = 0
        while len(out) < length:
            words = _compress(self.cv, self.block_words, counter,
                              self.block_len, self.flags | ROOT)
            for w in words:
                out += w.to_bytes(4, "little")
            counter += 1
        return bytes(out[:length])


def _chunk_output(chunk, counter):
    """Hash one chunk (<= 1024 bytes) into its deferred output node."""
    cv = IV[:]
    blocks = [chunk[i:i + BLOCK_LEN] for i in range(0, len(chunk), BLOCK_LEN)] \
        or [b""]
    for i, block in enumerate(blocks[:-1]):
        flags = CHUNK_START if i == 0 else 0
        cv = _compress(cv, _words(block), counter, BLOCK_LEN, flags)[:8]
    last = blocks[-1]
    flags = CHUNK_END | (CHUNK_START if len(blocks) == 1 else 0)
    return _Output(cv, _words(last), counter, len(last), flags)


def _parent_output(left_cv, right_cv):
    return _Output(IV[:], list(left_cv) + list(right_cv), 0, BLOCK_LEN, PARENT)


def _left_len(total):
    """Bytes in the left subtree: the largest power-of-two chunk count below it."""
    chunks = (total + CHUNK_LEN - 1) // CHUNK_LEN
    power = 1
    while power * 2 < chunks:
        power *= 2
    return power * CHUNK_LEN


def _subtree_output(data, counter):
    if len(data) <= CHUNK_LEN:
        return _chunk_output(data, counter)
    split = _left_len(len(data))
    left = _subtree_output(data[:split], counter)
    right = _subtree_output(data[split:], counter + split // CHUNK_LEN)
    return _parent_output(left.chaining_value(), right.chaining_value())


class blake3(object):
    """Minimal stand-in for blake3.blake3 - hash, update, digest, hexdigest."""

    name = "blake3"
    digest_size = OUT_LEN
    block_size = BLOCK_LEN

    def __init__(self, data=b""):
        self._buf = bytearray()
        if data:
            self.update(data)

    def update(self, data):
        self._buf += data
        return self

    def digest(self, length=OUT_LEN):
        return _subtree_output(bytes(self._buf), 0).root_bytes(length)

    def hexdigest(self, length=OUT_LEN):
        return self.digest(length).hex()

    def copy(self):
        return blake3(bytes(self._buf))


def selftest(verbose=True):
    """Differential test against the real module, plus the spec's own vectors."""
    # official test vectors: input is 0,1,2,...,250 repeating; unkeyed hash
    vectors = {
        0: "af1349b9f5f9a1a6a0404dea36dcc9499bcb25c9adc112b7cc9a93cae41f3262",
        1: "2d3adedff11b61f14c886e35afa036736dcd87a74d27b5c1510225d0f592e213",
        1023: "10108970eeda3eb932baac1428c7a2163b0e924c9a9e25b35bba72b28f70bd11",
        1024: "42214739f095a406f3fc83deb889744ac00df831c10daa55189b5d121c855af7",
        2048: "e776b6028c7cd22a4d0ba182a8bf62205d2ef576467e838ed6f2529b85fba24a",
        3072: "b98cb0ff3623be03326b373de6b9095218513e64f1ee2edd2525c7ad1e5cffd2",
    }
    ok = True
    for n, expected in sorted(vectors.items()):
        data = bytes((i % 251) for i in range(n))
        got = blake3(data).hexdigest()
        if got != expected:
            ok = False
            print("FAIL vector {}: {} != {}".format(n, got, expected))
        elif verbose:
            print("ok  vector {:5d}".format(n))
    try:
        import blake3 as _mod
        import random
        random.seed(0)
        for _ in range(60):
            n = random.choice([0, 1, 63, 64, 65, 1023, 1024, 1025, 2048, 4096,
                               random.randrange(0, 70000)])
            data = bytes(random.randrange(256) for _ in range(n))
            for length in (20, 32, 64, 131):
                mine = blake3(data).digest(length)
                theirs = _mod.blake3(data).digest(length=length)
                if mine != theirs:
                    ok = False
                    print("FAIL vs module: len={} out={}".format(n, length))
                    break
        if verbose:
            print("ok  differential against the blake3 module")
    except ImportError:
        if verbose:
            print("(the blake3 module is not installed - vectors only)")
    print("PASS" if ok else "FAILED")
    return ok


if __name__ == "__main__":
    import sys
    sys.exit(0 if selftest() else 1)
