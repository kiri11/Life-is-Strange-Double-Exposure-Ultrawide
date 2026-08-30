# Life is Strange: Double Exposure - Ultrawide & Cutscene Fix

A lightweight, native ultrawide fix for **Life is Strange: Double Exposure** supporting 21:9, 32:9, 32:10, and custom resolutions.

---

## What This Fix Does

- **True Hor+ Ultrawide Cutscenes & Dialogues:** Cinematics render full-width with **0% vertical crop**. The full 16:9 vertical framing (faces, headroom, director composition) is preserved and the horizontal field of view is *expanded* to fill your monitor. No black bars, no cut chins or foreheads.
- **Ultrawide Free-Roam Exploration:** Full-width rendering during gameplay.
- **Correct Photo Mechanics:** Max's Polaroid photographs and in-game camera captures retain proper 1:1 proportions without stretching or skewing. The camera UI works unchanged.
- **No Zoom Bug After Cutscenes:** The engine-level cause of the camera zoom when a cutscene hands control back to the player (Unreal's sequencer can leave the aspect-axis constraint on `MaintainXFOV`) is neutralized at the projection branch.
- **Fully Static:** A handful of patched bytes in the executable. No runtime hooks, no DLL injection, no Lua mods, no background processes, no performance impact. Behavior-neutral on a 16:9 display.
- **Full-Width UI (optional second patch):** the loading overlay covers the whole screen instead of leaving the still-streaming world visible at the sides, and HUD elements such as phone notifications sit on the physical screen edge instead of an invisible 16:9 boundary. See "Ultrawide UI Layout" below.

**Known limitation:** returning from a *dialogue* to exploration still shows a brief camera zoom. The equivalent bug after cutscenes is fixed; the dialogue path is not yet covered - see "Possible Improvements".

## How It Works (Short Version)

UE5 already contains perfect Hor+ math: in the `MaintainYFOV` projection path it derives the vertical FOV from the aspect ratio the camera was *authored* for (`vFOV = 2*atan(tan(hFOV/2) / authoredAspect)`) and then expands horizontally to the real viewport. The fix:

1. Forces that Hor+ projection branch for every unconstrained camera (2 bytes).
2. Adds a small code cave in `UCameraComponent::GetCameraView` that lifts the 16:9 pillarbox constraint from cameras authored ~16:9 - the cutscene and dialogue cameras - while cameras carrying other aspect ratios (exploration at the patched monitor ratio, square photo-capture cameras) keep their classic behavior.
3. Adds a second cave on the unique `UCineCameraComponent` super-call that keeps the game's cine-class views (loading/transition holds) constrained.
4. Patches the classic player-camera and photo-table aspect constants to your monitor's ratio.

See [RESEARCH.md](RESEARCH.md) for the complete technical breakdown.

---

## Supported Resolutions

Any resolution is supported; presets:

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

## Installation

Choose either method. Both create an automatic `.original` backup before patching and can restore it at any time.

### Method 1: Windows GUI Patcher
1. Run **`LiSUltrawidePatcher.exe`**.
2. The tool auto-detects your game executable and display resolution.
3. Keep the recommended mode selected and click **Patch Game Executable**.
4. Launch the game through Steam.

### Method 2: Python Script (Steam Deck / Linux / Proton / macOS / Windows)
```bash
python patcher.py --mode cine --width 5120 --height 2160
```
Or run `python patcher.py` without arguments for interactive prompts. Restore with:
```bash
python patcher.py --mode stock
```

**Requirements:** Python 3.6+, standard library only.

Manual hex editing is not practical for this fix: besides four static byte edits, it writes two code caves into `int3` padding whose relative addresses are computed at patch time. In-memory patchers (SUWSF and similar) cannot express the caves either - use the bundled patchers.

---

## Optional Engine.ini Tweaks

Not required, but recommended at ultrawide resolutions. Add to `%localappdata%\Chronos\Saved\Config\Windows\Engine.ini`:

```ini
[SystemSettings]
; Reduce strong chromatic aberration at the expanded screen edges
r.SceneColorFringeQuality=0
; Optional: faster texture/level streaming reduces visible pop-in at the
; screen sides during loading transitions
r.Streaming.PoolSize=4096
r.Streaming.FramesForFullUpdate=1
```

---

## Ultrawide UI Layout (Optional Second Patch)

The executable patch fixes the *camera*. The game's UI is a separate problem with a separate cause: `BP_UIWindowManager`'s `WindowParent` - the panel every game window is reparented into - is a fixed **3840x2160 box centred in the viewport**. On a 5120x2160 display that leaves 640 px of dead space on each side, which is why the loading overlay did not cover the screen and why notifications stopped short of the edge. Full analysis in [RESEARCH.md](RESEARCH.md) section 9.

This cannot be an executable byte patch - the value lives in cooked asset data - so it is applied to the IoStore container instead:

```bash
python tools/assetdump/patch_ui_layout.py --width 5120 --height 2160
python tools/assetdump/patch_ui_layout.py --restore
```

**Requirements:** Python 3.6+, `pip install blake3`, and an Oodle decompressor DLL (see [tools/assetdump/README.md](tools/assetdump/README.md)).

The patch rewrites 15 existing floats across 10 widget packages: one widens `WindowParent` to the real UMG design space (`viewport / DPIScale`, where the DPI scale is UE's `ScaleToFit` rule `min(W/3840, H/2160)`), and the rest re-inset the handful of elements an automated audit found positioned by absolute coordinates on the 3840 canvas - the pause title, several fixed-width backgrounds, and the main-menu/title-screen compositions, which are deliberately kept in their authored 16:9 framing rather than dragged to the physical screen edges.

It is **append-only**: modified package chunks are written as new uncompressed blocks at the end of the `.ucas`, the TOC block entries are repointed, and each chunk's BLAKE3 meta hash is recomputed. Existing bytes are never overwritten and `.utoc` is backed up, so `--restore` is exact.

> Do not run Steam's **Verify Integrity of Game Files** while this patch is applied - it would re-download the ~20 GB `pakchunk0`. A game update will also overwrite it; re-run the patcher afterwards.

---

## Possible Improvements

- **Dialogue-exit camera zoom:** returning from a dialogue to exploration briefly zooms in. The cutscene equivalent is fixed at the projection branch (see "How It Works"), but the dialogue path is not. Leading hypothesis is the view *blend* rather than a leaked axis constraint: `FMinimalViewInfo::BlendViewInfo` propagates `bConstrainAspectRatio` with `|=`, so a blend out of an unconstrained ~1.778 dialogue camera toward the constrained monitor-ratio exploration camera runs the Hor+ path with a mid-blend aspect, and `vFOV = atan(tan(hFOV/2)/aspect)` narrows as that aspect climbs. Unverified - it needs a per-frame recording of `(AspectRatio, bConstrainAspectRatio)` across a dialogue exit.
- **Per-shot aspect variants:** if a specific cinematic ever appears pillarboxed, its camera is authored at an aspect outside the (1.75, 1.8) gate window; the gate can be extended per report.

---

## Troubleshooting

- **A cutscene shows black bars:** report which scene - its camera is authored at an unusual aspect ratio and the gate can be widened for it.
- **Everything looks wrong after a game update:** the patchers locate all code sites by unique byte signatures and will report if the game version is unsupported; re-run the patcher after updates.
- **Restore stock:** GUI "Restore Original Stock" button, or `python patcher.py --mode stock`.

---

## Technical Documentation & Research

For the reverse-engineering breakdown, Unreal Engine 5 projection matrix analysis, disassembly of the patched sites, the loading-transition analysis, and dead ends explored, see **[RESEARCH.md](RESEARCH.md)**. Historical iterations of this fix are preserved in git history.

---

## License

This project is licensed under the **GNU General Public License v3.0 or later** - see [LICENSE](LICENSE). You are free to use, study, modify and redistribute it; any distributed modification must ship its complete source under the same terms.

As an additional term under GPL-3.0 section 7(b), every copy or modified version must preserve the copyright notice and credit the original author, Kiri11, with a link to this project.
