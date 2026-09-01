"""
Pure-Python decoder for Oodle Kraken streams.

A port of the Kraken parts of ooz (https://github.com/powzix/ooz),
Copyright (C) 2016, Powzix, GPL-3.0-or-later - the same license as this
project. Only the Kraken codec (decoder type 6) is implemented: that is the
one the game's containers use, and it is all the fix needs. Mermaid, Selkie,
Leviathan, LZNA and Bitknit streams are rejected with a clear error.

Nothing here is fast - it is plain Python, byte by byte - but the fix only
ever decodes a few dozen kilobytes, so speed is beside the point. What
matters is that it runs everywhere Python does, with no native library to
find, download or load: no Oodle DLL, no ctypes, no platform differences.

    decompress(compressed_bytes, decompressed_size) -> bytes

raises KrakenError on anything it cannot decode.
"""

MASK32 = 0xFFFFFFFF

# Which code paths a decode went through, for the test-suite's coverage check:
# {'block:<type>': n, 'lz:<mode>': n, 'quantum:<kind>': n, 'huff:<table>': n}
stats = {}


def _hit(key):
    stats[key] = stats.get(key, 0) + 1


class KrakenError(Exception):
    """The stream is not a Kraken stream this decoder can read."""


def _rotl32(x, n):
    n &= 31
    if n == 0:
        return x & MASK32
    return ((x << n) | (x >> (32 - n))) & MASK32


def _bsr(x):
    """Index of the highest set bit; x must be non-zero."""
    return x.bit_length() - 1


def _bsf(x):
    """Index of the lowest set bit; x must be non-zero."""
    return (x & -x).bit_length() - 1


def _le16(buf, i):
    return int.from_bytes(buf[i:i + 2], 'little') if i >= 0 else 0


def _le32(buf, i):
    """Little-endian dword at i; bytes outside the buffer read as zero."""
    if i < 0:
        return int.from_bytes(buf[0:max(0, i + 4)], 'little') << (8 * -i) & MASK32
    return int.from_bytes(buf[i:i + 4], 'little')


def _be32(buf, i):
    """Big-endian dword at i; bytes outside the buffer read as zero."""
    if i < 0:
        chunk = (b'\0' * -i) + buf[0:max(0, i + 4)]
    else:
        chunk = buf[i:i + 4]
    return int.from_bytes(chunk[:4].ljust(4, b'\0'), 'big')


# ---------------------------------------------------------------------------
# Bit reader (MSB-first, 32-bit accumulator; forward and backward variants)
# ---------------------------------------------------------------------------

class _Bits(object):
    __slots__ = ('buf', 'p', 'p_end', 'bits', 'bitpos', 'backward')

    def __init__(self, buf, p, p_end, backward=False):
        self.buf = buf
        self.p = p
        self.p_end = p_end
        self.bits = 0
        self.bitpos = 24
        self.backward = backward
        self.refill()

    def refill(self):
        buf = self.buf
        if self.backward:
            while self.bitpos > 0:
                self.p -= 1
                b = buf[self.p] if self.p >= self.p_end else 0
                self.bits |= b << self.bitpos
                self.bitpos -= 8
        else:
            while self.bitpos > 0:
                b = buf[self.p] if self.p < self.p_end else 0
                self.bits |= b << self.bitpos
                self.bitpos -= 8
                self.p += 1
        self.bits &= MASK32

    def read_bit(self):
        self.refill()
        return self.read_bit_no_refill()

    def read_bit_no_refill(self):
        r = self.bits >> 31
        self.bits = (self.bits << 1) & MASK32
        self.bitpos += 1
        return r

    def read_no_refill(self, n):
        """n bits, n >= 1."""
        r = self.bits >> (32 - n)
        self.bits = (self.bits << n) & MASK32
        self.bitpos += n
        return r

    def read_no_refill_zero(self, n):
        """n bits, n may be zero."""
        r = (self.bits >> 1) >> (31 - n)
        self.bits = (self.bits << n) & MASK32
        self.bitpos += n
        return r

    def read_more_than_24(self, n):
        if n <= 24:
            rv = self.read_no_refill_zero(n)
        else:
            rv = self.read_no_refill(24) << (n - 24)
            self.refill()
            rv += self.read_no_refill(n - 24)
        self.refill()
        return rv

    def read_distance(self, v):
        if v < 0xF0:
            n = (v >> 4) + 4
            w = _rotl32(self.bits | 1, n)
            self.bitpos += n
            m = (2 << n) - 1
            self.bits = w & ~m & MASK32
            rv = ((w & m) << 4) + (v & 0xF) - 248
        else:
            n = v - 0xF0 + 4
            w = _rotl32(self.bits | 1, n)
            self.bitpos += n
            m = (2 << n) - 1
            self.bits = w & ~m & MASK32
            rv = 8322816 + ((w & m) << 12)
            self.refill()
            rv += self.bits >> 20
            self.bitpos += 12
            self.bits = (self.bits << 12) & MASK32
        self.refill()
        return rv

    def read_length(self):
        if self.bits == 0:
            raise KrakenError('bad length code')
        n = 31 - _bsr(self.bits)
        if n > 12:
            raise KrakenError('bad length code')
        self.bitpos += n
        self.bits = (self.bits << n) & MASK32
        self.refill()
        n += 7
        self.bitpos += n
        rv = (self.bits >> (32 - n)) - 64
        self.bits = (self.bits << n) & MASK32
        self.refill()
        return rv

    def read_fluff(self, num_symbols):
        if num_symbols == 256:
            return 0
        x = 257 - num_symbols
        if x > num_symbols:
            x = num_symbols
        x *= 2
        y = _bsr(x - 1) + 1
        v = self.bits >> (32 - y)
        z = (1 << y) - x
        if (v >> 1) >= z:
            self.bits = (self.bits << y) & MASK32
            self.bitpos += y
            return v - z
        self.bits = (self.bits << (y - 1)) & MASK32
        self.bitpos += y - 1
        return v >> 1

    def byte_pos(self):
        """Byte position of the next unread whole byte (forward reader)."""
        return self.p - ((24 - self.bitpos) >> 3)

    def rewind_to(self, p, bitpos):
        """Restart at byte p with bitpos bits of it already consumed."""
        self.bitpos = 24
        self.p = p
        self.bits = 0
        self.refill()
        self.bits = (self.bits << bitpos) & MASK32
        self.bitpos += bitpos


class _Bits2(object):
    """Byte-position reader used by the Golomb-Rice code length decoders."""
    __slots__ = ('p', 'p_end', 'bitpos')

    def __init__(self, bits):
        # derived from a _Bits reader: the byte holding the next unread bit,
        # and how many bits of it are already consumed
        self.bitpos = (bits.bitpos - 24) & 7
        self.p_end = bits.p_end
        self.p = bits.p - ((24 - bits.bitpos + 7) >> 3)


# ---------------------------------------------------------------------------
# Huffman tables
# ---------------------------------------------------------------------------

