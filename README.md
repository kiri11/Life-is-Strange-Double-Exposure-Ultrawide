# Life is Strange: Double Exposure - Ultrawide & Cutscene Fix

A lightweight, native ultrawide fix for **Life is Strange: Double Exposure** supporting 21:9, 32:9, 32:10, and custom resolutions.

---

## What This Fix Does

- **True Hor+ Ultrawide Cutscenes & Dialogues:** Cinematics render full-width with **0% vertical crop**. The full 16:9 vertical framing (faces, headroom, director composition) is preserved and the horizontal field of view is *expanded* to fill your monitor. No black bars, no cut chins or foreheads.
- **Proven Classic Behavior Everywhere Else:** Free-roam exploration is full-width ultrawide, Max's Polaroid photos are pixel-perfect, and loading transitions stay covered - identical to the battle-tested classic 2-offset fix.
- **Fully Static:** 13 patched bytes in the executable. No runtime hooks, no DLL injection, no Lua mods, no background processes, no per-frame overhead.
- **Legacy Modes Still Available:** Pillarboxed 16:9 cutscenes (previous "clean" mode) and full Vert- ultrawide (previous 11-offset mode) remain selectable.

## Fix Modes

| Mode | Cutscenes | Exploration | Photo mode | Loading |
| :--- | :--- | :--- | :--- | :--- |
| **Cine Hor+ (Recommended)** | Hor+ ultrawide, 0% vertical crop | Classic full-width | Classic (photos correct) | Classic (covered) |
| **True Hor+ Everywhere** (experimental) | Hor+ ultrawide | Hor+ ultrawide | Unconstrained (photos may skew) | Sides visible |
| **Legacy Clean** | 16:9 pillarbox | Classic full-width | Classic | Classic |
| **Legacy Full** | Vert- wide (~20% crop) | Classic full-width | Classic | Wide |

## How It Works (Short Version)

UE5 already contains perfect Hor+ math: in the `MaintainYFOV` projection path it derives the vertical FOV from the aspect ratio the camera was *authored* for (`vFOV = 2*atan(tan(hFOV/2) / authoredAspect)`) and then expands horizontally to the real viewport. Two patched bytes force that branch for every unconstrained camera (behavior-neutral for constrained/pillarboxed views and on 16:9 displays).

The key insight enabling the recommended mode: with the classic exploration patch active, the camera views themselves carry a perfect discriminator - cutscene cameras arrive with their authored 16:9 aspect ratio (~1.778), while exploration and photo cameras carry the patched monitor aspect and photo-capture cameras are square. A small code cave in `UCameraComponent::GetCameraView` unconstrains only views authored in the (1.75, 1.8) window (-> Hor+ cutscenes), and a second cave on the unique `UCineCameraComponent` super-call forces the game's cine cameras - which are its loading/transition views - back to constrained 16:9 pillarbox.

See [RESEARCH.md](RESEARCH.md) for the full breakdown.

---

## Supported Resolutions

Any resolution is supported. The aspect value below is only used for the photo projection table (and by the legacy modes):

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
3. Choose your **Framing Mode** (True Hor+ Ultrawide is recommended).
4. Click **Patch Game Executable**.
5. Launch the game through Steam.

*(An automatic `.original` backup is created before patching. You can click **Restore Original Stock** at any time).*

### Method 2: Python Script (Steam Deck / Linux / Proton / macOS / Windows)
1. Open a terminal in the folder and run:
   ```bash
   python patcher.py
   ```
2. Select your resolution preset and framing mode.

Non-interactive usage:
   ```bash
   python patcher.py --mode cine --width 5120 --height 2160
   python patcher.py --mode stock
   ```

**Requirements:** Python 3.6+ using built-in standard libraries only. Zero external dependencies (`pip`) required.

