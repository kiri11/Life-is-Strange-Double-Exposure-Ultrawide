//! The three digests the container formats need, written out in full so the
//! fix depends on nothing: SHA-1 for the stub `.pak`'s index hashes, SHA-256
//! for the container id and the game-build fingerprint, BLAKE3 for the
//! IoStore chunk metadata. Each run hashes a dozen packages of a few dozen
//! KB and one megabyte of the game's data, so speed is beside the point;
//! correctness is checked against the specifications' own vectors below.

// ---------------------------------------------------------------- SHA-256

const K256: [u32; 64] = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
];

pub struct Sha256 {
    h: [u32; 8],
    buf: Vec<u8>,
    len: u64,
}

impl Default for Sha256 {
    fn default() -> Self {
        Self::new()
    }
}

impl Sha256 {
    pub fn new() -> Self {
        Sha256 {
            h: [
                0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab,
                0x5be0cd19,
            ],
            buf: Vec::with_capacity(64),
            len: 0,
        }
    }

    fn block(h: &mut [u32; 8], block: &[u8]) {
        let mut w = [0u32; 64];
        for (i, word) in w.iter_mut().take(16).enumerate() {
            *word = u32::from_be_bytes(block[i * 4..i * 4 + 4].try_into().unwrap());
        }
        for i in 16..64 {
            let s0 = w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3);
            let s1 = w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16].wrapping_add(s0).wrapping_add(w[i - 7]).wrapping_add(s1);
        }
        let [mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut hh] = *h;
        for i in 0..64 {
            let s1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let ch = (e & f) ^ (!e & g);
            let t1 = hh.wrapping_add(s1).wrapping_add(ch).wrapping_add(K256[i]).wrapping_add(w[i]);
            let s0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let maj = (a & b) ^ (a & c) ^ (b & c);
            let t2 = s0.wrapping_add(maj);
            hh = g;
            g = f;
            f = e;
            e = d.wrapping_add(t1);
            d = c;
            c = b;
            b = a;
            a = t1.wrapping_add(t2);
        }
        for (x, y) in h.iter_mut().zip([a, b, c, d, e, f, g, hh]) {
            *x = x.wrapping_add(y);
        }
    }

    pub fn update(&mut self, mut data: &[u8]) {
        self.len += data.len() as u64;
        if !self.buf.is_empty() {
            let take = (64 - self.buf.len()).min(data.len());
            self.buf.extend_from_slice(&data[..take]);
            data = &data[take..];
            if self.buf.len() == 64 {
                let block = std::mem::take(&mut self.buf);
                Self::block(&mut self.h, &block);
            }
        }
        while data.len() >= 64 {
            Self::block(&mut self.h, &data[..64]);
            data = &data[64..];
        }
        self.buf.extend_from_slice(data);
    }

    pub fn finish(mut self) -> [u8; 32] {
        let bits = self.len * 8;
        let mut pad = vec![0x80u8];
        while (self.buf.len() + pad.len()) % 64 != 56 {
            pad.push(0);
        }
        pad.extend_from_slice(&bits.to_be_bytes());
        self.len -= pad.len() as u64; // update() counts it again; the count is done with
        self.update(&pad);
        let mut out = [0u8; 32];
        for (i, word) in self.h.iter().enumerate() {
            out[i * 4..i * 4 + 4].copy_from_slice(&word.to_be_bytes());
        }
        out
    }
}

pub fn sha256(data: &[u8]) -> [u8; 32] {
    let mut h = Sha256::new();
    h.update(data);
    h.finish()
}

// ------------------------------------------------------------------ SHA-1

pub fn sha1(data: &[u8]) -> [u8; 20] {
    let mut h: [u32; 5] = [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0];
    let mut msg = data.to_vec();
    let bits = (data.len() as u64) * 8;
    msg.push(0x80);
    while msg.len() % 64 != 56 {
        msg.push(0);
    }
    msg.extend_from_slice(&bits.to_be_bytes());
    for block in msg.chunks(64) {
        let mut w = [0u32; 80];
        for (i, word) in w.iter_mut().take(16).enumerate() {
            *word = u32::from_be_bytes(block[i * 4..i * 4 + 4].try_into().unwrap());
        }
        for i in 16..80 {
            w[i] = (w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16]).rotate_left(1);
        }
        let [mut a, mut b, mut c, mut d, mut e] = h;
        for (i, wi) in w.iter().enumerate() {
            let (f, k) = match i {
                0..=19 => ((b & c) | (!b & d), 0x5A827999),
                20..=39 => (b ^ c ^ d, 0x6ED9EBA1),
                40..=59 => ((b & c) | (b & d) | (c & d), 0x8F1BBCDC),
                _ => (b ^ c ^ d, 0xCA62C1D6),
            };
            let t = a.rotate_left(5).wrapping_add(f).wrapping_add(e).wrapping_add(k).wrapping_add(*wi);
            e = d;
            d = c;
            c = b.rotate_left(30);
            b = a;
            a = t;
        }
        for (x, y) in h.iter_mut().zip([a, b, c, d, e]) {
            *x = x.wrapping_add(y);
        }
    }
    let mut out = [0u8; 20];
    for (i, word) in h.iter().enumerate() {
        out[i * 4..i * 4 + 4].copy_from_slice(&word.to_be_bytes());
    }
    out
}