_RICE_VALUE = [
    0x80000000, 0x00000007, 0x10000006, 0x00000006, 0x20000005, 0x00000105, 0x10000005, 0x00000005,
    0x30000004, 0x00000204, 0x10000104, 0x00000104, 0x20000004, 0x00010004, 0x10000004, 0x00000004,
    0x40000003, 0x00000303, 0x10000203, 0x00000203, 0x20000103, 0x00010103, 0x10000103, 0x00000103,
    0x30000003, 0x00020003, 0x10010003, 0x00010003, 0x20000003, 0x01000003, 0x10000003, 0x00000003,
    0x50000002, 0x00000402, 0x10000302, 0x00000302, 0x20000202, 0x00010202, 0x10000202, 0x00000202,
    0x30000102, 0x00020102, 0x10010102, 0x00010102, 0x20000102, 0x01000102, 0x10000102, 0x00000102,
    0x40000002, 0x00030002, 0x10020002, 0x00020002, 0x20010002, 0x01010002, 0x10010002, 0x00010002,
    0x30000002, 0x02000002, 0x11000002, 0x01000002, 0x20000002, 0x00000012, 0x10000002, 0x00000002,
    0x60000001, 0x00000501, 0x10000401, 0x00000401, 0x20000301, 0x00010301, 0x10000301, 0x00000301,
    0x30000201, 0x00020201, 0x10010201, 0x00010201, 0x20000201, 0x01000201, 0x10000201, 0x00000201,
    0x40000101, 0x00030101, 0x10020101, 0x00020101, 0x20010101, 0x01010101, 0x10010101, 0x00010101,
    0x30000101, 0x02000101, 0x11000101, 0x01000101, 0x20000101, 0x00000111, 0x10000101, 0x00000101,
    0x50000001, 0x00040001, 0x10030001, 0x00030001, 0x20020001, 0x01020001, 0x10020001, 0x00020001,
    0x30010001, 0x02010001, 0x11010001, 0x01010001, 0x20010001, 0x00010011, 0x10010001, 0x00010001,
    0x40000001, 0x03000001, 0x12000001, 0x02000001, 0x21000001, 0x01000011, 0x11000001, 0x01000001,
    0x30000001, 0x00000021, 0x10000011, 0x00000011, 0x20000001, 0x00001001, 0x10000001, 0x00000001,
    0x70000000, 0x00000600, 0x10000500, 0x00000500, 0x20000400, 0x00010400, 0x10000400, 0x00000400,
    0x30000300, 0x00020300, 0x10010300, 0x00010300, 0x20000300, 0x01000300, 0x10000300, 0x00000300,
    0x40000200, 0x00030200, 0x10020200, 0x00020200, 0x20010200, 0x01010200, 0x10010200, 0x00010200,
    0x30000200, 0x02000200, 0x11000200, 0x01000200, 0x20000200, 0x00000210, 0x10000200, 0x00000200,
    0x50000100, 0x00040100, 0x10030100, 0x00030100, 0x20020100, 0x01020100, 0x10020100, 0x00020100,
    0x30010100, 0x02010100, 0x11010100, 0x01010100, 0x20010100, 0x00010110, 0x10010100, 0x00010100,
    0x40000100, 0x03000100, 0x12000100, 0x02000100, 0x21000100, 0x01000110, 0x11000100, 0x01000100,
    0x30000100, 0x00000120, 0x10000110, 0x00000110, 0x20000100, 0x00001100, 0x10000100, 0x00000100,
    0x60000000, 0x00050000, 0x10040000, 0x00040000, 0x20030000, 0x01030000, 0x10030000, 0x00030000,
    0x30020000, 0x02020000, 0x11020000, 0x01020000, 0x20020000, 0x00020010, 0x10020000, 0x00020000,
    0x40010000, 0x03010000, 0x12010000, 0x02010000, 0x21010000, 0x01010010, 0x11010000, 0x01010000,
    0x30010000, 0x00010020, 0x10010010, 0x00010010, 0x20010000, 0x00011000, 0x10010000, 0x00010000,
    0x50000000, 0x04000000, 0x13000000, 0x03000000, 0x22000000, 0x02000010, 0x12000000, 0x02000000,
    0x31000000, 0x01000020, 0x11000010, 0x01000010, 0x21000000, 0x01001000, 0x11000000, 0x01000000,
    0x40000000, 0x00000030, 0x10000020, 0x00000020, 0x20000010, 0x00001010, 0x10000010, 0x00000010,
    0x30000000, 0x00002000, 0x10001000, 0x00001000, 0x20000000, 0x00100000, 0x10000000, 0x00000000,
]

_RICE_LEN = [
    0, 1, 1, 2, 1, 2, 2, 3, 1, 2, 2, 3, 2, 3, 3, 4, 1, 2, 2, 3, 2, 3, 3, 4, 2, 3, 3, 4, 3, 4, 4, 5,
    1, 2, 2, 3, 2, 3, 3, 4, 2, 3, 3, 4, 3, 4, 4, 5, 2, 3, 3, 4, 3, 4, 4, 5, 3, 4, 4, 5, 4, 5, 5, 6,
    1, 2, 2, 3, 2, 3, 3, 4, 2, 3, 3, 4, 3, 4, 4, 5, 2, 3, 3, 4, 3, 4, 4, 5, 3, 4, 4, 5, 4, 5, 5, 6,
    2, 3, 3, 4, 3, 4, 4, 5, 3, 4, 4, 5, 4, 5, 5, 6, 3, 4, 4, 5, 4, 5, 5, 6, 4, 5, 5, 6, 5, 6, 6, 7,
    1, 2, 2, 3, 2, 3, 3, 4, 2, 3, 3, 4, 3, 4, 4, 5, 2, 3, 3, 4, 3, 4, 4, 5, 3, 4, 4, 5, 4, 5, 5, 6,
    2, 3, 3, 4, 3, 4, 4, 5, 3, 4, 4, 5, 4, 5, 5, 6, 3, 4, 4, 5, 4, 5, 5, 6, 4, 5, 5, 6, 5, 6, 6, 7,
    2, 3, 3, 4, 3, 4, 4, 5, 3, 4, 4, 5, 4, 5, 5, 6, 3, 4, 4, 5, 4, 5, 5, 6, 4, 5, 5, 6, 5, 6, 6, 7,
    3, 4, 4, 5, 4, 5, 5, 6, 4, 5, 5, 6, 5, 6, 6, 7, 4, 5, 5, 6, 5, 6, 6, 7, 5, 6, 6, 7, 6, 7, 7, 8,
]

_BITMASKS = [(2 << i) - 1 for i in range(32)]

# 11-bit bit reversal: the Huffman table is built in canonical (MSB-first)
# order and looked up with the low 11 bits of an LSB-first accumulator
_REV11 = [int('{:011b}'.format(i)[::-1], 2) for i in range(2048)]

_CODE_PREFIX_ORG = [0x0, 0x0, 0x2, 0x6, 0xE, 0x1E, 0x3E, 0x7E, 0xFE, 0x1FE, 0x2FE, 0x3FE]


def _golomb_rice_lengths(buf, dst, size, br):
    """DecodeGolombRiceLengths: unary-coded values, MSB-first."""
    p, p_end, bitpos = br.p, br.p_end, br.bitpos
    if p >= p_end:
        raise KrakenError('truncated code lengths')
    count = -bitpos
    v = buf[p] & (255 >> bitpos)
    p += 1
    di = 0
    while True:
        if v == 0:
            count += 8
        else:
            x = _RICE_VALUE[v]
            lo = (count + (x & 0x0f0f0f0f)) & MASK32
            hi = (x >> 4) & 0x0f0f0f0f
            dst[di] = lo & 0xFF
            dst[di + 1] = (lo >> 8) & 0xFF
            dst[di + 2] = (lo >> 16) & 0xFF
            dst[di + 3] = (lo >> 24) & 0xFF
            dst[di + 4] = hi & 0xFF
            dst[di + 5] = (hi >> 8) & 0xFF
            dst[di + 6] = (hi >> 16) & 0xFF
            dst[di + 7] = (hi >> 24) & 0xFF
            di += _RICE_LEN[v]
            if di >= size:
                break
            count = x >> 28
        if p >= p_end:
            raise KrakenError('truncated code lengths')
        v = buf[p]
        p += 1
    if di > size:                       # went too far, step back
        n = di - size
        while n:
            v &= v - 1
            n -= 1
    bitpos = 0
    if not (v & 1):                     # byte not finished
        p -= 1
        bitpos = 8 - _bsf(v)
    br.p = p
    br.bitpos = bitpos


