# Asset dump toolchain

Read-only inspection of *Life is Strange: Double Exposure* game data. Pure Python 3
plus one native DLL for Oodle decompression. No FModel / retoc / UAssetGUI required.

The game's containers are **not encrypted** (`ContainerFlags=0x09` = Compressed|Indexed,
null `EncryptionKeyGuid`, plaintext directory index). Oodle is the only real gate.

## Setup

Oodle ships statically linked inside `Chronos-Win64-Shipping.exe`, so a standalone
decompressor is needed. **`patcher.py` handles this automatically** - it reuses one
shipped by another Unreal Engine game if you have one, and otherwise offers to
download Epic's Oodle-for-UE build. To do it by hand instead:

    curl -sL -o oodle.zip https://github.com/WorkingRobot/OodleUE/releases/latest/download/msvc-x64-release.zip
    # extract bin/oodle-data-shared.dll next to oodle.py

`oodle.py` loads, in order: `$LISDE_OODLE_DLL` (set by `patcher.py` when it locates
one elsewhere), then `oodle-data-shared.dll` / `oo2core_*_win64.dll` in this folder.

## Files

| File | Purpose |
| :--- | :--- |
| `oodle.py`   | `OodleLZ_Decompress` via ctypes |
| `iostore.py` | `.utoc`/`.ucas` reader: header, chunk table, compression blocks, plaintext directory index, `global.utoc` script-object table |
| `pak.py`     | UE5 `.pak` v11 reader (index + encoded entries) - holds the cooked `.ini` config |
| `zen.py`     | Zen package parser: name batch, import/export maps, export-bundle data layout |
| `unver.py`   | Unversioned-property header decoder (fragments + zero mask) |
| `slots.py`   | `UCanvasPanelSlot` decoder - prints anchors/offsets/alignment per widget |

## Usage

    cd <game>/Chronos/Content/Paks
    python .../slots.py BP_LoadingWindow BP_NotificationWindow BP_PauseWindow
    python .../zen.py pakchunk0-Windows.utoc BP_LoadingWindow      # export map

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
- Property *values* decode without a `.usmap` only because the schemas above are
  hardcoded. Anything beyond these structs needs a real mappings file
  (UE4SS `dumpusmap` or Dumper-7).
