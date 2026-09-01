# Life is Strange: Double Exposure - Ultrawide Fix

A native ultrawide fix for **Life is Strange: Double Exposure**. Works with 21:9, 32:9, 32:10, 16:10 and any other resolution - the installer detects yours automatically.

- Cutscenes, dialogues and exploration fill the whole screen with no black bars and no cropping.
- No camera zoom or snap when a dialogue or cutscene ends.
- Photos and Max's Polaroids keep their correct proportions.
- Loading screens and HUD elements use the full width of the screen.
- Optional: disable chromatic aberration and reduce blurriness.

Nothing runs in the background and there is no performance impact. Everything can be undone with a single **Restore** button.

---

## Installation (Windows)

1. **[Download the fix](https://github.com/kiri11/Life-is-Strange-Double-Exposure-Ultrawide/releases/latest/download/LiS-DE-Ultrawide-Fix.zip)** and unpack the zip anywhere.
2. Run **`LiSUltrawidePatcher.exe`**.
3. It finds your game and your display automatically. If it does not find the game, click **Browse...** and select the game executable.
4. Leave the options ticked and click **Install**.

That is it. Start the game and play.

**Windows will warn you the first time.** The program is not code-signed, so SmartScreen shows *"Windows protected your PC"*. Click **More info**, then **Run anyway**. Some antivirus tools also flag it because it modifies a game executable. The program is built by GitHub Actions from the source in this repository, so you can check exactly what it does.

**No internet on the gaming PC?** The installer needs Python and downloads a small private copy if your PC has none. To skip that download, use **[LiS-DE-Ultrawide-Fix-bundled-with-Python.zip](https://github.com/kiri11/Life-is-Strange-Double-Exposure-Ultrawide/releases/latest/download/LiS-DE-Ultrawide-Fix-bundled-with-Python.zip)** instead. Nothing is installed system-wide either way.

### After a game update

Run **Install** again. The same applies after Steam's **Verify Integrity of Game Files**: it puts the stock game executable back, which removes the ultrawide camera part of the fix, so run **Install** again afterwards.

### Undoing the fix

Click **Restore original** in the installer.

---

## Installation (Linux and Steam Deck)

> **Tested on Windows only.** The installer supports SteamOS, the Steam Deck and Linux under Proton (native, Flatpak or Snap Steam), but has not been run on real hardware. If something does not work, please [open an issue](https://github.com/kiri11/Life-is-Strange-Double-Exposure-Ultrawide/issues).

Requirements: Python 3.6 or newer, nothing else.

1. Start the game once and quit, so that Steam creates the Proton prefix that holds `Engine.ini`.
2. Download and unpack the fix. On the Steam Deck, switch to **Desktop Mode** and open **Konsole**.
3. `cd` into the unpacked folder and run:

```bash
python3 patcher.py
```

Run with no arguments for an interactive install, or use flags:

```bash
python3 patcher.py --yes
python3 patcher.py --width 5120 --height 2160 --yes
python3 patcher.py --restore
```

Useful flags:

| Flag | Purpose |
| :--- | :--- |
| `--exe <path>` | Point at a specific `Chronos-Win64-Shipping.exe` |
| `--find-exe` | Print where the game was found and exit |
| `--check-exe` | Report whether the executable is stock, patched or unrecognised |
| `--engine-ini <path>` | Path to `Engine.ini` inside a prefix Steam does not manage (Heroic, Lutris, plain Wine) |
| `--no-exe`, `--no-game-files`, `--no-chromatic-fix` | Skip individual parts of the fix |
| `--sharpen` | Also apply the anti-blur settings |

With Steam, `Engine.ini` lives at `steamapps/compatdata/1874000/pfx/drive_c/users/steamuser/AppData/Local/Chronos/Saved/Config/Windows/Engine.ini` and is found automatically.

---

## What each option does

| Option | Effect | Changes |
| :--- | :--- | :--- |
| **Ultrawide camera** | Full-width cutscenes, dialogue and exploration | `Chronos-Win64-Shipping.exe` |
| **Full-width UI** | Loading screens and HUD use the whole screen | adds `Content/Paks/Mods/LiSUltrawideUI_P.*` |
| **Disable chromatic aberration** | Removes colour fringing at the edges | `Engine.ini` |
| **Reduce blurriness** | Recommended TSR settings for your resolution (off by default) | `Engine.ini` |

The game executable is backed up as `.exe.original` before it is changed, and the `Engine.ini` settings are added as a clearly marked block. Running the installer again never stacks changes.

---

## Troubleshooting

- **Windows or your antivirus blocked the installer:** click **More info**, then **Run anyway**. If an antivirus quarantined it, restore the file and exclude the folder.
- **The installer cannot find the game:** click **Browse** and select `Chronos\Binaries\Win64\Chronos-Win64-Shipping.exe` inside the game folder.
- **"The system refused permission to write the game files":** the game is installed somewhere only an administrator may write to. The installer offers to run again as administrator - say yes.
- **The fix stopped working after a game update or Verify Integrity:** run **Install** again. If the installer says the game version is not recognised, a new version of the fix is needed.
- **A cutscene shows black bars:** report which scene, so its camera can be included.
- **The UI is still 16:9 in game:** check that `Chronos/Content/Paks/Mods/LiSUltrawideUI_P.utoc`, `.ucas` and `.pak` are all present. If they are, open an issue with your resolution.
- **"Could not locate Engine.ini" on Linux or the Steam Deck:** start the game once, quit, and run the installer again.

### Still broken? Open an issue

[Open an issue](https://github.com/kiri11/Life-is-Strange-Double-Exposure-Ultrawide/issues) and include:

- What you did and which options were ticked.
- What is wrong and where, with a screenshot if possible.
- Your display resolution and where the game came from (Steam, Epic, other).
- The installer's log output.

---

## Technical details

The fix changes a handful of bytes in the game executable to force Unreal Engine's built-in Hor+ projection for every camera, and adds a small mod container with full-width versions of the game's UI packages. The complete reverse-engineering breakdown is in **[RESEARCH.md](RESEARCH.md)**.

`LiSUltrawidePatcher.exe` is a thin Windows front-end that runs `patcher.py`. It is compiled from [`LiSUltrawidePatcher.cs`](LiSUltrawidePatcher.cs) by [the release workflow](.github/workflows/build.yml), and each release names the commit it was built from. To build it yourself with the compiler that ships with Windows:

```
%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe /target:winexe /win32icon:LiSUltrawidePatcher.ico /out:LiSUltrawidePatcher.exe LiSUltrawidePatcher.cs /r:System.dll /r:System.Windows.Forms.dll /r:System.Drawing.dll /r:System.IO.Compression.dll /r:System.IO.Compression.FileSystem.dll
```

---

## License

This project is licensed under the **GNU General Public License v3.0 or later** - see [LICENSE](LICENSE). The release archive contains its complete source: `LiSUltrawidePatcher.cs`, `patcher.py` and every module the fix runs. The bundled archive also contains python.org's embeddable Python, unmodified, under its own PSF license in `tools/python/`.

As an additional term under GPL-3.0 section 7(b), every copy or modified version must preserve the copyright notice and credit the original author, Kiri11, with a link to this project.