def _golomb_rice_bits(buf, dst, size, bitcount, br):
    """DecodeGolombRiceBits: |bitcount| extra bits per value, MSB-first."""
    if bitcount == 0:
        return
    p, bitpos = br.p, br.bitpos
    bits_required = bitpos + bitcount * size
    bytes_required = (bits_required + 7) >> 3
    if bytes_required > br.p_end - p:
        raise KrakenError('truncated code lengths')
    br.p = p + (bits_required >> 3)
    br.bitpos = bits_required & 7
    acc = 0
    have = 0
    for i in range(size):
        while have < bitcount:
            if bitpos:
                acc = (acc << (8 - bitpos)) | (buf[p] & (255 >> bitpos))
                have += 8 - bitpos
                bitpos = 0
            else:
                acc = (acc << 8) | buf[p]
                have += 8
            p += 1
        have -= bitcount
        val = (acc >> have) & ((1 << bitcount) - 1)
        acc &= (1 << have) - 1
        dst[i] = (dst[i] * (1 << bitcount) + val) & 0xFF


def _convert_to_ranges(num_symbols, P, symlen, sl, bits):
    """Huff_ConvertToRanges -> list of (symbol, num)."""
    num_ranges = P >> 1
    sym_idx = 0
    if P & 1:
        bits.refill()
        v = symlen[sl]
        sl += 1
        if v >= 8:
            raise KrakenError('bad symbol ranges')
        sym_idx = bits.read_no_refill(v + 1) + (1 << (v + 1)) - 1
    syms_used = 0
    ranges = []
    for i in range(num_ranges):
        bits.refill()
        v = symlen[sl]
        if v >= 9:
            raise KrakenError('bad symbol ranges')
        num = bits.read_no_refill_zero(v) + (1 << v)
        v = symlen[sl + 1]
        if v >= 8:
            raise KrakenError('bad symbol ranges')
        space = bits.read_no_refill(v + 1) + (1 << (v + 1)) - 1
        ranges.append((sym_idx, num))
        syms_used += num
        sym_idx += num + space
        sl += 2
    if sym_idx >= 256 or syms_used >= num_symbols or sym_idx + num_symbols - syms_used > 256:
        raise KrakenError('bad symbol ranges')
    ranges.append((sym_idx, num_symbols - syms_used))
    return ranges


def _read_code_lengths_old(bits, syms, code_prefix):
    if bits.read_bit_no_refill():
        sym = 0
        num_symbols = 0
        avg_bits_x4 = 32
        forced_bits = bits.read_no_refill(2)
        thres = 1 << (31 - (20 >> forced_bits))
        skip_zeros = bits.read_bit()
        while True:
            if not skip_zeros:
                if not (bits.bits & 0xff000000):
                    raise KrakenError('bad code lengths')
                lz = 31 - _bsr(bits.bits)
                sym += bits.read_no_refill(2 * (lz + 1)) - 2 + 1
                if sym >= 256:
                    break
            skip_zeros = False
            bits.refill()
            if not (bits.bits & 0xff000000):
                raise KrakenError('bad code lengths')
            lz = 31 - _bsr(bits.bits)
            n = bits.read_no_refill(2 * (lz + 1)) - 2 + 1
            if sym + n > 256:
                raise KrakenError('bad code lengths')
            bits.refill()
            num_symbols += n
            while n:
                if bits.bits < thres:
                    raise KrakenError('bad code lengths')
                lz = 31 - _bsr(bits.bits)
                v = bits.read_no_refill(lz + forced_bits + 1) + ((lz - 1) << forced_bits)
                codelen = (-(v & 1) ^ (v >> 1)) + ((avg_bits_x4 + 2) >> 2)
                if codelen < 1 or codelen > 11:
                    raise KrakenError('bad code lengths')
                avg_bits_x4 = codelen + ((3 * avg_bits_x4 + 2) >> 2)
                bits.refill()
                syms[code_prefix[codelen]] = sym
                code_prefix[codelen] += 1
                sym += 1
                n -= 1
            if sym == 256:
                break
        if sym != 256 or num_symbols < 2:
            raise KrakenError('bad code lengths')
        return num_symbols
    # sparse symbol encoding
    num_symbols = bits.read_no_refill(8)
    if num_symbols == 0:
        raise KrakenError('bad code lengths')
    if num_symbols == 1:
        syms[0] = bits.read_no_refill(8)
    else:
        codelen_bits = bits.read_no_refill(3)
        if codelen_bits > 4:
            raise KrakenError('bad code lengths')
        for i in range(num_symbols):
            bits.refill()
            sym = bits.read_no_refill(8)
            codelen = bits.read_no_refill_zero(codelen_bits) + 1
            if codelen > 11:
                raise KrakenError('bad code lengths')
            syms[code_prefix[codelen]] = sym
            code_prefix[codelen] += 1
    return num_symbols


def _read_code_lengths_new(bits, syms, code_prefix):
    forced_bits = bits.read_no_refill(2)
    num_symbols = bits.read_no_refill(8) + 1
    fluff = bits.read_fluff(num_symbols)

    code_len = bytearray(512 + 16)
    br2 = _Bits2(bits)
    _golomb_rice_lengths(bits.buf, code_len, num_symbols + fluff, br2)
    for i in range(num_symbols + fluff, num_symbols + fluff + 16):
        code_len[i] = 0
    _golomb_rice_bits(bits.buf, code_len, num_symbols, forced_bits, br2)

    bits.rewind_to(br2.p, br2.bitpos)

    running_sum = 0x1e
    for i in range(num_symbols):
        v = code_len[i]
        v = -(v & 1) ^ (v >> 1)
        cl = v + ((running_sum & MASK32) >> 2) + 1
        if cl < 1 or cl > 11:
            raise KrakenError('bad code lengths')
        code_len[i] = cl
        running_sum = (running_sum + v) & MASK32

    ranges = _convert_to_ranges(num_symbols, fluff, code_len, num_symbols, bits)
    cp = 0
    for sym, n in ranges:
        while n:
            cl = code_len[cp]
            cp += 1
            syms[code_prefix[cl]] = sym
            code_prefix[cl] += 1
            sym += 1
            n -= 1
    return num_symbols


def _make_lut(code_prefix, syms):
    """Huff_MakeLut + ReverseBitsArray2048 -> (bits2len, bits2sym), LSB-first."""
    lens = [0] * 2048
    symbols = [0] * 2048
    slot = 0
    for i in range(1, 11):
        start = _CODE_PREFIX_ORG[i]
        count = code_prefix[i] - start
        if count:
            step = 1 << (11 - i)
            num_to_set = count << (11 - i)
            if slot + num_to_set > 2048:
                raise KrakenError('bad Huffman table')
            for j in range(count):
                s = syms[start + j]
                for k in range(slot, slot + step):
                    lens[k] = i
                    symbols[k] = s
                slot += step
    count = code_prefix[11] - _CODE_PREFIX_ORG[11]
    if count:
        if slot + count > 2048:
            raise KrakenError('bad Huffman table')
        start = _CODE_PREFIX_ORG[11]
        for j in range(count):
            lens[slot + j] = 11
            symbols[slot + j] = syms[start + j]
        slot += count
    if slot != 2048:
        raise KrakenError('bad Huffman table')
    rev = _REV11
    return [lens[rev[i]] for i in range(2048)], [symbols[rev[i]] for i in range(2048)]


