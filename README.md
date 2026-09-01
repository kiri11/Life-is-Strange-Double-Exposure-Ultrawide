# Life is Strange: Double Exposure - Ultrawide & Cutscene Fix

A lightweight, native ultrawide fix for **Life is Strange: Double Exposure** supporting 21:9, 32:9, 32:10, 16:10 and custom resolutions. Any resolution works - the installer detects yours automatically.

---

## Installation

Both methods run the same code - the GUI is a thin front-end over `patcher.py`, so there is only one implementation to keep correct.

### Method 1: Windows GUI

1. **[Download the fix](https://github.com/kiri11/Life-is-Strange-Double-Exposure-Ultrawide/releases/latest/download/LiS-Ultrawide-Fix.zip)** and unpack the zip anywhere.
2. Run **`LiSUltrawidePatcher.exe`** (keep it next to `patcher.py`).
3. It finds your game installation and your display automatically - but if it does not find the game, use the **Browse...** button to select the game executable.
4. Check the badge under the path: a green tick means a stock executable this build knows, and an orange warning means it is already patched or is not a version the signatures match (hover it for the details).
5. Leave all four options ticked and click **Install**.

**Windows will warn you the first time.** The program is not code-signed - a signing certificate is a paid, identity-verified subscription, and this is a free fix - so SmartScreen shows *"Windows protected your PC"*. Click **More info**, then **Run anyway**. Some antivirus tools flag it as well, for the same reason and because of what it does: it rewrites bytes in a game executable and downloads a decompressor, which is what heuristics are built to notice.

None of that is something you have to take on trust. The exe in each release is compiled by GitHub Actions from `LiSUltrawidePatcher.cs` in this repository - the release notes name the commit it was built from and link the build that produced it - and it contains no patch logic of its own: it runs `patcher.py`, which is plain, readable Python sitting next to it. You can also skip the exe entirely and use [Method 2](#method-2-command-line), which does exactly the same work, or compile the exe yourself with the single command in the source file's header.

**Python is not required.** The patch logic lives in `patcher.py`, so the program needs an interpreter to run it - it uses `uv` or a Python you already have, and on a machine with neither it downloads python.org's embeddable build (~11 MB) into its own `tools/python/` folder - or into `%LOCALAPPDATA%\LiSUltrawideFix` if the fix was unpacked somewhere read-only. Nothing is installed system-wide, no `PATH` is touched, and deleting the fix deletes it again.

### Method 2: Command line

> **Tested on Windows only.** The installer is written to work on SteamOS and the Steam Deck, on Linux under Proton, and on macOS: the game search knows those install layouts, and `Engine.ini` is looked up inside a Proton prefix. None of that has been run on real hardware, though. If you are on one of those systems and something does not work, please [open an issue](https://github.com/kiri11/Life-is-Strange-Double-Exposure-Ultrawide/issues) - or send a pull request, it is a small installer and the platform-specific parts are all in `patcher.py`.

#### Optional: install uv

Any Python 3.6+ runs the installer with no dependencies at all, so this is optional. `uv` ([astral.sh/uv](https://astral.sh/uv)) is still the smoothest route if you would rather not think about interpreters: it supplies its own Python and the compiled `blake3`, needs no virtualenv, and installs for your user account only - no administrator rights, no system-wide changes.

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
python patcher.py
```

or, under uv:

```bash
uv run patcher.py
```

Run with no arguments for an interactive install, or drive it directly:

```bash
python patcher.py --yes
python patcher.py --width 5120 --height 2160 --yes
python patcher.py --restore
```

The game is located automatically ([how](RESEARCH.md#8a-where-the-installer-looks-for-the-game)), so the installer can live anywhere. Pass `--exe "...\Chronos-Win64-Shipping.exe"` to point it at a specific copy, `--find-exe` to print what it found and exit, or `--check-exe` to report whether that executable is stock, already patched, or a version the signatures no longer match.

**Requirements:** Python 3.6+ and nothing else - the standard library covers everything, and nothing is downloaded. The full-width UI step reads the game's Oodle-compressed packages with a pure-Python Kraken decoder that ships in `tools/assetdump/` ([how](RESEARCH.md#9e-reading-oodle-compressed-packages)), and it hashes with the compiled `blake3` module when that happens to be installed, falling back to a bundled pure-Python BLAKE3 otherwise.

### Checking what you have

Before anything is written, the installer classifies the executable using the same byte signatures it patches through - each patch site is recognisable in both its stock and its patched form:

| Verdict | Meaning |
| :--- | :--- |
| **Original** | Stock, and a build these signatures match - safe to install. |
| **Already patched** | The fix is in. Installing again is still safe: every run restarts from the `.exe.original` backup, so nothing stacks. The read-out names the parts it found, including the gate bound the exe was patched for. |
| **Unrecognised** | The signature sites are not there - a game update, or not the game's executable. |

The GUI shows this as a coloured line under the path (green tick / orange warning, full detail on hover) and refreshes it after every install or restore; on the command line it is one `Executable:` line, or `--check-exe` on its own. Because the check reads the same signature table the patcher writes with, the badge cannot disagree with what **Install** would do.

A second line reports the full-width UI. Its container is built from ten of the game's own UI packages, so it belongs to the build it was made on, and the installer records a fingerprint of the game's data next to it:

| Verdict | Meaning |
| :--- | :--- |
| **Installed for 5120x2160** | Current - built from the game as it is on disk. |
| **Built for a different build of the game** | The game has been updated since. Install again *before playing*: the container still shadows ten packages with copies cooked for a build that is gone. |
| **Needs installing again** | Files are missing from the set, or nothing records what they were built from. |
| **Not installed** | The step was never run, or was restored. |

The check reads a megabyte of the game's data and nothing else - no decoding, no container parsing.

### What each option does

| Option | Effect | Touches |
| :--- | :--- | :--- |
| **Ultrawide camera** | Hor+ cutscenes, dialogue and exploration; no bars, no zoom on hand-off | `Chronos-Win64-Shipping.exe` |
| **Full-width UI** | Loading screens cover the screen; HUD on the real edge | adds `Content/Paks/Mods/LiSUltrawideUI_P.*` |
| **Disable chromatic aberration** | Removes colour fringing, most visible at the widened edges | `Engine.ini` |
| **Reduce blurriness** | Recommended TSR settings for your resolution | `Engine.ini` |

The first three are enabled by default and each can be turned off (`--no-exe`, `--no-game-files`, `--no-chromatic-fix`); **Reduce blurriness** is opt-in (`--sharpen`).

The parts that write to existing files back them up first: `.exe.original`, and a clearly marked, individually removable block in `Engine.ini`. The full-width UI needs no backup at all - it is delivered as its own small mod container (~120 KB) alongside the game's data, and the game's own files are only ever read. Re-running never stacks changes - each run starts from the pristine state.

A backup belongs to the version of the game it was taken from, and the installer checks that before it trusts one. When a game update replaces the files underneath it, the old backup is set aside (renamed `.old`, never deleted) and a fresh one taken from the updated game - so re-running after an update installs onto the new version instead of writing the previous one back over it. If a backup is missing and the game is already patched, the installer stops and says so rather than guessing.

> After a game update, re-run the installer. The mod container holds copies of ten UI packages taken from the build it was made on, and an update can change them; re-running rebuilds it from the updated game. Steam's **Verify Integrity of Game Files** is safe to run - it has nothing of ours to repair, since no shipped file is modified.

---

## What This Fix Does

- **True Hor+ ultrawide everywhere.** Cutscenes, dialogues and free-roam exploration all render with **0% vertical crop**: the complete 16:9 vertical framing (faces, headroom, director composition) is preserved and the horizontal field of view is *expanded* to fill your monitor. No black bars, no cut chins or foreheads.
- **No camera zoom or snap when a dialogue or cutscene ends.** Both the sequencer's leaked aspect-axis constraint and the game's own letterbox-open animation are handled, so control returns to the player without a framing jump.
- **Correct photo mechanics.** Max's Polaroids and the in-game camera keep proper proportions without stretching or skewing, and the photo pipeline is left bit-identical to vanilla.
- **Full-width UI (optional).** Loading screens cover the whole screen instead of leaving the still-streaming world visible at the sides, and HUD elements such as phone notifications sit on the physical screen edge rather than an invisible 16:9 boundary.
- **Display tweaks (optional).** Disable chromatic aberration, and apply recommended anti-blur TSR settings for your resolution.
- **Fully static.** A handful of patched bytes in the executable plus a small mod container next to the game's data. No runtime hooks, no DLL injection, no Lua mods, no background processes, no performance impact. Behaviour-neutral on a 16:9 display.

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
- **The full-width UI step reported an error reading a package:** the game's packages are decoded by `tools/assetdump/kraken.py`, and a package it cannot read is a bug in that decoder or a game update that changed the compression. Say so in an issue with the exact line - it names the package.
- **The UI is still 16:9 after a game update:** the installer's second status line will say the container was built for a different build. Run **Install** again.
- **The UI is still 16:9 in game:** check that `Chronos/Content/Paks/Mods/LiSUltrawideUI_P.utoc`, `.ucas` and `.pak` are all present - the three belong together. If they are and nothing changed, say so in an issue with your resolution; the container is only mounted if the game scans that folder, and that is the one thing this fix cannot check from outside the game.
- **Everything looks wrong after a game update:** re-run the installer. It re-takes its backups from the updated game, and all code sites are located by unique byte signatures, so it either patches the new build or tells you in one line that this build needs a new version of the fix.
- **"Windows refused permission to write the game files":** the game is installed somewhere only an administrator may write to, usually under Program Files. The GUI offers to run the same install again as administrator - say yes to the Windows prompt. On the command line, start the terminal as administrator and re-run `python patcher.py`.
- **Windows or your antivirus blocked the installer:** the program is unsigned, so SmartScreen needs **More info -> Run anyway**. If an antivirus quarantined it, restore the file and exclude the folder, or leave the exe alone and run `python patcher.py` instead ([Method 2](#method-2-command-line)) - the two are the same installer.
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

### Building the GUI yourself

The repository holds no executable. `LiSUltrawidePatcher.exe` is compiled from [`LiSUltrawidePatcher.cs`](LiSUltrawidePatcher.cs) by [the release workflow](.github/workflows/build.yml) on every push to `main`, and each release names the commit it was built from - so the download can always be traced back to the source, and the two cannot drift apart. Every release archive also ships that `.cs`, stamped with the version the exe beside it carries, so the source travels with the download instead of only living here. Building it yourself needs no SDK, just the compiler that ships with Windows:

```
%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe /target:winexe /win32icon:LiSUltrawidePatcher.ico /out:LiSUltrawidePatcher.exe LiSUltrawidePatcher.cs /r:System.dll /r:System.Windows.Forms.dll /r:System.Drawing.dll /r:System.IO.Compression.dll /r:System.IO.Compression.FileSystem.dll
```

---

## License

This project is licensed under the **GNU General Public License v3.0 or later** - see [LICENSE](LICENSE). You are free to use, study, modify and redistribute it; any distributed modification must ship its complete source under the same terms.

The release archive is its own complete corresponding source. The only compiled file in it is `LiSUltrawidePatcher.exe`, and the `LiSUltrawidePatcher.cs` it was built from is packed beside it, along with `patcher.py` and every module the fix actually runs - so a copy of the zip satisfies GPL-3 section 6 by itself, wherever it was downloaded from and whether or not this repository is reachable.

As an additional term under GPL-3.0 section 7(b), every copy or modified version must preserve the copyright notice and credit the original author, Kiri11, with a link to this project.
