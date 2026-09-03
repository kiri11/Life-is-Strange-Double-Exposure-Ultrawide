//! A decoder for Oodle Kraken streams.
//!
//! A port of the Kraken parts of ooz (<https://github.com/powzix/ooz>),
//! Copyright (C) 2016, Powzix, GPL-3.0-or-later, the same license as this
//! project, by way of the Python port this fix used to ship. Only the
//! Kraken codec (decoder type 6) is implemented: that is the one the game's
//! containers use, and it is all the fix needs. Mermaid, Selkie, Leviathan,
//! LZNA and Bitknit streams are rejected with a named error.
//!
//! The port keeps the Python's shape function for function, including the
//! rule that a byte read outside the buffer is a zero rather than a fault:
//! the tests decode the same streams and check the same code paths.
//!
//! ```ignore
//! decompress(compressed, decompressed_size) -> Result<Vec<u8>, KrakenError>
//! ```

use std::collections::BTreeMap;
use std::fmt;

#[derive(Debug, Clone, PartialEq)]
pub struct KrakenError(pub String);

impl fmt::Display for KrakenError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for KrakenError {}

type R<T> = Result<T, KrakenError>;

fn err<T>(what: &str) -> R<T> {
    Err(KrakenError(what.to_string()))
}

/// Which code paths a decode went through, for the test-suite's coverage
/// check: `block:<type>`, `lz:<mode>`, `quantum:<kind>`, `huff:<table>`,
/// `multi:<kind>`, `offsets:<kind>`, `stream:<kind>`.
pub type Stats = BTreeMap<String, u32>;

/// Decode a Kraken stream that expands to exactly `dst_len` bytes.
pub fn decompress(src: &[u8], dst_len: usize) -> R<Vec<u8>> {
    Dec { buf: src, stats: None }.decompress(dst_len)
}

/// `decompress`, and the code paths it went through.
pub fn decompress_with_stats(src: &[u8], dst_len: usize) -> R<(Vec<u8>, Stats)> {
    let mut d = Dec { buf: src, stats: Some(Stats::new()) };
    let out = d.decompress(dst_len)?;
    Ok((out, d.stats.unwrap_or_default()))
}

// ---------------------------------------------------------------- helpers

fn shl(x: u32, n: i64) -> u32 {
    if !(0..32).contains(&n) { 0 } else { x << n }
}

fn shr(x: u32, n: i64) -> u32 {
    if !(0..32).contains(&n) { 0 } else { x >> n }
}

fn rotl32(x: u32, n: i64) -> u32 {
    x.rotate_left((n & 31) as u32)
}

/// Index of the highest set bit; x must be non-zero.
fn bsr(x: u32) -> i64 {
    31 - x.leading_zeros() as i64
}

/// Index of the lowest set bit; x must be non-zero.
fn bsf(x: u32) -> i64 {
    x.trailing_zeros() as i64
}

const RICE_VALUE: [u32; 256] = [
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
];

const RICE_LEN: [u8; 256] = [
    0, 1, 1, 2, 1, 2, 2, 3, 1, 2, 2, 3, 2, 3, 3, 4, 1, 2, 2, 3, 2, 3, 3, 4, 2, 3, 3, 4, 3, 4, 4, 5,
    1, 2, 2, 3, 2, 3, 3, 4, 2, 3, 3, 4, 3, 4, 4, 5, 2, 3, 3, 4, 3, 4, 4, 5, 3, 4, 4, 5, 4, 5, 5, 6,
    1, 2, 2, 3, 2, 3, 3, 4, 2, 3, 3, 4, 3, 4, 4, 5, 2, 3, 3, 4, 3, 4, 4, 5, 3, 4, 4, 5, 4, 5, 5, 6,
    2, 3, 3, 4, 3, 4, 4, 5, 3, 4, 4, 5, 4, 5, 5, 6, 3, 4, 4, 5, 4, 5, 5, 6, 4, 5, 5, 6, 5, 6, 6, 7,
    1, 2, 2, 3, 2, 3, 3, 4, 2, 3, 3, 4, 3, 4, 4, 5, 2, 3, 3, 4, 3, 4, 4, 5, 3, 4, 4, 5, 4, 5, 5, 6,
    2, 3, 3, 4, 3, 4, 4, 5, 3, 4, 4, 5, 4, 5, 5, 6, 3, 4, 4, 5, 4, 5, 5, 6, 4, 5, 5, 6, 5, 6, 6, 7,
    2, 3, 3, 4, 3, 4, 4, 5, 3, 4, 4, 5, 4, 5, 5, 6, 3, 4, 4, 5, 4, 5, 5, 6, 4, 5, 5, 6, 5, 6, 6, 7,
    3, 4, 4, 5, 4, 5, 5, 6, 4, 5, 5, 6, 5, 6, 6, 7, 4, 5, 5, 6, 5, 6, 6, 7, 5, 6, 6, 7, 6, 7, 7, 8,
];

/// `(2 << i) - 1`: a mask of `i + 1` bits, the way ooz tabulates it.
fn bitmask(i: i64) -> u32 {
    (2u64 << i).wrapping_sub(1) as u32
}

const CODE_PREFIX_ORG: [usize; 12] = [0x0, 0x0, 0x2, 0x6, 0xE, 0x1E, 0x3E, 0x7E, 0xFE, 0x1FE, 0x2FE, 0x3FE];

// ------------------------------------------------------------- bit readers

/// MSB-first, 32-bit accumulator; forward and backward variants. Bytes
/// outside `[p_end, p)` (backward) or `[p, p_end)` (forward) read as zero.
struct Bits<'a> {
    buf: &'a [u8],
    p: i64,
    p_end: i64,
    bits: u32,
    bitpos: i64,
    backward: bool,
}

fn byte_of(buf: &[u8], i: i64) -> u32 {
    if i >= 0 && (i as usize) < buf.len() { buf[i as usize] as u32 } else { 0 }
}

impl<'a> Bits<'a> {
    fn new(buf: &'a [u8], p: i64, p_end: i64, backward: bool) -> Self {
        let mut b = Bits { buf, p, p_end, bits: 0, bitpos: 24, backward };
        b.refill();
        b
    }

    fn refill(&mut self) {
        if self.backward {
            while self.bitpos > 0 {
                self.p -= 1;
                let b = if self.p >= self.p_end { byte_of(self.buf, self.p) } else { 0 };
                self.bits |= shl(b, self.bitpos);
                self.bitpos -= 8;
            }
        } else {
            while self.bitpos > 0 {
                let b = if self.p < self.p_end { byte_of(self.buf, self.p) } else { 0 };
                self.bits |= shl(b, self.bitpos);
                self.bitpos -= 8;
                self.p += 1;
            }
        }
    }

    fn read_bit(&mut self) -> u32 {
        self.refill();
        self.read_bit_no_refill()
    }

    fn read_bit_no_refill(&mut self) -> u32 {
        let r = self.bits >> 31;
        self.bits <<= 1;
        self.bitpos += 1;
        r
    }

    /// n bits, n >= 1.
    fn read_no_refill(&mut self, n: i64) -> u32 {
        let r = shr(self.bits, 32 - n);
        self.bits = shl(self.bits, n);
        self.bitpos += n;
        r
    }

    /// n bits, n may be zero.
    fn read_no_refill_zero(&mut self, n: i64) -> u32 {
        let r = shr(self.bits >> 1, 31 - n);
        self.bits = shl(self.bits, n);
        self.bitpos += n;
        r
    }

    fn read_more_than_24(&mut self, n: i64) -> u32 {
        let rv = if n <= 24 {
            self.read_no_refill_zero(n)
        } else {
            let hi = self.read_no_refill(24) << (n - 24);
            self.refill();
            hi + self.read_no_refill(n - 24)
        };
        self.refill();
        rv
    }

    fn read_distance(&mut self, v: u32) -> i64 {
        
        let rv = if v < 0xF0 {
            let n = (v >> 4) as i64 + 4;
            let w = rotl32(self.bits | 1, n);
            self.bitpos += n;
            let m = bitmask(n);
            self.bits = w & !m;
            (((w & m) as i64) << 4) + (v & 0xF) as i64 - 248
        } else {
            let n = v as i64 - 0xF0 + 4;
            let w = rotl32(self.bits | 1, n);
            self.bitpos += n;
            let m = bitmask(n);
            self.bits = w & !m;
            let mut r = 8322816 + (((w & m) as i64) << 12);
            self.refill();
            r += (self.bits >> 20) as i64;
            self.bitpos += 12;
            self.bits <<= 12;
            r
        };
        self.refill();
        rv
    }

    fn read_length(&mut self) -> R<i64> {
        if self.bits == 0 {
            return err("bad length code");
        }
        let mut n = 31 - bsr(self.bits);
        if n > 12 {
            return err("bad length code");
        }
        self.bitpos += n;
        self.bits = shl(self.bits, n);
        self.refill();
        n += 7;
        self.bitpos += n;
        let rv = shr(self.bits, 32 - n) as i64 - 64;
        self.bits = shl(self.bits, n);
        self.refill();
        Ok(rv)
    }