def _decode_bytes_core(buf, src, src_mid, src_end, lut_len, lut_sym, out, dst, dst_end):
    """Three interleaved Huffman streams: forward, middle forward, end backward."""
    src_mid_org = src_mid
    src_bits = src_mid_bits = src_end_bits = 0
    src_bitpos = src_mid_bitpos = src_end_bitpos = 0
    if src > src_mid:
        raise KrakenError('bad Huffman stream')
    while dst < dst_end:
        d = src_mid - src
        if d <= 1:
            if d == 1:
                src_bits |= buf[src] << src_bitpos
        else:
            src_bits |= (buf[src] | (buf[src + 1] << 8)) << src_bitpos
        k = src_bits & 0x7FF
        n = lut_len[k]
        src_bitpos -= n
        src_bits >>= n
        out[dst] = lut_sym[k]
        dst += 1
        src += (7 - src_bitpos) >> 3
        src_bitpos &= 7

        if dst < dst_end:
            d = src_end - src_mid
            if d <= 1:
                if d == 1:
                    src_end_bits |= buf[src_mid] << src_end_bitpos
                    src_mid_bits |= buf[src_mid] << src_mid_bitpos
            else:
                src_end_bits |= (buf[src_end - 1] | (buf[src_end - 2] << 8)) << src_end_bitpos
                src_mid_bits |= (buf[src_mid] | (buf[src_mid + 1] << 8)) << src_mid_bitpos
            k = src_end_bits & 0x7FF
            n = lut_len[k]
            out[dst] = lut_sym[k]
            dst += 1
            src_end_bitpos -= n
            src_end_bits >>= n
            src_end -= (7 - src_end_bitpos) >> 3
            src_end_bitpos &= 7
            if dst < dst_end:
                k = src_mid_bits & 0x7FF
                n = lut_len[k]
                out[dst] = lut_sym[k]
                dst += 1
                src_mid_bitpos -= n
                src_mid_bits >>= n
                src_mid += (7 - src_mid_bitpos) >> 3
                src_mid_bitpos &= 7
        if src > src_mid or src_mid > src_end:
            raise KrakenError('bad Huffman stream')
    if src != src_mid_org or src_end != src_mid:
        raise KrakenError('bad Huffman stream')


def _decode_bytes_type12(buf, src, src_size, out, output_size, kind):
    src_end = src + src_size
    bits = _Bits(buf, src, src_end)
    code_prefix = list(_CODE_PREFIX_ORG)
    syms = [0] * 1280
    if not bits.read_bit_no_refill():
        _hit('huff:old')
        num_syms = _read_code_lengths_old(bits, syms, code_prefix)
    elif not bits.read_bit_no_refill():
        _hit('huff:new')
        num_syms = _read_code_lengths_new(bits, syms, code_prefix)
    else:
        raise KrakenError('bad Huffman header')
    if num_syms < 1:
        raise KrakenError('bad Huffman header')
    src = bits.p - ((24 - bits.bitpos) >> 3)

    if num_syms == 1:
        for i in range(output_size):
            out[i] = syms[0]
        return src_size

    lut_len, lut_sym = _make_lut(code_prefix, syms)

    if kind == 1:
        if src + 3 > src_end:
            raise KrakenError('truncated Huffman stream')
        split_mid = _le16(buf, src)
        src += 2
        _decode_bytes_core(buf, src, src + split_mid, src_end, lut_len, lut_sym,
                           out, 0, output_size)
    else:
        if src + 6 > src_end:
            raise KrakenError('truncated Huffman stream')
        half = (output_size + 1) >> 1
        split_mid = _le32(buf, src) & 0xFFFFFF
        src += 3
        if split_mid > src_end - src:
            raise KrakenError('bad Huffman stream')
        src_mid = src + split_mid
        split_left = _le16(buf, src)
        src += 2
        if src_mid - src < split_left + 2 or src_end - src_mid < 3:
            raise KrakenError('bad Huffman stream')
        split_right = _le16(buf, src_mid)
        if src_end - (src_mid + 2) < split_right + 2:
            raise KrakenError('bad Huffman stream')
        _decode_bytes_core(buf, src, src + split_left, src_mid, lut_len, lut_sym,
                           out, 0, half)
        _decode_bytes_core(buf, src_mid + 2, src_mid + 2 + split_right, src_end,
                           lut_len, lut_sym, out, half, output_size)
    return src_size


# ---------------------------------------------------------------------------
# RLE
# ---------------------------------------------------------------------------

def _decode_rle(buf, src, src_size, out, dst_size):
    if src_size <= 1:
        if src_size != 1:
            raise KrakenError('bad RLE stream')
        for i in range(dst_size):
            out[i] = buf[src]
        return 1
    if buf[src]:
        # the command buffer is itself entropy coded, then raw bytes follow
        n, data = _decode_bytes(buf, src, src + src_size, 0x6C000)
        cmd = bytearray(data) + buf[src + n:src + src_size]
    else:
        cmd = buf[src + 1:src + src_size]
    cp, ce = 0, len(cmd)
    dst = 0
    rle_byte = 0
    while cp < ce:
        c = cmd[ce - 1]
        if c == 0 or c >= 0x30:
            ce -= 1
            to_copy = (~c) & 0xF
            to_rle = c >> 4
        elif c >= 0x10:
            data = _le16(cmd, ce - 2) - 4096
            ce -= 2
            to_copy = data & 0x3F
            to_rle = data >> 6
        elif c == 1:
            rle_byte = cmd[cp]
            cp += 1
            ce -= 1
            continue
        elif c >= 9:
            to_rle = (_le16(cmd, ce - 2) - 0x8ff) * 128
            ce -= 2
            to_copy = 0
        else:
            to_copy = (_le16(cmd, ce - 2) - 511) * 64
            ce -= 2
            to_rle = 0
        if dst_size - dst < to_copy + to_rle or ce - cp < to_copy:
            raise KrakenError('bad RLE stream')
        out[dst:dst + to_copy] = cmd[cp:cp + to_copy]
        cp += to_copy
        dst += to_copy
        if to_rle:
            out[dst:dst + to_rle] = bytes([rle_byte]) * to_rle
            dst += to_rle
    if ce != cp or dst != dst_size:
        raise KrakenError('bad RLE stream')
    return src_size


# ---------------------------------------------------------------------------
# TANS
# ---------------------------------------------------------------------------

def _tans_decode_table(bits, L_bits):
    """-> (A, B): weight-1 symbols, and (symbol << 16 | weight) for the rest."""
    bits.refill()
    A, B = [], []
    L = 1 << L_bits
    if bits.read_bit_no_refill():
        Q = bits.read_no_refill(3)
        num_symbols = bits.read_no_refill(8) + 1
        if num_symbols < 2:
            raise KrakenError('bad TANS table')
        fluff = bits.read_fluff(num_symbols)
        total = fluff + num_symbols
        rice = bytearray(512 + 16)
        br2 = _Bits2(bits)
        _golomb_rice_lengths(bits.buf, rice, total, br2)
        for i in range(total, total + 16):
            rice[i] = 0
        bits.rewind_to(br2.p, br2.bitpos)
        ranges = _convert_to_ranges(num_symbols, fluff, rice, num_symbols, bits)
        bits.refill()
        cur = 0
        average = 6
        somesum = 0
        for symbol, num in ranges:
            while num:
                bits.refill()
                nextra = Q + rice[cur]
                cur += 1
                if nextra > 15:
                    raise KrakenError('bad TANS table')
                v = bits.read_no_refill_zero(nextra) + (1 << nextra) - (1 << Q)
                average_div4 = average >> 2
                limit = 2 * average_div4
                if v <= limit:
                    v = average_div4 + (-(v & 1) ^ (v >> 1))
                if limit > v:
                    limit = v
                v += 1
                average += limit - average_div4
                if v == 1:
                    A.append(symbol)
                else:
                    B.append((symbol << 16) + v)
                somesum += v
                symbol += 1
                num -= 1
        if somesum != L:
            raise KrakenError('bad TANS table')
        return A, B

    seen = [False] * 256
    count = bits.read_no_refill(3) + 1
    bits_per_sym = _bsr(L_bits) + 1
    max_delta_bits = bits.read_no_refill(bits_per_sym)
    if max_delta_bits == 0 or max_delta_bits > L_bits:
        raise KrakenError('bad TANS table')
    weight = 0
    total_weights = 0
    while count:
        bits.refill()
        sym = bits.read_no_refill(8)
        if seen[sym]:
            raise KrakenError('bad TANS table')
        delta = bits.read_no_refill(max_delta_bits)
        weight += delta
        if weight == 0:
            raise KrakenError('bad TANS table')
        seen[sym] = True
        if weight == 1:
            A.append(sym)
        else:
            B.append((sym << 16) + weight)
        total_weights += weight
        count -= 1
    bits.refill()
    sym = bits.read_no_refill(8)
    if seen[sym]:
        raise KrakenError('bad TANS table')
    if L - total_weights < weight or L - total_weights <= 1:
        raise KrakenError('bad TANS table')
    B.append((sym << 16) + (L - total_weights))
    A.sort()
    B.sort()
    return A, B