// ----------------------------------------------------------------- BLAKE3
// Unkeyed, single-threaded, structured as the specification describes:
// 1024-byte chunks, a binary tree of chaining values, extendable output.

const BLAKE3_IV: [u32; 8] =
    [0x6A09E667, 0xBB67AE85, 0x3C6EF372, 0xA54FF53A, 0x510E527F, 0x9B05688C, 0x1F83D9AB, 0x5BE0CD19];
const MSG_PERMUTATION: [usize; 16] = [2, 6, 3, 10, 7, 0, 4, 13, 1, 11, 12, 5, 9, 14, 15, 8];
const CHUNK_START: u32 = 1;
const CHUNK_END: u32 = 2;
const PARENT: u32 = 4;
const ROOT: u32 = 8;
const BLOCK_LEN: usize = 64;
const CHUNK_LEN: usize = 1024;

fn g(s: &mut [u32; 16], a: usize, b: usize, c: usize, d: usize, mx: u32, my: u32) {
    s[a] = s[a].wrapping_add(s[b]).wrapping_add(mx);
    s[d] = (s[d] ^ s[a]).rotate_right(16);
    s[c] = s[c].wrapping_add(s[d]);
    s[b] = (s[b] ^ s[c]).rotate_right(12);
    s[a] = s[a].wrapping_add(s[b]).wrapping_add(my);
    s[d] = (s[d] ^ s[a]).rotate_right(8);
    s[c] = s[c].wrapping_add(s[d]);
    s[b] = (s[b] ^ s[c]).rotate_right(7);
}

fn round(s: &mut [u32; 16], m: &[u32; 16]) {
    g(s, 0, 4, 8, 12, m[0], m[1]);
    g(s, 1, 5, 9, 13, m[2], m[3]);
    g(s, 2, 6, 10, 14, m[4], m[5]);
    g(s, 3, 7, 11, 15, m[6], m[7]);
    g(s, 0, 5, 10, 15, m[8], m[9]);
    g(s, 1, 6, 11, 12, m[10], m[11]);
    g(s, 2, 7, 8, 13, m[12], m[13]);
    g(s, 3, 4, 9, 14, m[14], m[15]);
}

/// The 7-round compression function.
fn compress(cv: &[u32; 8], block: &[u32; 16], counter: u64, block_len: u32, flags: u32) -> [u32; 16] {
    let mut state = [
        cv[0], cv[1], cv[2], cv[3], cv[4], cv[5], cv[6], cv[7],
        BLAKE3_IV[0], BLAKE3_IV[1], BLAKE3_IV[2], BLAKE3_IV[3],
        counter as u32, (counter >> 32) as u32, block_len, flags,
    ];
    let mut m = *block;
    for i in 0..7 {
        round(&mut state, &m);
        if i < 6 {
            let mut p = [0u32; 16];
            for (j, slot) in p.iter_mut().enumerate() {
                *slot = m[MSG_PERMUTATION[j]];
            }
            m = p;
        }
    }
    for i in 0..8 {
        state[i] ^= state[i + 8];
        state[i + 8] ^= cv[i];
    }
    state
}

/// 64 bytes as 16 little-endian words; a short block is zero-padded.
fn words(block: &[u8]) -> [u32; 16] {
    let mut padded = [0u8; BLOCK_LEN];
    padded[..block.len()].copy_from_slice(block);
    let mut w = [0u32; 16];
    for (i, word) in w.iter_mut().enumerate() {
        *word = u32::from_le_bytes(padded[i * 4..i * 4 + 4].try_into().unwrap());
    }
    w
}

/// A node's final compression, deferred so the root can add the ROOT flag.
struct Output {
    cv: [u32; 8],
    block: [u32; 16],
    counter: u64,
    block_len: u32,
    flags: u32,
}

impl Output {
    fn chaining_value(&self) -> [u32; 8] {
        compress(&self.cv, &self.block, self.counter, self.block_len, self.flags)[..8].try_into().unwrap()
    }

    fn root_bytes(&self, length: usize) -> Vec<u8> {
        let mut out = Vec::with_capacity(length + BLOCK_LEN);
        let mut counter = 0u64;
        while out.len() < length {
            let w = compress(&self.cv, &self.block, counter, self.block_len, self.flags | ROOT);
            for word in w {
                out.extend_from_slice(&word.to_le_bytes());
            }
            counter += 1;
        }
        out.truncate(length);
        out
    }
}

