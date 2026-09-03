//! The Kraken decoder against streams made by Epic's own compressor.
//!
//! Every file in tests/kraken/ is a Kraken stream produced by Oodle's own
//! OodleLZ_Compress from an input that is not game data - this repository's
//! own files, and synthetic tables shaped like cooked assets - at a range of
//! levels and option settings. The compressor is Oodle 2.9.10, the build
//! Unreal Engine 5.2 ships and the game was cooked with: it matters, because
//! Epic's current builds emit only the classic Kraken codings, while 2.9.10
//! also produces the TANS, RLE and multi-array literal codings, the newer
//! Huffman table coding and scaled offsets that make up most of the game's
//! data. The set was chosen so that between them the streams exercise every
//! decoder code path the compressor could be made to emit, plus edge cases:
//! one-byte, eight-byte and nine-byte inputs (the first eight bytes of a
//! stream are stored raw), a stream that is one byte over a quantum, an
//! all-zero input (a memset quantum), an incompressible one (a stored
//! block), and one with quantum checksums.
//!
//! manifest.json records each stream's decoded size and SHA-256, and which
//! of the decoder's code paths it went through when the set was generated;
//! the test decodes every stream, compares the digest, and fails if a code
//! path recorded in the manifest was not hit - which is what would happen if
//! a change quietly routed decoding somewhere else.

use std::collections::BTreeSet;
use std::path::PathBuf;

use lis_ultrawide_core::json;
use lis_ultrawide_core::kraken::{KrakenError, decompress, decompress_with_stats};
use lis_ultrawide_core::{hash, to_hex};

fn vectors_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../tests/kraken")
}

struct Entry {
    name: String,
    raw_size: usize,
    size: usize,
    sha256: String,
    keys: Vec<String>,
}

fn manifest() -> Vec<Entry> {
    let text = std::fs::read_to_string(vectors_dir().join("manifest.json")).expect("tests/kraken/manifest.json");
    let list = json::parse(&text).unwrap();
    list.as_array()
        .unwrap()
        .iter()
        .map(|e| Entry {
            name: e.get("name").unwrap().as_str().unwrap().to_string(),
            raw_size: e.get("raw_size").unwrap().as_u64().unwrap() as usize,
            size: e.get("size").unwrap().as_u64().unwrap() as usize,
            sha256: e.get("sha256").unwrap().as_str().unwrap().to_string(),
            keys: e.get("keys").unwrap().as_array().unwrap().iter().map(|k| k.as_str().unwrap().to_string()).collect(),
        })
        .collect()
}

#[test]
fn decodes_every_vector_through_the_recorded_code_paths() {
    let entries = manifest();
    assert!(entries.len() >= 20);
    let mut expected_keys = BTreeSet::new();
    let mut all_hit = BTreeSet::new();
    let mut total = 0usize;
    let started = std::time::Instant::now();
    for e in &entries {
        let data = std::fs::read(vectors_dir().join(format!("{}.kraken", e.name))).unwrap();
        assert_eq!(data.len(), e.size, "{}: file size", e.name);
        let (out, stats) = decompress_with_stats(&data, e.raw_size).unwrap_or_else(|err| panic!("{}: {err}", e.name));
        assert_eq!(to_hex(&hash::sha256(&out)), e.sha256, "{}: decoded {} bytes, wrong content", e.name, out.len());
        let hit: BTreeSet<String> = stats.keys().cloned().collect();
        let missing: Vec<&String> = e.keys.iter().filter(|k| !hit.contains(*k)).collect();
        assert!(missing.is_empty(), "{}: decoded correctly but without {:?}", e.name, missing);
        expected_keys.extend(e.keys.iter().cloned());
        all_hit.extend(hit);
        total += out.len();
    }
    eprintln!(
        "{} vectors, {:.1} MB decoded in {:?}; code paths: {}",
        entries.len(),
        total as f64 / 1e6,
        started.elapsed(),
        all_hit.iter().cloned().collect::<Vec<_>>().join(" ")
    );
    let not_hit: Vec<&String> = expected_keys.iter().filter(|k| !all_hit.contains(*k)).collect();
    assert!(not_hit.is_empty(), "code paths in the manifest that no vector reached: {not_hit:?}");
}

#[test]
fn refuses_other_codecs_by_name_and_truncation_without_a_crash() {
    // a stream of another Oodle codec is refused by name
    let mut mermaid = vec![0x8c, 0x0a];
    mermaid.extend([0u8; 32]);
    match decompress(&mermaid, 100) {
        Err(KrakenError(msg)) => assert!(msg.contains("Mermaid"), "{msg}"),
        Ok(_) => panic!("a Mermaid stream was not refused"),
    }
    // truncation is an error rather than a crash or wrong output
    let entries = manifest();
    let largest = entries.iter().max_by_key(|e| e.size).unwrap();
    let data = std::fs::read(vectors_dir().join(format!("{}.kraken", largest.name))).unwrap();
    for cut in [1, 2, 5, 100, data.len() / 2, data.len() - 1] {
        assert!(decompress(&data[..cut], largest.raw_size).is_err(), "truncated stream ({cut} of {} bytes) decoded without error", data.len());
    }
    // and so is every single-byte corruption of a small stream
    let small = entries.iter().find(|e| e.name == "rleish-L8-sst1").unwrap();
    let data = std::fs::read(vectors_dir().join(format!("{}.kraken", small.name))).unwrap();
    let want = decompress(&data, small.raw_size).unwrap();
    let mut wrong_but_quiet = 0;
    for i in 0..data.len() {
        let mut d = data.clone();
        d[i] ^= 0x5A;
        if let Ok(out) = decompress(&d, small.raw_size)
            && out != want {
                wrong_but_quiet += 1;
            }
    }
    // Kraken has no checksum by default, so some corruptions decode to
    // something else without complaint; what matters is that none panicked.
    eprintln!("{} of {} corruptions decoded quietly to different bytes", wrong_but_quiet, data.len());
}