def _tans_init_lut(A, B, L_bits):
    """-> list of (x, bits_x, symbol, w) per state."""
    L = 1 << L_bits
    a_used = len(A)
    slots_left = L - a_used
    sa = slots_left >> 2
    ptr = [0, 0, 0, 0]
    sb = sa + (1 if (slots_left & 3) > 0 else 0)
    ptr[1] = sb
    sb += sa + (1 if (slots_left & 3) > 1 else 0)
    ptr[2] = sb
    sb += sa + (1 if (slots_left & 3) > 2 else 0)
    ptr[3] = sb
    lut = [None] * L
    for i, sym in enumerate(A):
        lut[slots_left + i] = (L - 1, L_bits, sym, 0)
    weights_sum = 0
    for entry in B:
        weight = entry & 0xFFFF
        symbol = entry >> 16
        if weight > 4:
            sym_bits = _bsr(weight)
            Z = L_bits - sym_bits
            x = (1 << Z) - 1
            bits_x = Z
            w = (L - 1) & (weight << Z)
            what_to_add = 1 << Z
            X = (1 << (sym_bits + 1)) - weight
            for j in range(4):
                d = ptr[j]
                Y = (weight + ((weights_sum - j - 1) & 3)) >> 2
                if X >= Y:
                    for _ in range(Y):
                        lut[d] = (x, bits_x, symbol, w)
                        d += 1
                        w += what_to_add
                    X -= Y
                else:
                    for _ in range(X):
                        lut[d] = (x, bits_x, symbol, w)
                        d += 1
                        w += what_to_add
                    Z -= 1
                    what_to_add >>= 1
                    bits_x = Z
                    w = 0
                    x >>= 1
                    for _ in range(Y - X):
                        lut[d] = (x, bits_x, symbol, w)
                        d += 1
                        w += what_to_add
                    X = weight
                ptr[j] = d
        else:
            if weight <= 0:
                raise KrakenError('bad TANS table')
            bmask = ((1 << weight) - 1) << (weights_sum & 3)
            bmask |= bmask >> 4
            n = weight
            ww = weight
            while n:
                idx = _bsf(bmask)
                bmask &= bmask - 1
                d = ptr[idx]
                ptr[idx] += 1
                weight_bits = _bsr(ww)
                shift = L_bits - weight_bits
                lut[d] = ((1 << shift) - 1, shift, symbol, (L - 1) & (ww << shift))
                ww += 1
                n -= 1
        weights_sum += weight
    if any(e is None for e in lut):
        raise KrakenError('bad TANS table')
    return lut


def _decode_tans(buf, src, src_size, out, dst_size):
    if src_size < 8 or dst_size < 5:
        raise KrakenError('bad TANS stream')
    src_end = src + src_size
    br = _Bits(buf, src, src_end)
    if br.read_bit_no_refill():
        raise KrakenError('bad TANS stream')
    L_bits = br.read_no_refill(2) + 8
    A, B = _tans_decode_table(br, L_bits)
    src = br.p - ((24 - br.bitpos) >> 3)
    if src >= src_end:
        raise KrakenError('bad TANS stream')
    lut = _tans_init_lut(A, B, L_bits)

    dst_end = dst_size - 5
    L_mask = (1 << L_bits) - 1
    bits_f = _le32(buf, src)
    src += 4
    bits_b = _be32(buf, src_end - 4)
    src_end -= 4
    bitpos_f = bitpos_b = 32

    s0 = bits_f & L_mask
    s1 = bits_b & L_mask
    bits_f >>= L_bits
    bitpos_f -= L_bits
    bits_b >>= L_bits
    bitpos_b -= L_bits
    s2 = bits_f & L_mask
    s3 = bits_b & L_mask
    bits_f >>= L_bits
    bitpos_f -= L_bits
    bits_b >>= L_bits
    bitpos_b -= L_bits

    bits_f = (bits_f | (_le32(buf, src) << bitpos_f)) & MASK32
    src += (31 - bitpos_f) >> 3
    bitpos_f |= 24
    s4 = bits_f & L_mask
    bits_f >>= L_bits
    bitpos_f -= L_bits

    ptr_f = src - (bitpos_f >> 3)
    bitpos_f &= 7
    ptr_b = src_end + (bitpos_b >> 3)
    bitpos_b &= 7

    if ptr_f > ptr_b:
        raise KrakenError('bad TANS stream')

    states = [s0, s1, s2, s3, s4]
    dst = 0
    if dst < dst_end:
        while True:
            done = False
            # forward: states 0,1 then 2,3 then 4; backward the same
            for group in ((0, 1), (2, 3), (4,)):
                bits_f = (bits_f | (_le32(buf, ptr_f) << bitpos_f)) & MASK32
                ptr_f += (31 - bitpos_f) >> 3
                bitpos_f |= 24
                for si in group:
                    x, bits_x, symbol, w = lut[states[si]]
                    out[dst] = symbol
                    dst += 1
                    bitpos_f -= bits_x
                    states[si] = (bits_f & x) + w
                    bits_f >>= bits_x
                    if dst >= dst_end:
                        done = True
                        break
                if done:
                    break
            if done:
                break
            for group in ((0, 1), (2, 3), (4,)):
                bits_b = (bits_b | (_be32(buf, ptr_b - 4) << bitpos_b)) & MASK32
                ptr_b -= (31 - bitpos_b) >> 3
                bitpos_b |= 24
                for si in group:
                    x, bits_x, symbol, w = lut[states[si]]
                    out[dst] = symbol
                    dst += 1
                    bitpos_b -= bits_x
                    states[si] = (bits_b & x) + w
                    bits_b >>= bits_x
                    if dst >= dst_end:
                        done = True
                        break
                if done:
                    break
            if done:
                break

    if ptr_b - ptr_f + (bitpos_f >> 3) + (bitpos_b >> 3) != 0:
        raise KrakenError('bad TANS stream')
    if (states[0] | states[1] | states[2] | states[3] | states[4]) & ~0xFF:
        raise KrakenError('bad TANS stream')
    for i in range(5):
        out[dst_end + i] = states[i]
    return src_size


# ---------------------------------------------------------------------------
# Entropy-coded byte arrays
# ---------------------------------------------------------------------------