    fn read_fluff(&mut self, num_symbols: i64) -> i64 {
        if num_symbols == 256 {
            return 0;
        }
        let mut x = 257 - num_symbols;
        if x > num_symbols {
            x = num_symbols;
        }
        x *= 2;
        let y = bsr((x - 1) as u32) + 1;
        let v = shr(self.bits, 32 - y) as i64;
        let z = (1i64 << y) - x;
        if (v >> 1) >= z {
            self.bits = shl(self.bits, y);
            self.bitpos += y;
            return v - z;
        }
        self.bits = shl(self.bits, y - 1);
        self.bitpos += y - 1;
        v >> 1
    }

    /// Byte position of the next unread whole byte (forward reader).
    fn byte_pos(&self) -> i64 {
        self.p - ((24 - self.bitpos) >> 3)
    }

    /// Restart at byte p with bitpos bits of it already consumed.
    fn rewind_to(&mut self, p: i64, bitpos: i64) {
        self.bitpos = 24;
        self.p = p;
        self.bits = 0;
        self.refill();
        self.bits = shl(self.bits, bitpos);
        self.bitpos += bitpos;
    }
}

/// Byte-position reader used by the Golomb-Rice code length decoders.
struct Bits2 {
    p: i64,
    p_end: i64,
    bitpos: i64,
}

impl Bits2 {
    /// Derived from a [`Bits`] reader: the byte holding the next unread
    /// bit, and how many bits of it are already consumed.
    fn from(bits: &Bits) -> Self {
        Bits2 {
            bitpos: (bits.bitpos - 24) & 7,
            p_end: bits.p_end,
            p: bits.p - ((24 - bits.bitpos + 7) >> 3),
        }
    }
}

// ---------------------------------------------------------- Huffman tables

/// DecodeGolombRiceLengths: unary-coded values, MSB-first.
fn golomb_rice_lengths(buf: &[u8], dst: &mut [u8], size: usize, br: &mut Bits2) -> R<()> {
    let (mut p, p_end, bitpos) = (br.p, br.p_end, br.bitpos);
    if p >= p_end {
        return err("truncated code lengths");
    }
    let mut count: i64 = -bitpos;
    let mut v = byte_of(buf, p) & (255 >> bitpos);
    p += 1;
    let mut di = 0usize;
    loop {
        if v == 0 {
            count += 8;
        } else {
            let x = RICE_VALUE[v as usize];
            let lo = (count as u32).wrapping_add(x & 0x0f0f0f0f);
            let hi = (x >> 4) & 0x0f0f0f0f;
            if di + 8 > dst.len() {
                return err("bad code lengths");
            }
            dst[di..di + 4].copy_from_slice(&lo.to_le_bytes());
            dst[di + 4..di + 8].copy_from_slice(&hi.to_le_bytes());
            di += RICE_LEN[v as usize] as usize;
            if di >= size {
                break;
            }
            count = (x >> 28) as i64;
        }
        if p >= p_end {
            return err("truncated code lengths");
        }
        v = byte_of(buf, p);
        p += 1;
    }
    if di > size {
        // went too far, step back
        let mut n = di - size;
        while n > 0 {
            v &= v.wrapping_sub(1);
            n -= 1;
        }
    }
    let mut bitpos = 0;
    if v & 1 == 0 {
        // byte not finished
        if v == 0 {
            return err("bad code lengths");
        }
        p -= 1;
        bitpos = 8 - bsf(v);
    }
    br.p = p;
    br.bitpos = bitpos;
    Ok(())
}

/// DecodeGolombRiceBits: `bitcount` extra bits per value, MSB-first.
fn golomb_rice_bits(buf: &[u8], dst: &mut [u8], size: usize, bitcount: i64, br: &mut Bits2) -> R<()> {
    if bitcount == 0 {
        return Ok(());
    }
    let (mut p, mut bitpos) = (br.p, br.bitpos);
    let bits_required = bitpos + bitcount * size as i64;
    let bytes_required = (bits_required + 7) >> 3;
    if bytes_required > br.p_end - p {
        return err("truncated code lengths");
    }
    br.p = p + (bits_required >> 3);
    br.bitpos = bits_required & 7;
    let mut acc: u64 = 0;
    let mut have: i64 = 0;
    for slot in dst.iter_mut().take(size) {
        while have < bitcount {
            if bitpos != 0 {
                acc = (acc << (8 - bitpos)) | (byte_of(buf, p) & (255 >> bitpos)) as u64;
                have += 8 - bitpos;
                bitpos = 0;
            } else {
                acc = (acc << 8) | byte_of(buf, p) as u64;
                have += 8;
            }
            p += 1;
        }
        have -= bitcount;
        let val = (acc >> have) & ((1u64 << bitcount) - 1);
        acc &= (1u64 << have) - 1;
        *slot = ((*slot as u64) * (1u64 << bitcount) + val) as u8;
    }
    Ok(())
}

/// Huff_ConvertToRanges -> (symbol, count) pairs.
fn convert_to_ranges(num_symbols: i64, p: i64, symlen: &[u8], mut sl: usize, bits: &mut Bits) -> R<Vec<(i64, i64)>> {
    let num_ranges = p >> 1;
    let mut sym_idx: i64 = 0;
    if p & 1 != 0 {
        bits.refill();
        let v = symlen[sl] as i64;
        sl += 1;
        if v >= 8 {
            return err("bad symbol ranges");
        }
        sym_idx = bits.read_no_refill(v + 1) as i64 + (1 << (v + 1)) - 1;
    }
    let mut syms_used: i64 = 0;
    let mut ranges = Vec::new();
    for _ in 0..num_ranges {
        bits.refill();
        let v = symlen[sl] as i64;
        if v >= 9 {
            return err("bad symbol ranges");
        }
        let num = bits.read_no_refill_zero(v) as i64 + (1 << v);
        let v = symlen[sl + 1] as i64;
        if v >= 8 {
            return err("bad symbol ranges");
        }
        let space = bits.read_no_refill(v + 1) as i64 + (1 << (v + 1)) - 1;
        ranges.push((sym_idx, num));
        syms_used += num;
        sym_idx += num + space;
        sl += 2;
    }
    if sym_idx >= 256 || syms_used >= num_symbols || sym_idx + num_symbols - syms_used > 256 {
        return err("bad symbol ranges");
    }
    ranges.push((sym_idx, num_symbols - syms_used));
    Ok(ranges)
}

fn read_code_lengths_old(bits: &mut Bits, syms: &mut [u8], code_prefix: &mut [usize; 12]) -> R<i64> {
    if bits.read_bit_no_refill() != 0 {
        let mut sym: i64 = 0;
        let mut num_symbols: i64 = 0;
        let mut avg_bits_x4: i64 = 32;
        let forced_bits = bits.read_no_refill(2) as i64;
        let thres = 1u32 << (31 - (20 >> forced_bits));
        let mut skip_zeros = bits.read_bit() != 0;
        loop {
            if !skip_zeros {
                if bits.bits & 0xff000000 == 0 {
                    return err("bad code lengths");
                }
                let lz = 31 - bsr(bits.bits);
                sym += bits.read_no_refill(2 * (lz + 1)) as i64 - 2 + 1;
                if sym >= 256 {
                    break;
                }
            }
            skip_zeros = false;
            bits.refill();
            if bits.bits & 0xff000000 == 0 {
                return err("bad code lengths");
            }
            let lz = 31 - bsr(bits.bits);
            let mut n = bits.read_no_refill(2 * (lz + 1)) as i64 - 2 + 1;
            if sym + n > 256 {
                return err("bad code lengths");
            }
            bits.refill();
            num_symbols += n;
            while n > 0 {
                if bits.bits < thres {
                    return err("bad code lengths");
                }
                let lz = 31 - bsr(bits.bits);
                let v = bits.read_no_refill(lz + forced_bits + 1) as i64 + ((lz - 1) << forced_bits);
                let codelen = (-(v & 1) ^ (v >> 1)) + ((avg_bits_x4 + 2) >> 2);
                if !(1..=11).contains(&codelen) {
                    return err("bad code lengths");
                }
                avg_bits_x4 = codelen + ((3 * avg_bits_x4 + 2) >> 2);
                bits.refill();
                syms[code_prefix[codelen as usize]] = sym as u8;
                code_prefix[codelen as usize] += 1;
                sym += 1;
                n -= 1;
            }
            if sym == 256 {
                break;
            }
        }
        if sym != 256 || num_symbols < 2 {
            return err("bad code lengths");
        }
        return Ok(num_symbols);
    }
    // sparse symbol encoding
    let num_symbols = bits.read_no_refill(8) as i64;
    if num_symbols == 0 {
        return err("bad code lengths");
    }
    if num_symbols == 1 {
        syms[0] = bits.read_no_refill(8) as u8;
    } else {
        let codelen_bits = bits.read_no_refill(3) as i64;
        if codelen_bits > 4 {
            return err("bad code lengths");
        }
        for _ in 0..num_symbols {
            bits.refill();
            let sym = bits.read_no_refill(8) as u8;
            let codelen = bits.read_no_refill_zero(codelen_bits) as usize + 1;
            if codelen > 11 {
                return err("bad code lengths");
            }
            syms[code_prefix[codelen]] = sym;
            code_prefix[codelen] += 1;
        }
    }
    Ok(num_symbols)
}

