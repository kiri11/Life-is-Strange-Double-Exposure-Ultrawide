# Life is Strange: Double Exposure - Ultrawide & Cutscene Fix

A lightweight, native ultrawide fix for **Life is Strange: Double Exposure** supporting 21:9, 32:9, 32:10, and custom resolutions.

---

## What This Fix Does

- **Native Ultrawide Gameplay:** Unlocks full 21:9 / 32:9 rendering during free-roam exploration.
- **Unskewed Photo Mechanics:** Corrects the static projection table to ensure Max's Polaroid photographs and in-game camera captures retain proper 1:1 proportions without horizontal stretching.
- **Uncropped 16:9 Cutscenes (Recommended):** Keeps cinematic cutscenes and dialogues framed in pristine, uncropped 16:9 with side pillarboxes (0% vertical crop, 100% facial and cinematic headroom preserved). Full ultrawide cutscenes are also optionally supported.
- **Zero Performance Impact:** Direct executable or in-memory patching with no background overhead.

---

## Supported Resolutions

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
| **Custom** | *Any Width x Height* | *Auto-calculated* |

---

## Installation Methods

Choose any of the following installation methods:

### Method 1: Windows GUI Patcher (Recommended for Windows)
1. Run **`LiSUltrawidePatcher.exe`**.
2. The tool will auto-detect your game executable and display resolution.
3. Choose your **Cutscene Framing Mode** (Uncropped 16:9 is recommended).
4. Click **Patch Game Executable**.
5. Launch the game through Steam.

*(An automatic `.original` backup is created before patching. You can click **Restore Original Stock** at any time).*

### Method 2: Python Script (Steam Deck / Linux / Proton / macOS / Windows)
1. Open a terminal in the folder and run:
   ```bash
   python patcher.py
   ```
2. Select your resolution preset and cutscene framing mode.

**Requirements:** Python 3.6+ using built-in standard libraries only. Zero external dependencies (`pip`) required.

### Method 3: In-Memory Patching via SUWSF (No File Modifications)
If you prefer not to modify your `.exe` file on disk:
1. Download [SUWSF](https://github.com/PhantomGamers/SUWSF) and place `SUWSF.asi` and `dxgi.dll` in `Chronos/Binaries/Win64/`.
2. Copy the included **`SUWSF.ini`** into `Chronos/Binaries/Win64/`.
3. Edit `SUWSF.ini` to set `Resolution="YourResolution"` (e.g. `5120x2160` or `3440x1440`).
4. Launch the game normally.

### Method 4: Manual Hex Editing (HxD)
1. Open `Chronos/Binaries/Win64/Chronos-Win64-Shipping.exe` in **HxD**.
2. Patch the following 2 offsets with your resolution's hex value:
   - `0x23E665C` (Player Exploration Camera) -> Replace `3B 8E E3 3F` with your resolution hex (e.g. `26 B4 17 40`).
   - `0x69C8A8C` (Photo Projection Table) -> Replace `39 8E E3 3F` with your resolution hex (e.g. `26 B4 17 40`).
3. Save the file.

---

## Technical Documentation & Research

For the full reverse-engineering breakdown, Unreal Engine 5 projection matrix analysis, complete 11-offset disassembly maps, and dead-end analyses, see **[RESEARCH.md](RESEARCH.md)**.

---

## License

This project is licensed under the **GNU General Public License v3.0 or later** - see [LICENSE](LICENSE). You are free to use, study, modify and redistribute it; any distributed modification must ship its complete source under the same terms.

As an additional term under GPL-3.0 section 7(b), every copy or modified version must preserve the copyright notice and credit the original author, Kiri11, with a link to this project.