### Method 3: In-Memory Patching via SUWSF (No File Modifications)
If you prefer not to modify your `.exe` file on disk:
1. Download [SUWSF](https://github.com/PhantomGamers/SUWSF/releases) and place `SUWSF.asi` and `dxgi.dll` in `Chronos/Binaries/Win64/`.
2. Copy the included **`SUWSF.ini`** (True Hor+ configuration) into `Chronos/Binaries/Win64/`.
3. Launch the game normally. SUWSF will automatically detect your display resolution and apply the patches in memory at startup.

Do not combine Method 3 with a patched executable; the patchers automatically disable `SUWSF.ini` when they patch the exe.

### Method 4: Manual Hex Editing (HxD)

Open `Chronos/Binaries/Win64/Chronos-Win64-Shipping.exe` in [HxD](https://mh-nexus.de/en/hxd/) (or any hex editor).

**Cine Hor+ Mode (recommended)** - use `patcher.py` or the GUI for this mode. It applies the static edits below **plus two small code caves** (33 + 18 bytes written into `int3` padding) whose relative addresses are computed at patch time, so it is impractical to apply by hand:

| File Offset | Original Bytes | New Bytes | Purpose |
| :--- | :--- | :--- | :--- |
| `0x23E665C` | `3B 8E E3 3F` | *your aspect hex* | Player exploration camera (classic) |
| `0x69C8A8C` | `39 8E E3 3F` | *your aspect hex* | Photo projection table (classic) |
| `0x440ABC6` | `02` | `FF` | Disable `MajorAxisFOV` Vert- branch |
| `0x440ABCF` | `01` | `FF` | Disable `MaintainXFOV` Vert- branch (forces Hor+ `MaintainYFOV`) |
| `0x441A14C` + cave A | `0F B6 83 B4 02 00 00` | `call caveA` + nop | Unconstrain only cameras authored 16:9 (cutscenes -> Hor+) |
| `0x4005B87` + cave B | (dynamic) | (dynamic) | Force cine (loading/transition) views back to 16:9 pillarbox |

**True Hor+ Everywhere Mode (experimental)** - 4 edits, starting from a stock executable:

| File Offset | Original Bytes | New Bytes | Purpose |
| :--- | :--- | :--- | :--- |
| `0x441A14C` | `0F B6 83 B4 02 00 00` | `31 C0 0F 1F 44 00 00` | Unconstrain all cameras (`bConstrainAspectRatio=false`) |
| `0x440ABC6` | `02` | `FF` | Disable `MajorAxisFOV` Vert- branch |
| `0x440ABCF` | `01` | `FF` | Disable `MaintainXFOV` Vert- branch (forces Hor+ `MaintainYFOV`) |
| `0x69C8A8C` | `39 8E E3 3F` | *your aspect hex* | Photo projection table |

Important: in Hor+ mode, offset `0x23E665C` must remain at its stock value `3B 8E E3 3F`. The engine divides by this authored aspect ratio to compute the preserved vertical FOV; writing your monitor ratio here re-introduces vertical cropping.

**Legacy Clean Mode** - 2 edits:
   - `0x23E665C` (Player Exploration Camera) -> Replace `3B 8E E3 3F` with your resolution hex (e.g. `26 B4 17 40`).
   - `0x69C8A8C` (Photo Projection Table) -> Replace `39 8E E3 3F` with your resolution hex (e.g. `26 B4 17 40`).

---

## Optional Engine.ini Tweaks

Not required for the fix, but recommended at ultrawide resolutions. Add to `%localappdata%\Chronos\Saved\Config\Windows\Engine.ini`:

```ini
[SystemSettings]
; Reduce strong chromatic aberration at the expanded screen edges
r.SceneColorFringeQuality=0
```

---

## Troubleshooting

- **Photos look skewed:** use Cine Hor+ (or a legacy) mode - the photo pipeline there is identical to the proven classic fix. (In the experimental "everywhere" mode the photo camera runs unconstrained; that is a known limitation.)
- **A cutscene still shows black bars:** a few cinematics may draw letterbox bars as UI widgets rather than camera constraints, or use a non-cinematic camera class; report which scene, these can be addressed separately.
- **Vertical framing steps down when a cutscene ends:** in Cine Hor+ mode cutscenes show the full 16:9 vertical image while exploration uses the classic full-width (vertically tighter) framing, so a hard cut between them shifts vertical FOV. Inherent to combining Hor+ cinematics with the classic exploration camera.
- **Everything looks wrong after a game update:** the patchers locate all code sites by unique byte signatures and will report if the game version is unsupported; re-run the patcher after updates.

---

## Technical Documentation & Research

For the full reverse-engineering breakdown, Unreal Engine 5 projection matrix analysis, the Hor+ discovery, the cutscene zoom-bug root cause, complete disassembly maps, and dead-end analyses, see **[RESEARCH.md](RESEARCH.md)**.

---

## License

This project is licensed under the **GNU General Public License v3.0 or later** - see [LICENSE](LICENSE). You are free to use, study, modify and redistribute it; any distributed modification must ship its complete source under the same terms.

As an additional term under GPL-3.0 section 7(b), every copy or modified version must preserve the copyright notice and credit the original author, Kiri11, with a link to this project.