fn read_code_lengths_new(bits: &mut Bits, syms: &mut [u8], code_prefix: &mut [usize; 12]) -> R<i64> {
    let forced_bits = bits.read_no_refill(2) as i64;
    let num_symbols = bits.read_no_refill(8) as i64 + 1;
    let fluff = bits.read_fluff(num_symbols);

    let mut code_len = vec![0u8; 512 + 16];
    let mut br2 = Bits2::from(bits);
    let total = (num_symbols + fluff) as usize;
    golomb_rice_lengths(bits.buf, &mut code_len, total, &mut br2)?;
    for c in code_len.iter_mut().skip(total).take(16) {
        *c = 0;
    }
    golomb_rice_bits(bits.buf, &mut code_len, num_symbols as usize, forced_bits, &mut br2)?;

    bits.rewind_to(br2.p, br2.bitpos);

    let mut running_sum: u32 = 0x1e;
    for c in code_len.iter_mut().take(num_symbols as usize) {
        let v = *c as i64;
        let v = -(v & 1) ^ (v >> 1);
        let cl = v + (running_sum >> 2) as i64 + 1;
        if !(1..=11).contains(&cl) {
            return err("bad code lengths");
        }
        *c = cl as u8;
        running_sum = running_sum.wrapping_add(v as u32);
    }

    let ranges = convert_to_ranges(num_symbols, fluff, &code_len, num_symbols as usize, bits)?;
    let mut cp = 0usize;
    for (mut sym, mut n) in ranges {
        while n > 0 {
            let cl = code_len[cp] as usize;
            cp += 1;
            syms[code_prefix[cl]] = sym as u8;
            code_prefix[cl] += 1;
            sym += 1;
            n -= 1;
        }
    }
    Ok(num_symbols)
}

/// Huff_MakeLut + ReverseBitsArray2048 -> (bits2len, bits2sym), LSB-first.
fn make_lut(code_prefix: &[usize; 12], syms: &[u8]) -> R<(Vec<u8>, Vec<u8>)> {
    let mut lens = vec![0u8; 2048];
    let mut symbols = vec![0u8; 2048];
    let mut slot = 0usize;
    for i in 1..11 {
        let start = CODE_PREFIX_ORG[i];
        let count = code_prefix[i] - start;
        if count > 0 {
            let step = 1usize << (11 - i);
            let num_to_set = count << (11 - i);
            if slot + num_to_set > 2048 {
                return err("bad Huffman table");
            }
            for j in 0..count {
                let s = syms[start + j];
                for k in slot..slot + step {
                    lens[k] = i as u8;
                    symbols[k] = s;
                }
                slot += step;
            }
        }
    }
    let count = code_prefix[11] - CODE_PREFIX_ORG[11];
    if count > 0 {
        if slot + count > 2048 {
            return err("bad Huffman table");
        }
        let start = CODE_PREFIX_ORG[11];
        for j in 0..count {
            lens[slot + j] = 11;
            symbols[slot + j] = syms[start + j];
        }
        slot += count;
    }
    if slot != 2048 {
        return err("bad Huffman table");
    }
    // the table is built in canonical (MSB-first) order and looked up with
    // the low 11 bits of an LSB-first accumulator
    let mut lut_len = vec![0u8; 2048];
    let mut lut_sym = vec![0u8; 2048];
    for i in 0..2048usize {
        let rev = ((i as u32).reverse_bits() >> 21) as usize;
        lut_len[i] = lens[rev];
        lut_sym[i] = symbols[rev];
    }
    Ok((lut_len, lut_sym))
}

/// Three interleaved Huffman streams: forward, middle forward, end backward.
#[allow(clippy::too_many_arguments)]
fn decode_bytes_core(
    buf: &[u8],
    mut src: i64,
    mut src_mid: i64,
    mut src_end: i64,
    lut_len: &[u8],
    lut_sym: &[u8],
    out: &mut [u8],
    mut dst: usize,
    dst_end: usize,
) -> R<()> {
    let src_mid_org = src_mid;
    let (mut src_bits, mut src_mid_bits, mut src_end_bits) = (0u32, 0u32, 0u32);
    let (mut src_bitpos, mut src_mid_bitpos, mut src_end_bitpos) = (0i64, 0i64, 0i64);
    if src > src_mid {
        return err("bad Huffman stream");
    }
    while dst < dst_end {
        let d = src_mid - src;
        if d <= 1 {
            if d == 1 {
                src_bits |= shl(byte_of(buf, src), src_bitpos);
            }
        } else {
            src_bits |= shl(byte_of(buf, src) | (byte_of(buf, src + 1) << 8), src_bitpos);
        }
        let k = (src_bits & 0x7FF) as usize;
        let n = lut_len[k] as i64;
        src_bitpos -= n;
        src_bits = shr(src_bits, n);
        out[dst] = lut_sym[k];
        dst += 1;
        src += (7 - src_bitpos) >> 3;
        src_bitpos &= 7;

        if dst < dst_end {
            let d = src_end - src_mid;
            if d <= 1 {
                if d == 1 {
                    src_end_bits |= shl(byte_of(buf, src_mid), src_end_bitpos);
                    src_mid_bits |= shl(byte_of(buf, src_mid), src_mid_bitpos);
                }
            } else {
                src_end_bits |= shl(byte_of(buf, src_end - 1) | (byte_of(buf, src_end - 2) << 8), src_end_bitpos);
                src_mid_bits |= shl(byte_of(buf, src_mid) | (byte_of(buf, src_mid + 1) << 8), src_mid_bitpos);
            }
            let k = (src_end_bits & 0x7FF) as usize;
            let n = lut_len[k] as i64;
            out[dst] = lut_sym[k];
            dst += 1;
            src_end_bitpos -= n;
            src_end_bits = shr(src_end_bits, n);
            src_end -= (7 - src_end_bitpos) >> 3;
            src_end_bitpos &= 7;
            if dst < dst_end {
                let k = (src_mid_bits & 0x7FF) as usize;
                let n = lut_len[k] as i64;
                out[dst] = lut_sym[k];
                dst += 1;
                src_mid_bitpos -= n;
                src_mid_bits = shr(src_mid_bits, n);
                src_mid += (7 - src_mid_bitpos) >> 3;
                src_mid_bitpos &= 7;
            }
        }
        if src > src_mid || src_mid > src_end {
            return err("bad Huffman stream");
        }
    }
    if src != src_mid_org || src_end != src_mid {
        return err("bad Huffman stream");
    }
    Ok(())
}

// ------------------------------------------------------------ the decoder

/// One TANS state: (x, bits_x, symbol, w).
type TansEntry = (u32, i64, u8, u32);

struct Dec<'a> {
    buf: &'a [u8],
    /// Only the test-suite asks for these; a plain decode builds no keys.
    stats: Option<Stats>,
}

impl<'a> Dec<'a> {
    fn hit(&mut self, key: impl FnOnce() -> String) {
        if let Some(stats) = &mut self.stats {
            *stats.entry(key()).or_insert(0) += 1;
        }
    }

    fn b(&self, i: i64) -> u32 {
        byte_of(self.buf, i)
    }

    fn le16(&self, i: i64) -> u32 {
        if i < 0 {
            return 0; // the Python reads nothing before the buffer here
        }
        self.b(i) | (self.b(i + 1) << 8)
    }

    /// Little-endian dword at i; bytes outside the buffer read as zero.
    fn le32(&self, i: i64) -> u32 {
        self.b(i) | (self.b(i + 1) << 8) | (self.b(i + 2) << 16) | (self.b(i + 3) << 24)
    }

    /// Big-endian dword at i; bytes outside the buffer read as zero.
    fn be32(&self, i: i64) -> u32 {
        (self.b(i) << 24) | (self.b(i + 1) << 16) | (self.b(i + 2) << 8) | self.b(i + 3)
    }

