# Asset dump toolchain

Inspection of *Life is Strange: Double Exposure* game data, and the writer that
publishes the full-width UI fix as a mod container. Pure Python 3, including the
decompressor. No FModel / retoc / UAssetGUI required, and nothing to download.

The game's own files are only ever read: the fix writes a container of its own into
`Content/Paks/Mods/`, which the engine mounts after `pakchunk0` and which therefore
shadows the packages in it.

The game's containers are **not encrypted** (`ContainerFlags=0x09` = Compressed|Indexed,
null `EncryptionKeyGuid`, plaintext directory index). Every compressed block is Oodle
Kraken, and `kraken.py` decodes it.

## Decompression

Oodle ships statically linked inside `Chronos-Win64-Shipping.exe`, and Epic's own
decompressor cannot be redistributed, so `kraken.py` is a pure-Python port of the
Kraken decoder from [ooz](https://github.com/powzix/ooz) (GPL-3.0-or-later, like this
project). `oodle.py` is the one entry point the readers call; it uses `kraken.py`
unless `LISDE_OODLE_DLL` names a native Oodle library, which is only worth doing for
research runs that decode far more than the fix does (the Python decoder manages
roughly 7 MB/s). The installer never sets that variable and never looks for a library.

`kraken.py` handles Kraken only (decoder type 6). Mermaid, Selkie, Leviathan, LZNA
and Bitknit are rejected with a named error.

`../../tests/test_kraken.py` checks the decoder against streams made by Epic's
compressor from non-game inputs, and `verify_kraken.py` here compares it with a native
Oodle library on the game's own blocks - run that after a game update, on a machine
that has such a library.

## Files

| File | Purpose |
| :--- | :--- |
| `kraken.py`  | pure-Python Oodle Kraken decoder |
| `oodle.py`   | `decompress()` for the readers: `kraken.py`, or a native library when `LISDE_OODLE_DLL` is set |
| `iostore.py` | `.utoc`/`.ucas` reader: header, chunk table, compression blocks, plaintext directory index, `global.utoc` script-object table |
| `pak.py`     | UE5 `.pak` v11 reader (index + encoded entries) - holds the cooked `.ini` config |
| `zen.py`     | Zen package parser: name batch, import/export maps, export-bundle data layout |
| `unver.py`   | Unversioned-property header decoder (fragments + zero mask) |
| `slots.py`   | `UCanvasPanelSlot` decoder - prints anchors/offsets/alignment per widget |
| `container.py` | `.utoc`/`.ucas`/`.pak` **writer**: TOC perfect hash, `FIoContainerHeader`, directory index, stub pak (RESEARCH.md 12) |
| `patch_ui_layout.py` | the fix itself: edits the UI slots and publishes the result as a mod container |
| `verify_kraken.py` | research check: `kraken.py` against a native Oodle library on the game's blocks |
| `make_kraken_vectors.py` | regenerates `tests/kraken/` from non-game inputs; needs Oodle 2.9.10 with `OodleLZ_Compress` |

## Usage

    cd <game>/Chronos/Content/Paks
    python .../slots.py BP_LoadingWindow BP_NotificationWindow BP_PauseWindow
    python .../zen.py pakchunk0-Windows.utoc BP_LoadingWindow      # export map
    python .../container.py pakchunk0-Windows.utoc                 # check a container

## Notes / gotchas

- Export payloads are laid out in **export-bundle order**, not export-map order.
  `CookedSerialOffset` is relative to the original cooked file, so
  `data_offset = HeaderSize + accumulated sizes in bundle order`.
- Unversioned property schema order is **derived-class-first**, then base
  (`TFieldIterator` order). For `UCanvasPanelSlot`:
  `0=LayoutData 1=bAutoSize 2=ZOrder 3=Parent 4=Content`.
- Object references inside export payloads are 4-byte 1-based `FPackageIndex`
  values, not the 8-byte `FPackageObjectIndex` used in the import/export maps.
- Each export's serialized data ends with 4 trailing zero bytes.
- A container's perfect hash is FNV-**1** (multiply, then xor), 64-bit, over the
  12 chunk-id bytes, and the modulo is taken on the full 64-bit value - truncating
  to `uint32` first resolves most chunks and quietly misses a few.
- In `FFilePackageStoreEntry`, an array view's offset is measured from the view
  itself, not from the end of its `{count, offset}` pair.
- Property *values* decode without a `.usmap` only because the schemas above are
  hardcoded. Anything beyond these structs needs a real mappings file
  (UE4SS `dumpusmap` or Dumper-7).
