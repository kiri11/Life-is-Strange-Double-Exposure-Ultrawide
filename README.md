# Life is Strange: Double Exposure - Native Ultrawide & Aspect Ratio Fix (21:9 / 32:9 / 32:10 / 16:10)

A comprehensive, native aspect ratio fix for **Life is Strange: Double Exposure** that unlocks true ultrawide gameplay and photos without the photo-skewing bugs or FOV stretching introduced by generic memory patches.

---

## Why This Fix Is Different (The Breakthrough)

### The Problem With Previous Fixes:
- **SUWSF / Bramble Patch:** Only applied a crude 6-byte `NOP` over the black-bar check in RAM. The engine's nominal camera aspect ratio was left hardcoded at 16:9 (`1.7777778f`), which caused **Max's camera and Polaroid photos to be heavily stretched and distorted**.
- **Partial Hex Edits:** Older community guides only patched 1 or 2 search occurrences in HxD, leaving the rest of the camera components and view targets unpatched.

### The Solution:
Through disassembly analysis of `Chronos-Win64-Shipping.exe`, we identified the complete map of hardcoded camera aspect ratio locations and the static photo projection constant table at `0x69C8A8C`.

By synchronizing the target aspect ratio float directly, the photography capture pipeline and exploration camera operate natively with zero photo distortion.

---

## Cutscene Framing Modes

Unreal Engine 5 cinematics in *Life is Strange: Double Exposure* are authored using fixed 16:9 CineCamera lenses. The patcher provides two framing options:

1. **Recommended: Uncropped 16:9 Cutscenes (2-Offset Clean Patch)**
   - **Exploration & Photos:** Full native 21:9 / 32:9 ultrawide with unskewed photos.
   - **Cutscenes & Dialogues:** Pristine uncropped 16:9 framing (0% vertical crop, character heads/chins and cinematic headroom 100% preserved).
2. **Full Ultrawide Cutscenes (11-Offset Full Patch)**
   - **Exploration & Photos:** Full native 21:9 / 32:9 ultrawide.
   - **Cutscenes & Dialogues:** Spans edge-to-edge with no black bars (with the inherent ~20% 16:9 lens vertical crop).

---

## Features

- **Native Aspect Ratio Support:** Full support for 21:9 (UWQHD / WUHD), 32:9 (Super Ultrawide), 32:10, 16:10, and custom resolutions.
- **Cutscene Framing Options:** Choose between uncropped 16:9 director's framing or full ultrawide cutscenes.
- **Unskewed Polaroid Camera:** Max's in-game Polaroid and camera captures retain 100% proper proportion and aspect ratio.
- **1-Click Backup & Restore:** Automatically creates a clean `.original` backup and allows reverting to stock settings with a single click.

---

## Supported Resolutions & Aspect Ratios

| Resolution | Aspect Ratio | Hex Replacement Value |
| :--- | :--- | :--- |
| **5120x2160** (WUHD 4K) | 21.33:9 (`2.3703703`) | `26 B4 17 40` |
| **3440x1440** (UWQHD) | 21.5:9 (`2.3888889`) | `39 8E 18 40` |
| **2560x1080** (UWD) | 21.33:9 (`2.3703703`) | `26 B4 17 40` |
| **3840x1600** (UW) | 24:10 (`2.4000000`) | `9A 99 19 40` |
| **5120x1440** (Super Ultrawide) | 32:9 (`3.5555556`) | `39 8E 63 40` |
| **3840x1080** (Super Ultrawide) | 32:9 (`3.5555556`) | `39 8E 63 40` |
| **7680x2160** (Super Ultrawide) | 32:9 (`3.5555556`) | `39 8E 63 40` |
| **3840x1200** / **4320x1350** | 32:10 (`3.2000000`) | `CD CC 4C 40` |
| **2560x1600** / **1920x1200** | 16:10 (`1.6000000`) | `CD CC CC 3F` |
| **Custom** | *Any Width × Height* | *Auto-calculated* |

---

## Installation Options

### Option 1: GUI Patcher (Windows)
1. Run **`LiSUltrawidePatcher.exe`**.
2. The patcher will automatically detect your game directory and monitor resolution.
3. Select your desired **Cutscene Framing Mode** (Uncropped 16:9 is recommended to avoid vertical cropping).
4. Click **Patch Game Executable**.
5. Launch **Life is Strange: Double Exposure**.

*(An automatic `.original` backup of your executable is created before patching. You can click **Restore Original** at any time).*

### Option 2: Python Script (Steam Deck / Linux / Windows / macOS)
1. Run `python patcher.py` in your terminal.
2. Select your resolution preset and cutscene framing mode.

**Python Compatibility:**
- Requires Python 3.6 or newer (supports 3.6 through 3.14+).
- Uses only built-in standard library modules (`sys`, `os`, `struct`, `shutil`).
- No external packages (`pip install`) required.
- Works out-of-the-box on Steam Deck (SteamOS), Linux / Proton, Windows, and macOS.

---

## Manual Hex Editing (For HxD Users)

### Recommended Mode (Clean: Exploration 21:9 + Cutscenes 16:9 Uncropped):
Open `Chronos-Win64-Shipping.exe` in **HxD** and patch only these 2 locations:
- `0x23E665C` (Player Camera) -> Replace `3B 8E E3 3F` with your resolution hex (e.g. `26 B4 17 40`).
- `0x69C8A8C` (Photo Table) -> Replace `39 8E E3 3F` with your resolution hex (e.g. `26 B4 17 40`).

### Full Ultrawide Mode (All 11 Locations):
Patch all 11 locations listed below:

| Offset (Hex) | Original Bytes (16:9) | Description |
| :--- | :--- | :--- |
| `0x23E5558` | `3B 8E E3 3F` | `APlayerCameraManager` |
| `0x23E5739` | `3B 8E E3 3F` | `APlayerCameraManager` |
| `0x23E665C` | `3B 8E E3 3F` | `UCameraComponent` (Player Pawn) |
| `0x257BDEC` | `39 8E E3 3F` | `ACameraActor` |
| `0x43FEB0F` | `3B 8E E3 3F` | `FMinimalViewInfo` |
| `0x43FEB58` | `3B 8E E3 3F` | `CameraAnim` |
| `0x43FEFD1` | `3B 8E E3 3F` | `FMinimalViewInfo` |
| `0x44004BF` | `3B 8E E3 3F` | `FMinimalViewInfo` |
| `0x440050B` | `3B 8E E3 3F` | `CameraAnim` |
| `0x4401BBF` | `3B 8E E3 3F` | `FMinimalViewInfo` |
| `0x69C8A8C` | `39 8E E3 3F` | Static Photo Projection Float Table |

---

## License
This project is licensed under the **GNU General Public License v3.0 or later** - see [LICENSE](LICENSE). You are free to use, study, modify and redistribute it; any distributed modification must ship its complete source under the same terms.

As an additional term under GPL-3.0 section 7(b), every copy or modified version must preserve the copyright notice and credit the original author, Kiri11, with a link to this project.