    fn slice(&self, start: i64, len: i64) -> R<&'a [u8]> {
        if start < 0 || len < 0 || (start + len) as usize > self.buf.len() {
            return err("truncated stream");
        }
        Ok(&self.buf[start as usize..(start + len) as usize])
    }

    fn decode_bytes_type12(&mut self, mut src: i64, src_size: i64, out: &mut [u8], output_size: usize, kind: u32) -> R<i64> {
        let src_end = src + src_size;
        let mut bits = Bits::new(self.buf, src, src_end, false);
        let mut code_prefix = CODE_PREFIX_ORG;
        let mut syms = vec![0u8; 1280];
        let num_syms = if bits.read_bit_no_refill() == 0 {
            self.hit(|| "huff:old".into());
            read_code_lengths_old(&mut bits, &mut syms, &mut code_prefix)?
        } else if bits.read_bit_no_refill() == 0 {
            self.hit(|| "huff:new".into());
            read_code_lengths_new(&mut bits, &mut syms, &mut code_prefix)?
        } else {
            return err("bad Huffman header");
        };
        if num_syms < 1 {
            return err("bad Huffman header");
        }
        src = bits.byte_pos();

        if num_syms == 1 {
            for o in out.iter_mut().take(output_size) {
                *o = syms[0];
            }
            return Ok(src_size);
        }

        let (lut_len, lut_sym) = make_lut(&code_prefix, &syms)?;

        if kind == 1 {
            if src + 3 > src_end {
                return err("truncated Huffman stream");
            }
            let split_mid = self.le16(src) as i64;
            src += 2;
            decode_bytes_core(self.buf, src, src + split_mid, src_end, &lut_len, &lut_sym, out, 0, output_size)?;
        } else {
            if src + 6 > src_end {
                return err("truncated Huffman stream");
            }
            let half = output_size.div_ceil(2);
            let split_mid = (self.le32(src) & 0xFFFFFF) as i64;
            src += 3;
            if split_mid > src_end - src {
                return err("bad Huffman stream");
            }
            let src_mid = src + split_mid;
            let split_left = self.le16(src) as i64;
            src += 2;
            if src_mid - src < split_left + 2 || src_end - src_mid < 3 {
                return err("bad Huffman stream");
            }
            let split_right = self.le16(src_mid) as i64;
            if src_end - (src_mid + 2) < split_right + 2 {
                return err("bad Huffman stream");
            }
            decode_bytes_core(self.buf, src, src + split_left, src_mid, &lut_len, &lut_sym, out, 0, half)?;
            decode_bytes_core(self.buf, src_mid + 2, src_mid + 2 + split_right, src_end, &lut_len, &lut_sym, out, half, output_size)?;
        }
        Ok(src_size)
    }

    // ---------------------------------------------------------------- RLE

    fn decode_rle(&mut self, src: i64, src_size: i64, out: &mut [u8], dst_size: usize) -> R<i64> {
        if src_size <= 1 {
            if src_size != 1 {
                return err("bad RLE stream");
            }
            let v = self.b(src) as u8;
            for o in out.iter_mut().take(dst_size) {
                *o = v;
            }
            return Ok(1);
        }
        let cmd: Vec<u8> = if self.b(src) != 0 {
            // the command buffer is itself entropy coded, then raw bytes follow
            let (n, mut data) = self.decode_bytes(src, src + src_size, 0x6C000)?;
            data.extend_from_slice(self.slice(src + n, src_size - n)?);
            data
        } else {
            self.slice(src + 1, src_size - 1)?.to_vec()
        };
        let (mut cp, mut ce) = (0usize, cmd.len());
        let mut dst = 0usize;
        let mut rle_byte = 0u8;
        let le16 = |c: &[u8], i: usize| c[i] as i64 | ((c[i + 1] as i64) << 8);
        while cp < ce {
            let c = cmd[ce - 1];
            let (to_copy, to_rle): (i64, i64);
            if c == 0 || c >= 0x30 {
                ce -= 1;
                to_copy = (!c & 0xF) as i64;
                to_rle = (c >> 4) as i64;
            } else if c >= 0x10 {
                if ce < 2 {
                    return err("bad RLE stream");
                }
                let data = le16(&cmd, ce - 2) - 4096;
                ce -= 2;
                to_copy = data & 0x3F;
                to_rle = data >> 6;
            } else if c == 1 {
                rle_byte = cmd[cp];
                cp += 1;
                ce -= 1;
                continue;
            } else if c >= 9 {
                if ce < 2 {
                    return err("bad RLE stream");
                }
                to_rle = (le16(&cmd, ce - 2) - 0x8ff) * 128;
                ce -= 2;
                to_copy = 0;
            } else {
                if ce < 2 {
                    return err("bad RLE stream");
                }
                to_copy = (le16(&cmd, ce - 2) - 511) * 64;
                ce -= 2;
                to_rle = 0;
            }
            if to_copy < 0 || to_rle < 0 {
                return err("bad RLE stream");
            }
            let (to_copy, to_rle) = (to_copy as usize, to_rle as usize);
            if dst_size - dst < to_copy + to_rle || ce - cp < to_copy {
                return err("bad RLE stream");
            }
            out[dst..dst + to_copy].copy_from_slice(&cmd[cp..cp + to_copy]);
            cp += to_copy;
            dst += to_copy;
            if to_rle > 0 {
                out[dst..dst + to_rle].fill(rle_byte);
                dst += to_rle;
            }
        }
        if ce != cp || dst != dst_size {
            return err("bad RLE stream");
        }
        Ok(src_size)
    }

    // --------------------------------------------------------------- TANS

    /// -> (A, B): weight-1 symbols, and (symbol << 16 | weight) for the rest.
    fn tans_decode_table(&mut self, bits: &mut Bits, l_bits: i64) -> R<(Vec<u32>, Vec<u32>)> {
        bits.refill();
        let (mut a, mut b) = (Vec::new(), Vec::new());
        let l: i64 = 1 << l_bits;
        if bits.read_bit_no_refill() != 0 {
            let q = bits.read_no_refill(3) as i64;
            let num_symbols = bits.read_no_refill(8) as i64 + 1;
            if num_symbols < 2 {
                return err("bad TANS table");
            }
            let fluff = bits.read_fluff(num_symbols);
            let total = (fluff + num_symbols) as usize;
            let mut rice = vec![0u8; 512 + 16];
            let mut br2 = Bits2::from(bits);
            golomb_rice_lengths(bits.buf, &mut rice, total, &mut br2)?;
            for r in rice.iter_mut().skip(total).take(16) {
                *r = 0;
            }
            bits.rewind_to(br2.p, br2.bitpos);
            let ranges = convert_to_ranges(num_symbols, fluff, &rice, num_symbols as usize, bits)?;
            bits.refill();
            let mut cur = 0usize;
            let mut average: i64 = 6;
            let mut somesum: i64 = 0;
            for (mut symbol, mut num) in ranges {
                while num > 0 {
                    bits.refill();
                    let nextra = q + rice[cur] as i64;
                    cur += 1;
                    if nextra > 15 {
                        return err("bad TANS table");
                    }
                    let mut v = bits.read_no_refill_zero(nextra) as i64 + (1 << nextra) - (1 << q);
                    let average_div4 = average >> 2;
                    let mut limit = 2 * average_div4;
                    if v <= limit {
                        v = average_div4 + (-(v & 1) ^ (v >> 1));
                    }
                    if limit > v {
                        limit = v;
                    }
                    v += 1;
                    average += limit - average_div4;
                    if v == 1 {
                        a.push(symbol as u32);
                    } else {
                        b.push(((symbol as u32) << 16) + v as u32);
                    }
                    somesum += v;
                    symbol += 1;
                    num -= 1;
                }
            }
            if somesum != l {
                return err("bad TANS table");
            }
            return Ok((a, b));
        }

        let mut seen = [false; 256];
        let mut count = bits.read_no_refill(3) as i64 + 1;
        let bits_per_sym = bsr(l_bits as u32) + 1;
        let max_delta_bits = bits.read_no_refill(bits_per_sym) as i64;
        if max_delta_bits == 0 || max_delta_bits > l_bits {
            return err("bad TANS table");
        }
        let mut weight: i64 = 0;
        let mut total_weights: i64 = 0;
        while count > 0 {
            bits.refill();
            let sym = bits.read_no_refill(8) as usize;
            if seen[sym] {
                return err("bad TANS table");
            }
            let delta = bits.read_no_refill(max_delta_bits) as i64;
            weight += delta;
            if weight == 0 {
                return err("bad TANS table");
            }
            seen[sym] = true;
            if weight == 1 {
                a.push(sym as u32);
            } else {
                b.push(((sym as u32) << 16) + weight as u32);
            }
            total_weights += weight;
            count -= 1;
        }
        bits.refill();
        let sym = bits.read_no_refill(8) as usize;
        if seen[sym] {
            return err("bad TANS table");
        }
        if l - total_weights < weight || l - total_weights <= 1 {
            return err("bad TANS table");
        }
        b.push(((sym as u32) << 16) + (l - total_weights) as u32);
        a.sort_unstable();
        b.sort_unstable();
        Ok((a, b))
    }

    /// -> (x, bits_x, symbol, w) per state.
    #[allow(clippy::needless_range_loop)]
    fn tans_init_lut(&self, a: &[u32], b: &[u32], l_bits: i64) -> R<Vec<TansEntry>> {
        let l = 1usize << l_bits;
        let a_used = a.len();
        let slots_left = l - a_used;
        let sa = slots_left >> 2;
        let mut ptr = [0usize; 4];
        let mut sb = sa + usize::from((slots_left & 3) > 0);
        ptr[1] = sb;
        sb += sa + usize::from((slots_left & 3) > 1);
        ptr[2] = sb;
        sb += sa + usize::from((slots_left & 3) > 2);
        ptr[3] = sb;
        let mut lut: Vec<Option<TansEntry>> = vec![None; l];
        for (i, &sym) in a.iter().enumerate() {
            lut[slots_left + i] = Some(((l - 1) as u32, l_bits, sym as u8, 0));
        }
        let mut weights_sum: i64 = 0;
        let put = |lut: &mut Vec<Option<TansEntry>>, d: usize, e: TansEntry| -> R<()> {
            if d >= lut.len() {
                return err("bad TANS table");
            }
            lut[d] = Some(e);
            Ok(())
        };
        for &entry in b {
            let weight = (entry & 0xFFFF) as i64;
            let symbol = (entry >> 16) as u8;
            if weight > 4 {
                let sym_bits = bsr(weight as u32);
                let mut z = l_bits - sym_bits;
                let mut x = ((1i64 << z) - 1) as u32;
                let mut bits_x = z;
                let mut w = ((l - 1) as u32) & ((weight as u32) << z);
                let mut what_to_add = 1u32 << z;
                let mut big_x = (1i64 << (sym_bits + 1)) - weight;
                for j in 0..4 {
                    let mut d = ptr[j];
                    let y = (weight + ((weights_sum - j as i64 - 1) & 3)) >> 2;
                    if big_x >= y {
                        for _ in 0..y {
                            put(&mut lut, d, (x, bits_x, symbol, w))?;
                            d += 1;
                            w = w.wrapping_add(what_to_add);
                        }
                        big_x -= y;
                    } else {
                        for _ in 0..big_x {
                            put(&mut lut, d, (x, bits_x, symbol, w))?;
                            d += 1;
                            w = w.wrapping_add(what_to_add);
                        }
                        z -= 1;
                        what_to_add >>= 1;
                        bits_x = z;
                        w = 0;
                        x >>= 1;
                        for _ in 0..(y - big_x) {
                            put(&mut lut, d, (x, bits_x, symbol, w))?;
                            d += 1;
                            w = w.wrapping_add(what_to_add);
                        }
                        big_x = weight;
                    }
                    ptr[j] = d;
                }
            } else {
                if weight <= 0 {
                    return err("bad TANS table");
                }
                let mut bmask = ((1u32 << weight) - 1) << (weights_sum & 3);
                bmask |= bmask >> 4;
                let mut n = weight;
                let mut ww = weight;
                while n > 0 {
                    if bmask == 0 {
                        return err("bad TANS table");
                    }
                    let idx = bsf(bmask) as usize;
                    bmask &= bmask - 1;
                    if idx > 3 {
                        return err("bad TANS table");
                    }
                    let d = ptr[idx];
                    ptr[idx] += 1;
                    let weight_bits = bsr(ww as u32);
                    let shift = l_bits - weight_bits;
                    put(&mut lut, d, (((1i64 << shift) - 1) as u32, shift, symbol, ((l - 1) as u32) & ((ww as u32) << shift)))?;
                    ww += 1;
                    n -= 1;
                }
            }
            weights_sum += weight;
        }
        lut.into_iter().map(|e| e.ok_or_else(|| KrakenError("bad TANS table".into()))).collect()
    }

    fn decode_tans(&mut self, src_in: i64, src_size: i64, out: &mut [u8], dst_size: usize) -> R<i64> {
        if src_size < 8 || dst_size < 5 {
            return err("bad TANS stream");
        }
        let mut src = src_in;
        let mut src_end = src + src_size;
        let mut br = Bits::new(self.buf, src, src_end, false);
        if br.read_bit_no_refill() != 0 {
            return err("bad TANS stream");
        }
        let l_bits = br.read_no_refill(2) as i64 + 8;
        let (a, b) = self.tans_decode_table(&mut br, l_bits)?;
        src = br.byte_pos();
        if src >= src_end {
            return err("bad TANS stream");
        }
        let lut = self.tans_init_lut(&a, &b, l_bits)?;

        let dst_end = dst_size - 5;
        let l_mask = ((1i64 << l_bits) - 1) as u32;
        let mut bits_f = self.le32(src);
        src += 4;
        let mut bits_b = self.be32(src_end - 4);
        src_end -= 4;
        let (mut bitpos_f, mut bitpos_b): (i64, i64) = (32, 32);

        let s0 = bits_f & l_mask;
        let s1 = bits_b & l_mask;
        bits_f = shr(bits_f, l_bits);
        bitpos_f -= l_bits;
        bits_b = shr(bits_b, l_bits);
        bitpos_b -= l_bits;
        let s2 = bits_f & l_mask;
        let s3 = bits_b & l_mask;
        bits_f = shr(bits_f, l_bits);
        bitpos_f -= l_bits;
        bits_b = shr(bits_b, l_bits);
        bitpos_b -= l_bits;

        bits_f |= shl(self.le32(src), bitpos_f);
        src += (31 - bitpos_f) >> 3;
        bitpos_f |= 24;
        let s4 = bits_f & l_mask;
        bits_f = shr(bits_f, l_bits);
        bitpos_f -= l_bits;

        let mut ptr_f = src - (bitpos_f >> 3);
        bitpos_f &= 7;
        let mut ptr_b = src_end + (bitpos_b >> 3);
        bitpos_b &= 7;

        if ptr_f > ptr_b {
            return err("bad TANS stream");
        }

        let mut states = [s0 as usize, s1 as usize, s2 as usize, s3 as usize, s4 as usize];
        let mut dst = 0usize;
        const GROUPS: [&[usize]; 3] = [&[0, 1], &[2, 3], &[4]];
        'outer: while dst < dst_end {
            // forward: states 0,1 then 2,3 then 4; backward the same
            for group in GROUPS {
                bits_f |= shl(self.le32(ptr_f), bitpos_f);
                ptr_f += (31 - bitpos_f) >> 3;
                bitpos_f |= 24;
                for &si in group {
                    let (x, bits_x, symbol, w) = *lut.get(states[si]).ok_or_else(|| KrakenError("bad TANS stream".into()))?;
                    out[dst] = symbol;
                    dst += 1;
                    bitpos_f -= bits_x;
                    states[si] = ((bits_f & x).wrapping_add(w)) as usize;
                    bits_f = shr(bits_f, bits_x);
                    if dst >= dst_end {
                        break 'outer;
                    }
                }
            }
            for group in GROUPS {
                bits_b |= shl(self.be32(ptr_b - 4), bitpos_b);
                ptr_b -= (31 - bitpos_b) >> 3;
                bitpos_b |= 24;
                for &si in group {
                    let (x, bits_x, symbol, w) = *lut.get(states[si]).ok_or_else(|| KrakenError("bad TANS stream".into()))?;
                    out[dst] = symbol;
                    dst += 1;
                    bitpos_b -= bits_x;
                    states[si] = ((bits_b & x).wrapping_add(w)) as usize;
                    bits_b = shr(bits_b, bits_x);
                    if dst >= dst_end {
                        break 'outer;
                    }
                }
            }
        }

        if ptr_b - ptr_f + (bitpos_f >> 3) + (bitpos_b >> 3) != 0 {
            return err("bad TANS stream");
        }
        if (states[0] | states[1] | states[2] | states[3] | states[4]) & !0xFF != 0 {
            return err("bad TANS stream");
        }
        for i in 0..5 {
            out[dst_end + i] = states[i] as u8;
        }
        Ok(src_size)
    }

    // ----------------------------------------------- entropy-coded arrays

    /// Kraken_GetBlockSize -> decoded size of the array at src.
    fn block_size(&self, mut src: i64, src_end: i64, dest_capacity: i64) -> R<i64> {
        if src_end - src < 2 {
            return err("truncated block");
        }
        let chunk_type = (self.b(src) >> 4) & 7;
        if chunk_type == 0 {
            let src_size;
            if self.b(src) >= 0x80 {
                src_size = ((self.b(src) << 8) | self.b(src + 1)) as i64 & 0xFFF;
                src += 2;
            } else {
                if src_end - src < 3 {
                    return err("truncated block");
                }
                src_size = ((self.b(src) << 16) | (self.b(src + 1) << 8) | self.b(src + 2)) as i64;
                if src_size & !0x3FFFF != 0 {
                    return err("bad block");
                }
                src += 3;
            }
            if src_size > dest_capacity || src_end - src < src_size {
                return err("bad block");
            }
            return Ok(src_size);
        }
        if chunk_type >= 6 {
            return err("bad block");
        }
        let (src_size, dst_size);
        if self.b(src) >= 0x80 {
            if src_end - src < 3 {
                return err("truncated block");
            }
            let b = ((self.b(src) << 16) | (self.b(src + 1) << 8) | self.b(src + 2)) as i64;
            src_size = b & 0x3FF;
            dst_size = src_size + ((b >> 10) & 0x3FF) + 1;
            src += 3;
        } else {
            if src_end - src < 5 {
                return err("truncated block");
            }
            let b = self.be32(src + 1) as i64;
            src_size = b & 0x3FFFF;
            dst_size = (((b >> 18) | ((self.b(src) as i64) << 14)) & 0x3FFFF) + 1;
            if src_size >= dst_size {
                return err("bad block");
            }
            src += 5;
        }
        if src_end - src < src_size || dst_size > dest_capacity {
            return err("bad block");
        }
        Ok(dst_size)
    }

    /// Kraken_DecodeBytes -> (bytes consumed, decoded bytes).
    fn decode_bytes(&mut self, src_org: i64, src_end: i64, output_size: i64) -> R<(i64, Vec<u8>)> {
        let mut src = src_org;
        if src_end - src < 2 {
            return err("truncated block");
        }
        let chunk_type = (self.b(src) >> 4) & 7;
        if chunk_type == 0 {
            let src_size;
            if self.b(src) >= 0x80 {
                src_size = ((self.b(src) << 8) | self.b(src + 1)) as i64 & 0xFFF;
                src += 2;
            } else {
                if src_end - src < 3 {
                    return err("truncated block");
                }
                src_size = ((self.b(src) << 16) | (self.b(src + 1) << 8) | self.b(src + 2)) as i64;
                if src_size & !0x3FFFF != 0 {
                    return err("bad block");
                }
                src += 3;
            }
            if src_size > output_size || src_end - src < src_size {
                return err("bad block");
            }
            self.hit(|| "block:0".into());
            return Ok((src + src_size - src_org, self.slice(src, src_size)?.to_vec()));
        }

        let (src_size, dst_size);
        if self.b(src) >= 0x80 {
            if src_end - src < 3 {
                return err("truncated block");
            }
            let b = ((self.b(src) << 16) | (self.b(src + 1) << 8) | self.b(src + 2)) as i64;
            src_size = b & 0x3FF;
            dst_size = src_size + ((b >> 10) & 0x3FF) + 1;
            src += 3;
        } else {
            if src_end - src < 5 {
                return err("truncated block");
            }
            let b = self.be32(src + 1) as i64;
            src_size = b & 0x3FFFF;
            dst_size = (((b >> 18) | ((self.b(src) as i64) << 14)) & 0x3FFFF) + 1;
            if src_size >= dst_size {
                return err("bad block");
            }
            src += 5;
        }
        if src_end - src < src_size || dst_size > output_size {
            return err("bad block");
        }

        let mut out = vec![0u8; dst_size as usize];
        self.hit(|| format!("block:{chunk_type}"));
        let used = match chunk_type {
            2 | 4 => self.decode_bytes_type12(src, src_size, &mut out, dst_size as usize, chunk_type >> 1)?,
            5 => self.decode_recursive(src, src_size, &mut out, dst_size as usize)?,
            3 => self.decode_rle(src, src_size, &mut out, dst_size as usize)?,
            1 => self.decode_tans(src, src_size, &mut out, dst_size as usize)?,
            _ => return Err(KrakenError(format!("unknown block type {chunk_type}"))),
        };
        if used != src_size {
            return err("block size mismatch");
        }
        Ok((src + src_size - src_org, out))
    }

    fn decode_recursive(&mut self, src_org: i64, src_size: i64, out: &mut [u8], output_size: usize) -> R<i64> {
        let mut src = src_org;
        let src_end = src + src_size;
        if src_size < 6 {
            return err("bad recursive block");
        }
        let mut n = self.b(src) & 0x7F;
        if n < 2 {
            return err("bad recursive block");
        }
        if self.b(src) & 0x80 == 0 {
            src += 1;
            let mut pos = 0usize;
            while n > 0 {
                let (used, data) = self.decode_bytes(src, src_end, (output_size - pos) as i64)?;
                out[pos..pos + data.len()].copy_from_slice(&data);
                pos += data.len();
                src += used;
                n -= 1;
            }
            if pos != output_size {
                return err("bad recursive block");
            }
            return Ok(src - src_org);
        }
        let (used, arrays, total) = self.decode_multi_array(src, src_end, 1, output_size as i64)?;
        if total != output_size as i64 {
            return err("bad recursive block");
        }
        out[..total as usize].copy_from_slice(&arrays[0]);
        Ok(used)
    }

    /// Kraken_DecodeMultiArray -> (bytes consumed, arrays, total size).
    fn decode_multi_array(&mut self, src_org: i64, src_end: i64, array_count: usize, dst_capacity: i64) -> R<(i64, Vec<Vec<u8>>, i64)> {
        let mut src = src_org;
        if src_end - src < 4 {
            return err("bad multi array");
        }
        let mut num_arrays_in_file = self.b(src);
        src += 1;
        if num_arrays_in_file & 0x80 == 0 {
            return err("bad multi array");
        }
        num_arrays_in_file &= 0x3F;
        let num_arrays_in_file = num_arrays_in_file as usize;

        let mut total_size: i64 = 0;
        self.hit(|| format!("multi:{}", if num_arrays_in_file == 0 { "plain" } else { "interleaved" }));
        if num_arrays_in_file == 0 {
            let mut arrays = Vec::new();
            for _ in 0..array_count {
                let (used, data) = self.decode_bytes(src, src_end, dst_capacity - total_size)?;
                src += used;
                total_size += data.len() as i64;
                arrays.push(data);
            }
            return Ok((src - src_org, arrays, total_size));
        }

        let mut entropy = Vec::new();
        for _ in 0..num_arrays_in_file {
            let (used, data) = self.decode_bytes(src, src_end, 0x6C000)?;
            total_size += data.len() as i64;
            src += used;
            entropy.push(data);
        }

        if src_end - src < 3 {
            return err("bad multi array");
        }
        let q = self.le16(src);
        src += 2;

        let num_indexes = self.block_size(src, src_end, total_size)? as usize;
        if num_indexes < array_count + 1 {
            return err("bad multi array");
        }
        let mut num_lens = num_indexes - array_count;

        let interval_lenlog2: Vec<i64>;
        let interval_indexes: Vec<usize>;
        if q & 0x8000 != 0 {
            let (used, idx) = self.decode_bytes(src, src_end, num_indexes as i64)?;
            if idx.len() != num_indexes {
                return err("bad multi array");
            }
            src += used;
            interval_lenlog2 = idx.iter().map(|&t| (t >> 4) as i64).collect();
            interval_indexes = idx.iter().map(|&t| (t & 0xF) as usize).collect();
            num_lens = num_indexes;
        } else {
            let lenlog2_chunksize = num_indexes - array_count;
            let (used, idx) = self.decode_bytes(src, src_end, num_indexes as i64)?;
            if idx.len() != num_indexes {
                return err("bad multi array");
            }
            src += used;
            interval_indexes = idx.iter().map(|&t| t as usize).collect();
            let (used, ll) = self.decode_bytes(src, src_end, lenlog2_chunksize as i64)?;
            if ll.len() != lenlog2_chunksize {
                return err("bad multi array");
            }
            src += used;
            interval_lenlog2 = ll.iter().map(|&t| t as i64).collect();
            if interval_lenlog2.iter().any(|&t| t > 16) {
                return err("bad multi array");
            }
        }

        let varbits_complen = (q & 0x3FFF) as i64;
        if src_end - src < varbits_complen {
            return err("bad multi array");
        }
        let mut f = src;
        let mut bits_f: u32 = 0;
        let mut bitpos_f: i64 = 24;
        let src_end_actual = src + varbits_complen;
        let mut b = src_end_actual;
        let mut bits_b: u32 = 0;
        let mut bitpos_b: i64 = 24;

        let mut decoded = vec![0i64; num_lens];
        let mut i = 0usize;
        while i + 2 <= num_lens {
            bits_f |= shr(self.be32(f), 24 - bitpos_f);
            f += (bitpos_f + 7) >> 3;
            bits_b |= shr(self.le32(b - 4), 24 - bitpos_b);
            b -= (bitpos_b + 7) >> 3;
            let numbits_f = interval_lenlog2[i];
            let numbits_b = interval_lenlog2[i + 1];
            bits_f = rotl32(bits_f | 1, numbits_f);
            bitpos_f += numbits_f - 8 * ((bitpos_f + 7) >> 3);
            bits_b = rotl32(bits_b | 1, numbits_b);
            bitpos_b += numbits_b - 8 * ((bitpos_b + 7) >> 3);
            decoded[i] = (bits_f & bitmask(numbits_f)) as i64;
            bits_f &= !bitmask(numbits_f);
            decoded[i + 1] = (bits_b & bitmask(numbits_b)) as i64;
            bits_b &= !bitmask(numbits_b);
            i += 2;
        }
        if i < num_lens {
            bits_f |= shr(self.be32(f), 24 - bitpos_f);
            let numbits_f = interval_lenlog2[i];
            bits_f = rotl32(bits_f | 1, numbits_f);
            decoded[i] = (bits_f & bitmask(numbits_f)) as i64;
        }

        if interval_indexes[num_indexes - 1] != 0 {
            return err("bad multi array");
        }

        let (mut indi, mut leni) = (0usize, 0usize);
        let increment_leni = usize::from(q & 0x8000 != 0);
        let mut pos = vec![0usize; num_arrays_in_file];
        let mut arrays = Vec::new();
        let mut written: i64 = 0;
        for _ in 0..array_count {
            let mut cur = Vec::new();
            if indi >= num_indexes {
                return err("bad multi array");
            }
            loop {
                let source = interval_indexes[indi];
                indi += 1;
                if source == 0 {
                    break;
                }
                if source > num_arrays_in_file || leni >= num_lens {
                    return err("bad multi array");
                }
                let cur_len = decoded[leni];
                leni += 1;
                let ea = &entropy[source - 1];
                let p = pos[source - 1];
                if cur_len < 0 || cur_len > (ea.len() - p) as i64 || cur_len > dst_capacity - written {
                    return err("bad multi array");
                }
                let cur_len = cur_len as usize;
                cur.extend_from_slice(&ea[p..p + cur_len]);
                pos[source - 1] = p + cur_len;
                written += cur_len as i64;
                if indi >= num_indexes {
                    return err("bad multi array");
                }
            }
            leni += increment_leni;
            arrays.push(cur);
        }
        if indi != num_indexes || leni != num_lens {
            return err("bad multi array");
        }
        for i in 0..num_arrays_in_file {
            if pos[i] != entropy[i].len() {
                return err("bad multi array");
            }
        }
        Ok((src_end_actual - src_org, arrays, total_size))
    }

    // ----------------------------------------------------------- LZ phase

    /// Kraken_UnpackOffsets -> (offs_stream, len_stream).
    fn unpack_offsets(
        &mut self,
        src: i64,
        src_end: i64,
        packed_offs: &[u8],
        packed_offs_extra: Option<&[u8]>,
        multi_dist_scale: i64,
        packed_litlen: &[u8],
    ) -> R<(Vec<i64>, Vec<i64>)> {
        let mut bits_a = Bits::new(self.buf, src, src_end, false);
        let mut bits_b = Bits::new(self.buf, src_end, src, true);

        if bits_b.bits < 0x2000 {
            return err("bad offset stream");
        }
        let mut n = 31 - bsr(bits_b.bits);
        bits_b.bitpos += n;
        bits_b.bits = shl(bits_b.bits, n);
        bits_b.refill();
        n += 1;
        let u32_len_stream_size = shr(bits_b.bits, 32 - n) as i64 - 1;
        bits_b.bitpos += n;
        bits_b.bits = shl(bits_b.bits, n);
        bits_b.refill();

        let mut offs: Vec<i64> = Vec::with_capacity(packed_offs.len());
        let count = packed_offs.len();
        self.hit(|| format!(
            "offsets:{}",
            if multi_dist_scale == 0 { "classic".to_string() } else { format!("scaled{multi_dist_scale}") }
        ));
        if multi_dist_scale == 0 {
            let mut i = 0;
            while i < count {
                offs.push(-bits_a.read_distance(packed_offs[i] as u32));
                i += 1;
                if i == count {
                    break;
                }
                offs.push(-bits_b.read_distance(packed_offs[i] as u32));
                i += 1;
            }
        } else {
            let mut i = 0;
            while i < count {
                let cmd = packed_offs[i] as i64;
                i += 1;
                if (cmd >> 3) > 26 {
                    return err("bad offset stream");
                }
                let o = ((8 + (cmd & 7)) << (cmd >> 3)) | bits_a.read_more_than_24(cmd >> 3) as i64;
                offs.push(8 - o);
                if i == count {
                    break;
                }
                let cmd = packed_offs[i] as i64;
                i += 1;
                if (cmd >> 3) > 26 {
                    return err("bad offset stream");
                }
                let o = ((8 + (cmd & 7)) << (cmd >> 3)) | bits_b.read_more_than_24(cmd >> 3) as i64;
                offs.push(8 - o);
            }
            if multi_dist_scale != 1 {
                let extra = packed_offs_extra.ok_or_else(|| KrakenError("bad offset stream".into()))?;
                for (k, o) in offs.iter_mut().enumerate() {
                    *o = multi_dist_scale * *o - extra[k] as i64;
                }
            }
        }

        if u32_len_stream_size > 512 {
            return err("bad length stream");
        }
        let mut u32_len: Vec<i64> = Vec::new();
        let mut i: i64 = 0;
        while i + 1 < u32_len_stream_size {
            u32_len.push(bits_a.read_length()?);
            u32_len.push(bits_b.read_length()?);
            i += 2;
        }
        if i < u32_len_stream_size {
            u32_len.push(bits_a.read_length()?);
        }

        bits_a.p -= (24 - bits_a.bitpos) >> 3;
        bits_b.p += (24 - bits_b.bitpos) >> 3;
        if bits_a.p != bits_b.p {
            return err("bad length stream");
        }

        let mut lens = Vec::with_capacity(packed_litlen.len());
        let mut k = 0usize;
        for &v in packed_litlen {
            let mut v = v as i64;
            if v == 255 {
                if k >= u32_len.len() {
                    return err("bad length stream");
                }
                v = u32_len[k] + 255;
                k += 1;
            }
            lens.push(v + 3);
        }
        if k != u32_len.len() {
            return err("bad length stream");
        }
        Ok((offs, lens))
    }

    /// Kraken_ReadLzTable -> (cmd_stream, offs_stream, lit_stream, len_stream).
    #[allow(clippy::too_many_arguments, clippy::type_complexity)]
    fn read_lz_table(
        &mut self,
        mode: i64,
        mut src: i64,
        src_end: i64,
        out: &mut [u8],
        mut dst: usize,
        dst_size: usize,
        offset: usize,
    ) -> R<(Vec<u8>, Vec<i64>, Vec<u8>, Vec<i64>)> {
        if mode > 1 {
            return Err(KrakenError(format!("unsupported LZ mode {mode}")));
        }
        if src_end - src < 13 {
            return err("truncated LZ block");
        }
        if offset == 0 {
            out[dst..dst + 8].copy_from_slice(self.slice(src, 8)?);
            dst += 8;
            src += 8;
        }
        let _ = dst;
        if self.b(src) & 0x80 != 0 {
            return err("unsupported LZ block (excess bytes)");
        }

        let (used, lit_stream) = self.decode_bytes(src, src_end, dst_size as i64)?;
        src += used;
        let (used, cmd_stream) = self.decode_bytes(src, src_end, dst_size as i64)?;
        src += used;

        if src_end - src < 3 {
            return err("truncated LZ block");
        }
        let mut offs_scaling: i64 = 0;
        let mut packed_offs_extra: Option<Vec<u8>> = None;
        let packed_offs: Vec<u8>;
        if self.b(src) & 0x80 != 0 {
            offs_scaling = self.b(src) as i64 - 127;
            src += 1;
            let (used, po) = self.decode_bytes(src, src_end, cmd_stream.len() as i64)?;
            src += used;
            packed_offs = po;
            if offs_scaling != 1 {
                let (used, extra) = self.decode_bytes(src, src_end, packed_offs.len() as i64)?;
                if extra.len() != packed_offs.len() {
                    return err("bad offset stream");
                }
                src += used;
                packed_offs_extra = Some(extra);
            }
        } else {
            let (used, po) = self.decode_bytes(src, src_end, cmd_stream.len() as i64)?;
            src += used;
            packed_offs = po;
        }

        let (used, packed_len) = self.decode_bytes(src, src_end, (dst_size >> 2) as i64)?;
        src += used;

        let (offs, lens) =
            self.unpack_offsets(src, src_end, &packed_offs, packed_offs_extra.as_deref(), offs_scaling, &packed_len)?;
        Ok((cmd_stream, offs, lit_stream, lens))
    }

    /// Kraken_ProcessLzRuns: mode 0 adds literals to the last match, mode 1 is raw.
    #[allow(clippy::too_many_arguments)]
    fn process_lz_runs(
        &mut self,
        mode: i64,
        out: &mut [u8],
        mut dst: usize,
        dst_size: usize,
        offset: usize,
        cmd_stream: &[u8],
        offs_stream: &[i64],
        lit_stream: &[u8],
        len_stream: &[i64],
    ) -> R<()> {
        let dst_start = dst - offset;
        let dst_end = dst + dst_size;
        if offset == 0 {
            dst += 8;
        }
        let sub = mode == 0;

        let mut recent: [i64; 7] = [0, 0, 0, -8, -8, -8, 0];
        let mut last_offset: i64 = -8;
        let mut lit = 0usize;
        let mut li = 0usize;
        let mut oi = 0usize;
        let n_offs = offs_stream.len();
        let n_lens = len_stream.len();
        let n_lit = lit_stream.len();

        for &f in cmd_stream {
            let mut litlen = (f & 3) as i64;
            let offs_index = (f >> 6) as usize;
            let matchlen = ((f >> 2) & 0xF) as i64;
            if litlen == 3 {
                if li >= n_lens {
                    return err("bad LZ stream");
                }
                litlen = len_stream[li];
                li += 1;
            }
            recent[6] = if oi < n_offs { offs_stream[oi] } else { 0 };

            if litlen > 0 {
                let litlen = litlen as usize;
                if litlen > n_lit - lit || litlen > dst_end - dst {
                    return err("bad LZ stream");
                }
                if sub {
                    for k in 0..litlen {
                        let from = (dst + k) as i64 + last_offset;
                        if from < dst_start as i64 {
                            return err("bad LZ stream");
                        }
                        out[dst + k] = lit_stream[lit + k].wrapping_add(out[from as usize]);
                    }
                } else {
                    out[dst..dst + litlen].copy_from_slice(&lit_stream[lit..lit + litlen]);
                }
                dst += litlen;
                lit += litlen;
            }

            let off = recent[offs_index + 3];
            recent[offs_index + 3] = recent[offs_index + 2];
            recent[offs_index + 2] = recent[offs_index + 1];
            recent[offs_index + 1] = recent[offs_index];
            recent[3] = off;
            last_offset = off;
            if offs_index == 3 {
                if oi >= n_offs {
                    return err("bad LZ stream");
                }
                oi += 1;
            }

            if off >= 0 || (dst as i64) + off < dst_start as i64 {
                return err("bad LZ stream (offset out of bounds)");
            }

            let length = if matchlen != 15 {
                matchlen + 2
            } else {
                if li >= n_lens {
                    return err("bad LZ stream");
                }
                let l = 14 + len_stream[li];
                li += 1;
                l
            };
            if length < 0 || length as usize > dst_end - dst {
                return err("bad LZ stream (copy length out of bounds)");
            }
            copy_match(out, dst, off, length as usize);
            dst += length as usize;
        }

        if oi != n_offs || li != n_lens {
            return err("bad LZ stream");
        }
        let final_len = dst_end - dst;
        if final_len != n_lit - lit {
            return err("bad LZ stream");
        }
        if final_len > 0 {
            if sub {
                for k in 0..final_len {
                    let from = (dst + k) as i64 + last_offset;
                    if from < dst_start as i64 {
                        return err("bad LZ stream");
                    }
                    out[dst + k] = lit_stream[lit + k].wrapping_add(out[from as usize]);
                }
            } else {
                out[dst..dst_end].copy_from_slice(&lit_stream[lit..lit + final_len]);
            }
        }
        Ok(())
    }

    /// Kraken_DecodeQuantum: up to 256 KB, in 128 KB chunks with shared history.
    fn decode_quantum(&mut self, out: &mut [u8], mut dst: usize, dst_end: usize, dst_start: usize, src_in: i64, src_end: i64) -> R<i64> {
        let mut src = src_in;
        while dst_end - dst != 0 {
            let dst_count = (dst_end - dst).min(0x20000);
            if src_end - src < 4 {
                return err("truncated quantum");
            }
            let chunkhdr = self.b(src + 2) | (self.b(src + 1) << 8) | (self.b(src) << 16);
            let src_used: i64;
            if chunkhdr & 0x800000 == 0 {
                // entropy coded, no match copying
                self.hit(|| "quantum:entropy".into());
                let (used, data) = self.decode_bytes(src, src_end, dst_count as i64)?;
                if data.len() != dst_count {
                    return err("bad quantum");
                }
                out[dst..dst + dst_count].copy_from_slice(&data);
                src_used = used;
            } else {
                src += 3;
                src_used = (chunkhdr & 0x7FFFF) as i64;
                let mode = ((chunkhdr >> 19) & 0xF) as i64;
                if src_end - src < src_used {
                    return err("truncated quantum");
                }
                if src_used < dst_count as i64 {
                    self.hit(|| format!("lz:{mode}"));
                    let (cmd, offs, lit, lens) =
                        self.read_lz_table(mode, src, src + src_used, out, dst, dst_count, dst - dst_start)?;
                    self.process_lz_runs(mode, out, dst, dst_count, dst - dst_start, &cmd, &offs, &lit, &lens)?;
                } else if src_used > dst_count as i64 || mode != 0 {
                    return err("bad quantum");
                } else {
                    self.hit(|| "quantum:raw".into());
                    out[dst..dst + dst_count].copy_from_slice(self.slice(src, dst_count as i64)?);
                }
            }
            src += src_used;
            dst += dst_count;
        }
        Ok(src - src_in)
    }

    // ------------------------------------------------------- stream level

    /// -> (uncompressed, use_checksums), and the position after the header.
    fn parse_header(&self, p: i64) -> R<((bool, bool), i64)> {
        if p + 2 > self.buf.len() as i64 {
            return err("truncated header");
        }
        let b = self.b(p);
        if (b & 0xF) != 0xC || ((b >> 4) & 3) != 0 {
            return err("not an Oodle stream");
        }
        let uncompressed = (b >> 6) & 1 != 0;
        let b = self.b(p + 1);
        let decoder_type = b & 0x7F;
        let use_checksums = b >> 7 != 0;
        let name = match decoder_type {
            5 => "LZNA",
            6 => "Kraken",
            10 => "Mermaid/Selkie",
            11 => "Bitknit",
            12 => "Leviathan",
            _ => return err("not an Oodle stream"),
        };
        if decoder_type != 6 {
            return Err(KrakenError(format!("{name} streams are not supported - only Kraken is")));
        }
        Ok(((uncompressed, use_checksums), p + 2))
    }

    /// -> (compressed_size, memset_byte), and the position after the header.
    fn parse_quantum_header(&self, p: i64, use_checksum: bool) -> R<((i64, Option<u8>), i64)> {
        if p + 3 > self.buf.len() as i64 {
            return err("truncated quantum header");
        }
        let v = (self.b(p) << 16) | (self.b(p + 1) << 8) | self.b(p + 2);
        let size = (v & 0x3FFFF) as i64;
        if size != 0x3FFFF {
            return Ok(((size + 1, None), if use_checksum { p + 6 } else { p + 3 }));
        }
        if (v >> 18) == 1 {
            if p + 4 > self.buf.len() as i64 {
                return err("truncated quantum header");
            }
            return Ok(((0, Some(self.b(p + 3) as u8)), p + 4));
        }
        err("bad quantum header")
    }

    fn decompress(&mut self, dst_len: usize) -> R<Vec<u8>> {
        let src_len = self.buf.len() as i64;
        let mut out = vec![0u8; dst_len];
        let mut p: i64 = 0;
        let mut offset = 0usize;
        let mut hdr: Option<(bool, bool)> = None;
        while offset < dst_len {
            if offset & 0x3FFFF == 0 {
                let (h, np) = self.parse_header(p)?;
                hdr = Some(h);
                p = np;
            }
            let (uncompressed, use_checksums) = hdr.ok_or_else(|| KrakenError("missing header".into()))?;
            let dst_bytes_left = (dst_len - offset).min(0x40000);
            if uncompressed {
                self.hit(|| "stream:uncompressed".into());
                if src_len - p < dst_bytes_left as i64 {
                    return err("truncated stream");
                }
                out[offset..offset + dst_bytes_left].copy_from_slice(self.slice(p, dst_bytes_left as i64)?);
                p += dst_bytes_left as i64;
                offset += dst_bytes_left;
                continue;
            }
            let ((compressed_size, memset_byte), np) = self.parse_quantum_header(p, use_checksums)?;
            p = np;
            if p > src_len || src_len - p < compressed_size {
                return err("truncated stream");
            }
            if compressed_size > dst_bytes_left as i64 {
                return err("bad quantum header");
            }
            if compressed_size == 0 {
                self.hit(|| "stream:memset".into());
                out[offset..offset + dst_bytes_left].fill(memset_byte.unwrap_or(0));
            } else if compressed_size == dst_bytes_left as i64 {
                self.hit(|| "stream:stored".into());
                out[offset..offset + dst_bytes_left].copy_from_slice(self.slice(p, dst_bytes_left as i64)?);
                p += dst_bytes_left as i64;
            } else {
                let n = self.decode_quantum(&mut out, offset, offset + dst_bytes_left, 0, p, p + compressed_size)?;
                if n != compressed_size {
                    return err("quantum size mismatch");
                }
                p += compressed_size;
            }
            offset += dst_bytes_left;
        }
        if p != src_len {
            return err("trailing data after the stream");
        }
        Ok(out)
    }
}

/// `out[dst..dst+length] = out[dst+offset..]`, overlap-aware (offset < 0):
/// the pattern of `-offset` bytes repeats.
fn copy_match(out: &mut [u8], dst: usize, offset: i64, length: usize) {
    let src = (dst as i64 + offset) as usize;
    let dist = (-offset) as usize;
    if length <= dist {
        out.copy_within(src..src + length, dst);
    } else {
        for k in 0..length {
            out[dst + k] = out[src + k];
        }
    }
}
