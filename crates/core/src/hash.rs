//! The three digests the container formats need: SHA-1 for the stub `.pak`'s
//! index hashes, SHA-256 for the container id and the game-build
//! fingerprint, BLAKE3 for the IoStore chunk metadata. They come from the
//! RustCrypto `sha1` and `sha2` crates and the reference `blake3` crate,
//! all pure Rust: `blake3`'s `pure` feature keeps its C and assembly
//! back-ends out of the build, so no C compiler is needed on any target and
//! nothing is added at run time. The specifications' own vectors below pin
//! them, and the reference-container test pins the bytes they produce.

use sha2::Digest as _;

/// Incremental SHA-256, for hashing a file in pieces.
pub struct Sha256(sha2::Sha256);

impl Default for Sha256 {
    fn default() -> Self {
        Self::new()
    }
}

impl Sha256 {
    pub fn new() -> Self {
        Sha256(sha2::Sha256::new())
    }

    pub fn update(&mut self, data: &[u8]) {
        self.0.update(data);
    }

    pub fn finish(self) -> [u8; 32] {
        self.0.finalize().into()
    }
}

pub fn sha256(data: &[u8]) -> [u8; 32] {
    sha2::Sha256::digest(data).into()
}

pub fn sha1(data: &[u8]) -> [u8; 20] {
    sha1::Sha1::digest(data).into()
}

/// The unkeyed BLAKE3 hash of `data`, `length` bytes of it.
pub fn blake3(data: &[u8], length: usize) -> Vec<u8> {
    let mut out = vec![0u8; length];
    blake3::Hasher::new().update(data).finalize_xof().fill(&mut out);
    out
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