fn chunk_output(chunk: &[u8], counter: u64) -> Output {
    let mut cv = BLAKE3_IV;
    let blocks: Vec<&[u8]> = if chunk.is_empty() { vec![&[][..]] } else { chunk.chunks(BLOCK_LEN).collect() };
    for (i, block) in blocks[..blocks.len() - 1].iter().enumerate() {
        let flags = if i == 0 { CHUNK_START } else { 0 };
        cv = compress(&cv, &words(block), counter, BLOCK_LEN as u32, flags)[..8].try_into().unwrap();
    }
    let last = blocks[blocks.len() - 1];
    let flags = CHUNK_END | if blocks.len() == 1 { CHUNK_START } else { 0 };
    Output { cv, block: words(last), counter, block_len: last.len() as u32, flags }
}

fn parent_output(left: [u32; 8], right: [u32; 8]) -> Output {
    let mut block = [0u32; 16];
    block[..8].copy_from_slice(&left);
    block[8..].copy_from_slice(&right);
    Output { cv: BLAKE3_IV, block, counter: 0, block_len: BLOCK_LEN as u32, flags: PARENT }
}

/// Bytes in the left subtree: the largest power-of-two chunk count below it.
fn left_len(total: usize) -> usize {
    let chunks = total.div_ceil(CHUNK_LEN);
    let mut power = 1;
    while power * 2 < chunks {
        power *= 2;
    }
    power * CHUNK_LEN
}

fn subtree_output(data: &[u8], counter: u64) -> Output {
    if data.len() <= CHUNK_LEN {
        return chunk_output(data, counter);
    }
    let split = left_len(data.len());
    let left = subtree_output(&data[..split], counter);
    let right = subtree_output(&data[split..], counter + (split / CHUNK_LEN) as u64);
    parent_output(left.chaining_value(), right.chaining_value())
}

/// The unkeyed BLAKE3 hash of `data`, `length` bytes of it.
pub fn blake3(data: &[u8], length: usize) -> Vec<u8> {
    subtree_output(data, 0).root_bytes(length)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::to_hex;

    #[test]
    fn sha256_vectors() {
        assert_eq!(to_hex(&sha256(b"")), "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
        assert_eq!(to_hex(&sha256(b"abc")), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
        // a two-block message, and the same fed in pieces
        let msg = b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq";
        assert_eq!(to_hex(&sha256(msg)), "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");
        let mut h = Sha256::new();
        for piece in msg.chunks(7) {
            h.update(piece);
        }
        assert_eq!(h.finish(), sha256(msg));
        let million = vec![b'a'; 1_000_000];
        assert_eq!(to_hex(&sha256(&million)), "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0");
    }

    #[test]
    fn sha1_vectors() {
        assert_eq!(to_hex(&sha1(b"")), "da39a3ee5e6b4b0d3255bfef95601890afd80709");
        assert_eq!(to_hex(&sha1(b"abc")), "a9993e364706816aba3e25717850c26c9cd0d89d");
        assert_eq!(
            to_hex(&sha1(b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq")),
            "84983e441c3bd26ebaae4aa1f95129e5e54670f1"
        );
    }

    #[test]
    fn blake3_spec_vectors() {
        // the specification's vectors: input is 0,1,2,...,250 repeating
        let vectors = [
            (0, "af1349b9f5f9a1a6a0404dea36dcc9499bcb25c9adc112b7cc9a93cae41f3262"),
            (1, "2d3adedff11b61f14c886e35afa036736dcd87a74d27b5c1510225d0f592e213"),
            (1023, "10108970eeda3eb932baac1428c7a2163b0e924c9a9e25b35bba72b28f70bd11"),
            (1024, "42214739f095a406f3fc83deb889744ac00df831c10daa55189b5d121c855af7"),
            (2048, "e776b6028c7cd22a4d0ba182a8bf62205d2ef576467e838ed6f2529b85fba24a"),
            (3072, "b98cb0ff3623be03326b373de6b9095218513e64f1ee2edd2525c7ad1e5cffd2"),
        ];
        for (n, want) in vectors {
            let data: Vec<u8> = (0..n).map(|i| (i % 251) as u8).collect();
            assert_eq!(to_hex(&blake3(&data, 32)), want, "input length {n}");
        }
        // extendable output: a prefix of the longer digest, and past one block
        let data: Vec<u8> = (0..3072).map(|i| (i % 251) as u8).collect();
        assert_eq!(blake3(&data, 20), blake3(&data, 32)[..20]);
        assert_eq!(blake3(&data, 131)[..64], blake3(&data, 64)[..]);
    }
}
