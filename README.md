# Life is Strange: Double Exposure - Ultrawide & Cutscene Fix

A lightweight, native ultrawide fix for **Life is Strange: Double Exposure** supporting 21:9, 32:9, 32:10, 16:10 and custom resolutions. Any resolution works - the installer detects yours automatically.

---

## Installation

Both methods run the same code - the GUI is a thin front-end over `patcher.py`, so there is only one implementation to keep correct.

### Method 1: Windows GUI

1. **[Download the fix](https://github.com/kiri11/Life-is-Strange-Double-Exposure-Ultrawide/archive/refs/heads/main.zip)** and unpack the zip anywhere.
2. Run **`LiSUltrawidePatcher.exe`** (keep it next to `patcher.py`).
3. It finds your game installation and your display automatically - but if it does not find the game, use the **Browse...** button to select the game executable.
4. Check the badge under the path: a green tick means a stock executable this build knows, and an orange warning means it is already patched or is not a version the signatures match (hover it for the details).
5. Leave all four options ticked and click **Install**.

### Method 2: Command line (Windows / Steam Deck / Linux / Proton / macOS)

#### Install uv, if you do not have it

`uv` ([astral.sh/uv](https://astral.sh/uv)) is the easiest way to run the installer: no virtualenv, and it fetches both a Python interpreter and the one optional dependency by itself. It installs for your user account only - no administrator rights and no system-wide changes.

Windows:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

macOS, Linux and Steam Deck:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

#### Run the installer

```bash
uv run patcher.py
```

Plain Python works too, if you already have 3.6+:

```bash
python patcher.py
```

Run with no arguments for an interactive install, or drive it directly:

```bash
python patcher.py --yes
python patcher.py --width 5120 --height 2160 --yes
python patcher.py --restore
```

The game is located automatically ([how](RESEARCH.md#8a-where-the-installer-looks-for-the-game)), so the installer can live anywhere. Pass `--exe "...\Chronos-Win64-Shipping.exe"` to point it at a specific copy, `--find-exe` to print what it found and exit, or `--check-exe` to report whether that executable is stock, already patched, or a version the signatures no longer match.

**Requirements:** Python 3.6+, or nothing at all if you let `uv` supply it. The full-width UI step also needs `blake3` (automatic under `uv`, otherwise `pip install blake3`) and an Oodle decompressor, which the installer fetches by itself the first time it needs one - [why, and from where](RESEARCH.md#9e-obtaining-the-oodle-decompressor). Pass `--no-fetch-oodle` to forbid that download. If either is unavailable, that one step is skipped and reported; everything else still installs.

### Checking what you have

Before anything is written, the installer classifies the executable using the same byte signatures it patches through - each patch site is recognisable in both its stock and its patched form:

| Verdict | Meaning |
| :--- | :--- |
| **Original** | Stock, and a build these signatures match - safe to install. |
| **Already patched** | The fix is in. Installing again is still safe: every run restarts from the `.exe.original` backup, so nothing stacks. The read-out names the parts it found, including the gate bound the exe was patched for. |
| **Unrecognised** | The signature sites are not there - a game update, or not the game's executable. |

The GUI shows this as a coloured line under the path (green tick / orange warning, full detail on hover) and refreshes it after every install or restore; on the command line it is one `Executable:` line, or `--check-exe` on its own. Because the check reads the same signature table the patcher writes with, the badge cannot disagree with what **Install** would do.

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
2. **[Cave](https://en.wikipedia.org/wiki/Code_cave) A** in `UCameraComponent::GetCameraView`: unconstrain every camera authored *narrower* than your display, and **pin the FOV divisor to the authored 16:9**. The pin matters because the game *animates* a camera's `AspectRatio` up to the viewport aspect when it hands control back from a dialogue - its letterbox-open animation. Without the pin, the forced Hor+ branch reads that animation as a vertical-FOV change, which is exactly the zoom-and-snap this fix removes.
3. **Cave B** on the unique `UCineCameraComponent` super-call, keeping the game's cine-class views (loading and transition holds) constrained.

See [RESEARCH.md](RESEARCH.md) for the complete technical breakdown, including the runtime measurements that identified the letterbox ramp.

---

## Display Tweaks in Detail

The last two options write one managed block into `%localappdata%\Chronos\Saved\Config\Windows\Engine.ini`. Your existing settings are left untouched, and `--restore` removes the block byte-for-byte.

TSR - UE5's temporal upscaler - is what makes this game look soft. The two settings that matter most are rendering at 100% of the output resolution instead of upscaling from a lower one, and giving TSR a history buffer above output resolution to resolve detail from. The history multiplier is the expensive one, so it is scaled back at very high pixel counts (200 below ~8 MP, 150 above). These are a sane starting point rather than gospel - every line is an ordinary UE console variable, commented in the file, and safe to edit afterwards.

---

## Troubleshooting

- **A cutscene shows black bars:** report which scene - its camera is authored at an unusual aspect ratio and the gate can be widened for it.
- **The full-width UI step was skipped:** it needs `blake3` and an Oodle DLL. The Oodle one is downloaded automatically unless you passed `--no-fetch-oodle`, so this usually means `blake3` is missing - `uv run patcher.py` supplies it.
- **Everything looks wrong after a game update:** all code sites are located by unique byte signatures and the patcher reports cleanly if the game version is unsupported. Re-run the installer after updates.
- **The installer cannot find the game:** point it at the executable yourself - **Browse** in the GUI, or `python patcher.py --exe "D:\...\Chronos\Binaries\Win64\Chronos-Win64-Shipping.exe"`. `python patcher.py --find-exe` shows what the search does find.
- **Restore stock:** the GUI's **Restore original** button, or `python patcher.py --restore`.

### Still broken? Open an issue

[Open an issue](https://github.com/kiri11/Life-is-Strange-Double-Exposure-Ultrawide/issues) with as much detail as you can. What helps most:

- **What you did** - GUI or command line, which of the four options were ticked, and whether the game had been patched before.
- **What is wrong and where** - the scene, menu or moment it happens in, what you see versus what you expected, and a screenshot or clip if you can take one.
- **Your setup** - display resolution, and whether the game is the Steam, Epic or another copy.
- **What the installer said** - the whole GUI log box or the console output, plus the output of `python patcher.py --check-exe`, which reports the path it found and whether that executable is stock, patched or unrecognised.

---

## Technical Documentation & Research

For the reverse-engineering breakdown, Unreal Engine 5 projection-matrix analysis, disassembly of the patched sites, the asset-container tooling, the runtime camera measurements, the open work still on the table, and the dead ends explored, see **[RESEARCH.md](RESEARCH.md)**. Historical iterations of this fix are preserved in git history.

---

## License

This project is licensed under the **GNU General Public License v3.0 or later** - see [LICENSE](LICENSE). You are free to use, study, modify and redistribute it; any distributed modification must ship its complete source under the same terms.

As an additional term under GPL-3.0 section 7(b), every copy or modified version must preserve the copyright notice and credit the original author, Kiri11, with a link to this project.