def _block_size(buf, src, src_end, dest_capacity):
    """Kraken_GetBlockSize -> decoded size of the array at src."""
    if src_end - src < 2:
        raise KrakenError('truncated block')
    chunk_type = (buf[src] >> 4) & 7
    if chunk_type == 0:
        if buf[src] >= 0x80:
            src_size = ((buf[src] << 8) | buf[src + 1]) & 0xFFF
            src += 2
        else:
            if src_end - src < 3:
                raise KrakenError('truncated block')
            src_size = (buf[src] << 16) | (buf[src + 1] << 8) | buf[src + 2]
            if src_size & ~0x3FFFF:
                raise KrakenError('bad block')
            src += 3
        if src_size > dest_capacity or src_end - src < src_size:
            raise KrakenError('bad block')
        return src_size
    if chunk_type >= 6:
        raise KrakenError('bad block')
    if buf[src] >= 0x80:
        if src_end - src < 3:
            raise KrakenError('truncated block')
        b = (buf[src] << 16) | (buf[src + 1] << 8) | buf[src + 2]
        src_size = b & 0x3FF
        dst_size = src_size + ((b >> 10) & 0x3FF) + 1
        src += 3
    else:
        if src_end - src < 5:
            raise KrakenError('truncated block')
        b = _be32(buf, src + 1)
        src_size = b & 0x3FFFF
        dst_size = (((b >> 18) | (buf[src] << 14)) & 0x3FFFF) + 1
        if src_size >= dst_size:
            raise KrakenError('bad block')
        src += 5
    if src_end - src < src_size or dst_size > dest_capacity:
        raise KrakenError('bad block')
    return dst_size


def _decode_bytes(buf, src, src_end, output_size):
    """Kraken_DecodeBytes -> (bytes consumed, decoded bytes)."""
    src_org = src
    if src_end - src < 2:
        raise KrakenError('truncated block')
    chunk_type = (buf[src] >> 4) & 7
    if chunk_type == 0:
        if buf[src] >= 0x80:
            src_size = ((buf[src] << 8) | buf[src + 1]) & 0xFFF
            src += 2
        else:
            if src_end - src < 3:
                raise KrakenError('truncated block')
            src_size = (buf[src] << 16) | (buf[src + 1] << 8) | buf[src + 2]
            if src_size & ~0x3FFFF:
                raise KrakenError('bad block')
            src += 3
        if src_size > output_size or src_end - src < src_size:
            raise KrakenError('bad block')
        _hit('block:0')
        return src + src_size - src_org, bytes(buf[src:src + src_size])

    if buf[src] >= 0x80:
        if src_end - src < 3:
            raise KrakenError('truncated block')
        b = (buf[src] << 16) | (buf[src + 1] << 8) | buf[src + 2]
        src_size = b & 0x3FF
        dst_size = src_size + ((b >> 10) & 0x3FF) + 1
        src += 3
    else:
        if src_end - src < 5:
            raise KrakenError('truncated block')
        b = _be32(buf, src + 1)
        src_size = b & 0x3FFFF
        dst_size = (((b >> 18) | (buf[src] << 14)) & 0x3FFFF) + 1
        if src_size >= dst_size:
            raise KrakenError('bad block')
        src += 5
    if src_end - src < src_size or dst_size > output_size:
        raise KrakenError('bad block')

    out = bytearray(dst_size)
    _hit('block:%d' % chunk_type)
    if chunk_type == 2 or chunk_type == 4:
        used = _decode_bytes_type12(buf, src, src_size, out, dst_size, chunk_type >> 1)
    elif chunk_type == 5:
        used = _decode_recursive(buf, src, src_size, out, dst_size)
    elif chunk_type == 3:
        used = _decode_rle(buf, src, src_size, out, dst_size)
    elif chunk_type == 1:
        used = _decode_tans(buf, src, src_size, out, dst_size)
    else:
        raise KrakenError('unknown block type %d' % chunk_type)
    if used != src_size:
        raise KrakenError('block size mismatch')
    return src + src_size - src_org, bytes(out)


def _decode_recursive(buf, src, src_size, out, output_size):
    src_org = src
    src_end = src + src_size
    if src_size < 6:
        raise KrakenError('bad recursive block')
    n = buf[src] & 0x7F
    if n < 2:
        raise KrakenError('bad recursive block')
    if not (buf[src] & 0x80):
        src += 1
        pos = 0
        while n:
            used, data = _decode_bytes(buf, src, src_end, output_size - pos)
            out[pos:pos + len(data)] = data
            pos += len(data)
            src += used
            n -= 1
        if pos != output_size:
            raise KrakenError('bad recursive block')
        return src - src_org
    used, arrays, total = _decode_multi_array(buf, src, src_end, 1, output_size)
    if total != output_size:
        raise KrakenError('bad recursive block')
    out[0:total] = arrays[0]
    return used


def _decode_multi_array(buf, src, src_end, array_count, dst_capacity):
    """Kraken_DecodeMultiArray -> (bytes consumed, [arrays], total size)."""
    src_org = src
    if src_end - src < 4:
        raise KrakenError('bad multi array')
    num_arrays_in_file = buf[src]
    src += 1
    if not (num_arrays_in_file & 0x80):
        raise KrakenError('bad multi array')
    num_arrays_in_file &= 0x3F

    total_size = 0
    _hit('multi:%s' % ('plain' if num_arrays_in_file == 0 else 'interleaved'))
    if num_arrays_in_file == 0:
        arrays = []
        for i in range(array_count):
            used, data = _decode_bytes(buf, src, src_end, dst_capacity - total_size)
            arrays.append(data)
            src += used
            total_size += len(data)
        return src - src_org, arrays, total_size

    entropy = []
    for i in range(num_arrays_in_file):
        used, data = _decode_bytes(buf, src, src_end, 0x6C000)
        entropy.append(data)
        total_size += len(data)
        src += used

    if src_end - src < 3:
        raise KrakenError('bad multi array')
    Q = _le16(buf, src)
    src += 2

    num_indexes = _block_size(buf, src, src_end, total_size)
    num_lens = num_indexes - array_count
    if num_lens < 1:
        raise KrakenError('bad multi array')

    if Q & 0x8000:
        used, idx = _decode_bytes(buf, src, src_end, num_indexes)
        if len(idx) != num_indexes:
            raise KrakenError('bad multi array')
        src += used
        interval_lenlog2 = [t >> 4 for t in idx]
        interval_indexes = [t & 0xF for t in idx]
        num_lens = num_indexes
    else:
        lenlog2_chunksize = num_indexes - array_count
        used, idx = _decode_bytes(buf, src, src_end, num_indexes)
        if len(idx) != num_indexes:
            raise KrakenError('bad multi array')
        src += used
        interval_indexes = list(idx)
        used, ll = _decode_bytes(buf, src, src_end, lenlog2_chunksize)
        if len(ll) != lenlog2_chunksize:
            raise KrakenError('bad multi array')
        src += used
        interval_lenlog2 = list(ll)
        for t in interval_lenlog2:
            if t > 16:
                raise KrakenError('bad multi array')

    varbits_complen = Q & 0x3FFF
    if src_end - src < varbits_complen:
        raise KrakenError('bad multi array')
    f = src
    bits_f = 0
    bitpos_f = 24
    src_end_actual = src + varbits_complen
    b = src_end_actual
    bits_b = 0
    bitpos_b = 24

    decoded = [0] * num_lens
    i = 0
    while i + 2 <= num_lens:
        bits_f = (bits_f | (_be32(buf, f) >> (24 - bitpos_f))) & MASK32
        f += (bitpos_f + 7) >> 3
        bits_b = (bits_b | (_le32(buf, b - 4) >> (24 - bitpos_b))) & MASK32
        b -= (bitpos_b + 7) >> 3
        numbits_f = interval_lenlog2[i]
        numbits_b = interval_lenlog2[i + 1]
        bits_f = _rotl32(bits_f | 1, numbits_f)
        bitpos_f += numbits_f - 8 * ((bitpos_f + 7) >> 3)
        bits_b = _rotl32(bits_b | 1, numbits_b)
        bitpos_b += numbits_b - 8 * ((bitpos_b + 7) >> 3)
        decoded[i] = bits_f & _BITMASKS[numbits_f]
        bits_f &= ~_BITMASKS[numbits_f] & MASK32
        decoded[i + 1] = bits_b & _BITMASKS[numbits_b]
        bits_b &= ~_BITMASKS[numbits_b] & MASK32
        i += 2
    if i < num_lens:
        bits_f = (bits_f | (_be32(buf, f) >> (24 - bitpos_f))) & MASK32
        numbits_f = interval_lenlog2[i]
        bits_f = _rotl32(bits_f | 1, numbits_f)
        decoded[i] = bits_f & _BITMASKS[numbits_f]

    if interval_indexes[num_indexes - 1]:
        raise KrakenError('bad multi array')

    indi = leni = 0
    increment_leni = 1 if (Q & 0x8000) else 0
    pos = [0] * num_arrays_in_file
    arrays = []
    written = 0
    for arri in range(array_count):
        cur = bytearray()
        if indi >= num_indexes:
            raise KrakenError('bad multi array')
        while True:
            source = interval_indexes[indi]
            indi += 1
            if source == 0:
                break
            if source > num_arrays_in_file or leni >= num_lens:
                raise KrakenError('bad multi array')
            cur_len = decoded[leni]
            leni += 1
            ea = entropy[source - 1]
            p = pos[source - 1]
            if cur_len > len(ea) - p or cur_len > dst_capacity - written:
                raise KrakenError('bad multi array')
            cur += ea[p:p + cur_len]
            pos[source - 1] = p + cur_len
            written += cur_len
        leni += increment_leni
        arrays.append(bytes(cur))
    if indi != num_indexes or leni != num_lens:
        raise KrakenError('bad multi array')
    for i in range(num_arrays_in_file):
        if pos[i] != len(entropy[i]):
            raise KrakenError('bad multi array')
    return src_end_actual - src_org, arrays, total_size


