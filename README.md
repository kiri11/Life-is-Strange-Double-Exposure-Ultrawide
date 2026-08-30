# Life is Strange: Double Exposure - Ultrawide & Cutscene Fix

A lightweight, native ultrawide fix for **Life is Strange: Double Exposure** supporting 21:9, 32:9, 32:10, 16:10 and custom resolutions.

---

## What This Fix Does

- **True Hor+ ultrawide everywhere.** Cutscenes, dialogues and free-roam exploration all render with **0% vertical crop**: the complete 16:9 vertical framing (faces, headroom, director composition) is preserved and the horizontal field of view is *expanded* to fill your monitor. No black bars, no cut chins or foreheads.
- **No camera zoom or snap when a dialogue or cutscene ends.** Both the sequencer's leaked aspect-axis constraint and the game's own letterbox-open animation are handled, so control returns to the player without a framing jump.
- **Correct photo mechanics.** Max's Polaroids and the in-game camera keep proper proportions without stretching or skewing, and the photo pipeline is left bit-identical to vanilla.
- **Full-width UI (optional).** Loading screens cover the whole screen instead of leaving the still-streaming world visible at the sides, and HUD elements such as phone notifications sit on the physical screen edge rather than an invisible 16:9 boundary.
- **Display tweaks (optional).** Disable chromatic aberration, and apply recommended anti-blur TSR settings for your resolution.
- **Fully static.** A handful of patched bytes in the executable plus an in-place edit of the game's data. No runtime hooks, no DLL injection, no Lua mods, no background processes, no performance impact. Behaviour-neutral on a 16:9 display.

Everything is reversible with a single **Restore** action.

## How It Works (Short Version)

UE5 already contains perfect Hor+ math: in the `MaintainYFOV` projection path it derives the vertical FOV from the aspect ratio the camera was *authored* for (`vFOV = 2*atan(tan(hFOV/2) / authoredAspect)`) and then expands horizontally to the real viewport. The fix is three code changes and no aspect-ratio constants at all:

1. **Force that Hor+ projection branch** for every perspective camera (2 bytes). This also neutralises the sequencer bug that leaks `MaintainXFOV` after a cutscene.
2. **Cave A** in `UCameraComponent::GetCameraView`: unconstrain every camera authored *narrower* than your display, and **pin the FOV divisor to the authored 16:9**. The pin matters because the game *animates* a camera's `AspectRatio` up to the viewport aspect when it hands control back from a dialogue - its letterbox-open animation. Without the pin, the forced Hor+ branch reads that animation as a vertical-FOV change, which is exactly the zoom-and-snap this fix removes.
3. **Cave B** on the unique `UCineCameraComponent` super-call, keeping the game's cine-class views (loading and transition holds) constrained.

See [RESEARCH.md](RESEARCH.md) for the complete technical breakdown, including the runtime measurements that identified the letterbox ramp.

---

## Supported Resolutions

Any resolution is supported; the installer detects yours automatically. Presets:

| Resolution | Aspect Ratio |
| :--- | :--- |
| **5120x2160** (WUHD 4K) | 21.33:9 |
| **3440x1440** (UWQHD) | 21.5:9 |
| **2560x1080** (UWD) | 21.33:9 |
| **3840x1600** (UW) | 24:10 |
| **5120x1440** / **3840x1080** / **7680x2160** | 32:9 |
| **3840x1200** (32:10) | 32:10 |
| **2560x1600** (16:10) | 16:10 |
| **Custom** | *Any width x height* |

---

## Installation

Both methods run the same code - the GUI is a thin front-end over `patcher.py`, so there is only one implementation to keep correct.

### Method 1: Windows GUI

1. Run **`LiSUltrawidePatcher.exe`** (keep it next to `patcher.py`).
2. It detects your game and display automatically.
3. Leave all four options ticked and click **Install**.

**You do not need Python installed.** The GUI looks for `uv`, then `py`, then `python`. If none is present - or if the full-width UI option is ticked and `uv` is missing, since that step needs the `blake3` package - it offers to run uv's official installer for you:

```
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

It always asks first and shows the exact command. The installer is per-user, needs no administrator rights, and `uv` then fetches a Python interpreter and `blake3` by itself - so accepting once makes everything work on a machine with no Python at all. Declining just falls back to whatever Python you already have.

### Method 2: Command line (Windows / Steam Deck / Linux / Proton / macOS)

```bash
uv run patcher.py
```

`uv` ([astral.sh/uv](https://astral.sh/uv)) is the easiest option: no virtualenv, and it fetches the one optional dependency automatically. Plain Python works too:

```bash
python patcher.py
```

Run with no arguments for an interactive install, or drive it directly:

```bash
python patcher.py --yes
python patcher.py --width 5120 --height 2160 --yes
python patcher.py --restore
```

**Requirements:** Python 3.6+, or nothing at all if you let `uv` supply it. The full-width UI step also needs `blake3` (automatic under `uv`, otherwise `pip install blake3`) and an Oodle decompressor, which the installer obtains for you - see below. If either is unavailable, that one step is skipped and reported; everything else still installs.

### About the Oodle decompressor

The game's data files are 97% Oodle-compressed, and Oodle ships *statically linked* inside the game executable, so reading a package needs a standalone decompressor. It cannot be bundled here - it is proprietary, and redistributing it is governed by the Unreal Engine EULA.

The installer resolves this itself, in order:

1. a copy already in `tools/assetdump/`;
2. one shipped by **another Unreal Engine game on your PC** - most UE titles carry `oo2core_*_win64.dll`, which exports the same entry point, so nothing is downloaded;
3. otherwise Epic's Oodle-for-UE build (~7 MB), downloaded on request from [WorkingRobot/OodleUE](https://github.com/WorkingRobot/OodleUE).

Only step 3 touches the network, and only after you agree - the GUI asks, and the command line asks unless you pass `--fetch-oodle` (or `--yes`, which declines rather than downloading silently). Decline and just that one step is skipped.

### What each option does

| Option | Effect | Touches |
| :--- | :--- | :--- |
| **Ultrawide camera** | Hor+ cutscenes, dialogue and exploration; no bars, no zoom on hand-off | `Chronos-Win64-Shipping.exe` |
| **Full-width UI** | Loading screens cover the screen; HUD on the real edge | `pakchunk0-Windows.utoc` / `.ucas` |
| **Disable chromatic aberration** | Removes colour fringing, most visible at the widened edges | `Engine.ini` |
| **Reduce blurriness** | Recommended TSR settings for your resolution | `Engine.ini` |

All four are enabled by default and each can be turned off (`--no-exe`, `--no-game-files`, `--no-chromatic-fix`, `--no-sharpen`).

Every part creates a backup before writing: `.exe.original`, `.utoc.original`, and a clearly marked, individually removable block in `Engine.ini`. Re-running never stacks changes - each run starts from the pristine state.

> Do not run Steam's **Verify Integrity of Game Files** while the full-width UI patch is applied - it would re-download the ~20 GB `pakchunk0`. A game update also overwrites it; just re-run the installer afterwards.

---

## Display Tweaks in Detail

The last two options write one managed block into `%localappdata%\Chronos\Saved\Config\Windows\Engine.ini`. Your existing settings are left untouched, and `--restore` removes the block byte-for-byte.

TSR - UE5's temporal upscaler - is what makes this game look soft. The two settings that matter most are rendering at 100% of the output resolution instead of upscaling from a lower one, and giving TSR a history buffer above output resolution to resolve detail from. The history multiplier is the expensive one, so it is scaled back at very high pixel counts (200 below ~8 MP, 150 above). These are a sane starting point rather than gospel - every line is an ordinary UE console variable, commented in the file, and safe to edit afterwards.

Optional extra, not written by the installer - faster streaming reduces texture pop-in at the screen sides during loading transitions:

```ini
[SystemSettings]
r.Streaming.PoolSize=4096
r.Streaming.FramesForFullUpdate=1
```

---

## Possible Improvements

- **Player-menu tabs:** three full-stretch tabs (`JournalTabUI`, `SMSTabUI`, `CollectiblesTabUI`) are not repositioned by the UI patch. Giving them the centred box their three siblings already have needs a *structural* package edit (adding serialized properties changes the package size), which the in-place float patcher deliberately does not support.
- **Per-shot aspect variants:** if a specific cinematic ever appears pillarboxed, its camera is authored at an aspect outside cave A's gate window; the gate can be extended per report.

---

## Troubleshooting

- **A cutscene shows black bars:** report which scene - its camera is authored at an unusual aspect ratio and the gate can be widened for it.
- **The full-width UI step was skipped:** it needs `blake3` and an Oodle DLL (see Installation). Use `uv run patcher.py` to get `blake3` automatically.
- **Everything looks wrong after a game update:** all code sites are located by unique byte signatures and the patcher reports cleanly if the game version is unsupported. Re-run the installer after updates.
- **Restore stock:** the GUI's **Restore original** button, or `python patcher.py --restore`.

---

## Technical Documentation & Research

For the reverse-engineering breakdown, Unreal Engine 5 projection-matrix analysis, disassembly of the patched sites, the asset-container tooling, the runtime camera measurements, and the dead ends explored, see **[RESEARCH.md](RESEARCH.md)**. Historical iterations of this fix are preserved in git history.

---

## License

This project is licensed under the **GNU General Public License v3.0 or later** - see [LICENSE](LICENSE). You are free to use, study, modify and redistribute it; any distributed modification must ship its complete source under the same terms.

As an additional term under GPL-3.0 section 7(b), every copy or modified version must preserve the copyright notice and credit the original author, Kiri11, with a link to this project.