# ---------------------------------------------------------------------------
# LZ phase
# ---------------------------------------------------------------------------

def _unpack_offsets(buf, src, src_end, packed_offs, packed_offs_extra, multi_dist_scale,
                    packed_litlen):
    """Kraken_UnpackOffsets -> (offs_stream, len_stream)."""
    bits_a = _Bits(buf, src, src_end)
    bits_b = _Bits(buf, src_end, src, backward=True)

    if bits_b.bits < 0x2000:
        raise KrakenError('bad offset stream')
    n = 31 - _bsr(bits_b.bits)
    bits_b.bitpos += n
    bits_b.bits = (bits_b.bits << n) & MASK32
    bits_b.refill()
    n += 1
    u32_len_stream_size = (bits_b.bits >> (32 - n)) - 1
    bits_b.bitpos += n
    bits_b.bits = (bits_b.bits << n) & MASK32
    bits_b.refill()

    offs = []
    count = len(packed_offs)
    _hit('offsets:%s' % ('classic' if multi_dist_scale == 0 else 'scaled%d' % multi_dist_scale))
    if multi_dist_scale == 0:
        i = 0
        while i < count:
            offs.append(-bits_a.read_distance(packed_offs[i]))
            i += 1
            if i == count:
                break
            offs.append(-bits_b.read_distance(packed_offs[i]))
            i += 1
    else:
        i = 0
        while i < count:
            cmd = packed_offs[i]
            i += 1
            if (cmd >> 3) > 26:
                raise KrakenError('bad offset stream')
            o = ((8 + (cmd & 7)) << (cmd >> 3)) | bits_a.read_more_than_24(cmd >> 3)
            offs.append(8 - o)
            if i == count:
                break
            cmd = packed_offs[i]
            i += 1
            if (cmd >> 3) > 26:
                raise KrakenError('bad offset stream')
            o = ((8 + (cmd & 7)) << (cmd >> 3)) | bits_b.read_more_than_24(cmd >> 3)
            offs.append(8 - o)
        if multi_dist_scale != 1:
            offs = [multi_dist_scale * offs[k] - packed_offs_extra[k] for k in range(len(offs))]

    if u32_len_stream_size > 512:
        raise KrakenError('bad length stream')
    u32_len = []
    i = 0
    while i + 1 < u32_len_stream_size:
        u32_len.append(bits_a.read_length())
        u32_len.append(bits_b.read_length())
        i += 2
    if i < u32_len_stream_size:
        u32_len.append(bits_a.read_length())

    bits_a.p -= (24 - bits_a.bitpos) >> 3
    bits_b.p += (24 - bits_b.bitpos) >> 3
    if bits_a.p != bits_b.p:
        raise KrakenError('bad length stream')

    lens = []
    k = 0
    for v in packed_litlen:
        if v == 255:
            if k >= len(u32_len):
                raise KrakenError('bad length stream')
            v = u32_len[k] + 255
            k += 1
        lens.append(v + 3)
    if k != len(u32_len):
        raise KrakenError('bad length stream')
    return offs, lens


def _read_lz_table(mode, buf, src, src_end, out, dst, dst_size, offset):
    """Kraken_ReadLzTable -> (cmd_stream, offs_stream, lit_stream, len_stream)."""
    if mode > 1:
        raise KrakenError('unsupported LZ mode %d' % mode)
    if src_end - src < 13:
        raise KrakenError('truncated LZ block')
    if offset == 0:
        out[dst:dst + 8] = buf[src:src + 8]
        dst += 8
        src += 8
    if buf[src] & 0x80:
        raise KrakenError('unsupported LZ block (excess bytes)')

    used, lit_stream = _decode_bytes(buf, src, src_end, dst_size)
    src += used
    used, cmd_stream = _decode_bytes(buf, src, src_end, dst_size)
    src += used

    if src_end - src < 3:
        raise KrakenError('truncated LZ block')
    offs_scaling = 0
    packed_offs_extra = None
    if buf[src] & 0x80:
        offs_scaling = buf[src] - 127
        src += 1
        used, packed_offs = _decode_bytes(buf, src, src_end, len(cmd_stream))
        src += used
        if offs_scaling != 1:
            used, packed_offs_extra = _decode_bytes(buf, src, src_end, len(packed_offs))
            if len(packed_offs_extra) != len(packed_offs):
                raise KrakenError('bad offset stream')
            src += used
    else:
        used, packed_offs = _decode_bytes(buf, src, src_end, len(cmd_stream))
        src += used

    used, packed_len = _decode_bytes(buf, src, src_end, dst_size >> 2)
    src += used

    offs, lens = _unpack_offsets(buf, src, src_end, packed_offs, packed_offs_extra,
                                 offs_scaling, packed_len)
    return cmd_stream, offs, lit_stream, lens


def _copy_match(out, dst, offset, length):
    """out[dst:dst+length] = out[dst+offset:...], overlap-aware (offset < 0)."""
    src = dst + offset
    dist = -offset
    if length <= dist:
        out[dst:dst + length] = out[src:src + length]
        return
    # overlapping: the pattern of |dist| bytes repeats
    pattern = bytes(out[src:src + dist])
    reps, rem = divmod(length, dist)
    out[dst:dst + length] = pattern * reps + pattern[:rem]


def _process_lz_runs(mode, out, dst, dst_size, offset, cmd_stream, offs_stream,
                     lit_stream, len_stream):
    """Kraken_ProcessLzRuns: mode 0 adds literals to the last match, mode 1 is raw."""
    dst_start = dst - offset
    dst_end = dst + dst_size
    if offset == 0:
        dst += 8
    sub = (mode == 0)

    recent = [0, 0, 0, -8, -8, -8, 0]
    last_offset = -8
    lit = 0
    li = 0          # len_stream index
    oi = 0          # offs_stream index
    n_offs = len(offs_stream)
    n_lens = len(len_stream)
    n_lit = len(lit_stream)

    for f in cmd_stream:
        litlen = f & 3
        offs_index = f >> 6
        matchlen = (f >> 2) & 0xF
        if litlen == 3:
            if li >= n_lens:
                raise KrakenError('bad LZ stream')
            litlen = len_stream[li]
            li += 1
        recent[6] = offs_stream[oi] if oi < n_offs else 0

        if litlen:
            if litlen > n_lit - lit or litlen > dst_end - dst:
                raise KrakenError('bad LZ stream')
            if sub:
                for k in range(litlen):
                    out[dst + k] = (lit_stream[lit + k] + out[dst + k + last_offset]) & 0xFF
            else:
                out[dst:dst + litlen] = lit_stream[lit:lit + litlen]
            dst += litlen
            lit += litlen

        off = recent[offs_index + 3]
        recent[offs_index + 3] = recent[offs_index + 2]
        recent[offs_index + 2] = recent[offs_index + 1]
        recent[offs_index + 1] = recent[offs_index]
        recent[3] = off
        last_offset = off
        if offs_index == 3:
            if oi >= n_offs:
                raise KrakenError('bad LZ stream')
            oi += 1

        if off >= 0 or dst + off < dst_start:
            raise KrakenError('bad LZ stream (offset out of bounds)')

        if matchlen != 15:
            length = matchlen + 2
        else:
            if li >= n_lens:
                raise KrakenError('bad LZ stream')
            length = 14 + len_stream[li]
            li += 1
        if length > dst_end - dst:
            raise KrakenError('bad LZ stream (copy length out of bounds)')
        _copy_match(out, dst, off, length)
        dst += length

    if oi != n_offs or li != n_lens:
        raise KrakenError('bad LZ stream')
    final_len = dst_end - dst
    if final_len != n_lit - lit:
        raise KrakenError('bad LZ stream')
    if final_len:
        if sub:
            for k in range(final_len):
                out[dst + k] = (lit_stream[lit + k] + out[dst + k + last_offset]) & 0xFF
        else:
            out[dst:dst_end] = lit_stream[lit:lit + final_len]


def _decode_quantum(out, dst, dst_end, dst_start, buf, src, src_end):
    """Kraken_DecodeQuantum: up to 256 KB, in 128 KB chunks with shared history."""
    src_in = src
    while dst_end - dst != 0:
        dst_count = min(dst_end - dst, 0x20000)
        if src_end - src < 4:
            raise KrakenError('truncated quantum')
        chunkhdr = buf[src + 2] | (buf[src + 1] << 8) | (buf[src] << 16)
        if not (chunkhdr & 0x800000):
            # entropy coded, no match copying
            _hit('quantum:entropy')
            src_used, data = _decode_bytes(buf, src, src_end, dst_count)
            if len(data) != dst_count:
                raise KrakenError('bad quantum')
            out[dst:dst + dst_count] = data
        else:
            src += 3
            src_used = chunkhdr & 0x7FFFF
            mode = (chunkhdr >> 19) & 0xF
            if src_end - src < src_used:
                raise KrakenError('truncated quantum')
            if src_used < dst_count:
                _hit('lz:%d' % mode)
                cmd, offs, lit, lens = _read_lz_table(mode, buf, src, src + src_used,
                                                      out, dst, dst_count, dst - dst_start)
                _process_lz_runs(mode, out, dst, dst_count, dst - dst_start,
                                 cmd, offs, lit, lens)
            elif src_used > dst_count or mode != 0:
                raise KrakenError('bad quantum')
            else:
                _hit('quantum:raw')
                out[dst:dst + dst_count] = buf[src:src + dst_count]
        src += src_used
        dst += dst_count
    return src - src_in


# ---------------------------------------------------------------------------
# Stream level
# ---------------------------------------------------------------------------

_DECODER_NAMES = {5: 'LZNA', 6: 'Kraken', 10: 'Mermaid/Selkie', 11: 'Bitknit',
                  12: 'Leviathan'}


def _parse_header(buf, p):
    """-> (decoder_type, uncompressed, use_checksums), p + 2."""
    if p + 2 > len(buf):
        raise KrakenError('truncated header')
    b = buf[p]
    if (b & 0xF) != 0xC or ((b >> 4) & 3) != 0:
        raise KrakenError('not an Oodle stream')
    uncompressed = (b >> 6) & 1
    b = buf[p + 1]
    decoder_type = b & 0x7F
    use_checksums = b >> 7
    if decoder_type not in _DECODER_NAMES:
        raise KrakenError('not an Oodle stream')
    if decoder_type != 6:
        raise KrakenError('%s streams are not supported - only Kraken is'
                          % _DECODER_NAMES[decoder_type])
    return (decoder_type, uncompressed, use_checksums), p + 2


def _parse_quantum_header(buf, p, use_checksum):
    """-> (compressed_size, memset_byte), new p."""
    if p + 3 > len(buf):
        raise KrakenError('truncated quantum header')
    v = (buf[p] << 16) | (buf[p + 1] << 8) | buf[p + 2]
    size = v & 0x3FFFF
    if size != 0x3FFFF:
        if use_checksum:
            return (size + 1, None), p + 6
        return (size + 1, None), p + 3
    if (v >> 18) == 1:
        if p + 4 > len(buf):
            raise KrakenError('truncated quantum header')
        return (0, buf[p + 3]), p + 4
    raise KrakenError('bad quantum header')


def decompress(src, dst_len):
    """Decode a Kraken stream that expands to exactly dst_len bytes."""
    buf = bytes(src)
    src_len = len(buf)
    out = bytearray(dst_len)
    p = 0
    offset = 0
    hdr = None
    while offset < dst_len:
        if (offset & 0x3FFFF) == 0:
            hdr, p = _parse_header(buf, p)
        if hdr is None:
            raise KrakenError('missing header')
        dst_bytes_left = min(0x40000, dst_len - offset)
        if hdr[1]:                                  # uncompressed block
            _hit('stream:uncompressed')
            if src_len - p < dst_bytes_left:
                raise KrakenError('truncated stream')
            out[offset:offset + dst_bytes_left] = buf[p:p + dst_bytes_left]
            p += dst_bytes_left
            offset += dst_bytes_left
            continue
        (compressed_size, memset_byte), p = _parse_quantum_header(buf, p, hdr[2])
        if p > src_len or src_len - p < compressed_size:
            raise KrakenError('truncated stream')
        if compressed_size > dst_bytes_left:
            raise KrakenError('bad quantum header')
        if compressed_size == 0:
            _hit('stream:memset')
            out[offset:offset + dst_bytes_left] = bytes([memset_byte]) * dst_bytes_left
        elif compressed_size == dst_bytes_left:
            _hit('stream:stored')
            out[offset:offset + dst_bytes_left] = buf[p:p + dst_bytes_left]
            p += dst_bytes_left
        else:
            n = _decode_quantum(out, offset, offset + dst_bytes_left, 0,
                                buf, p, p + compressed_size)
            if n != compressed_size:
                raise KrakenError('quantum size mismatch')
            p += compressed_size
        offset += dst_bytes_left
    if p != src_len:
        raise KrakenError('trailing data after the stream')
    return bytes(out)
