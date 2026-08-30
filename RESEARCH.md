# Life is Strange: Double Exposure - Ultrawide Reverse Engineering & Research Archive

**Game:** *Life is Strange: Double Exposure*  
**Engine:** Unreal Engine 5.2.1 (Shipping x86-64)  
**Binary:** `Chronos-Win64-Shipping.exe` (~134.5 MB)  
**Target Resolution Tested:** 5120x2160 (21.33:9 / 64:27 Ultra-Wide 4K)  

---

## 1. Executive Summary & Recommended Solution

### The 2-Offset Clean Solution (8 Bytes Total)
The optimal configuration for playing *Life is Strange: Double Exposure* on Ultrawide (21:9 / 32:9) monitors:
- **Exploration & Free-Roam:** Full native ultrawide edge-to-edge.
- **Polaroid Photos & In-Game Photos:** 1:1 pixel-perfect with zero stretching or distortion.
- **Cinematic Cutscenes & Dialogues:** Pristine uncropped 16:9 framing (director's original composition with zero vertical loss / no cut chins or heads).

```
Target File: Chronos/Binaries/Win64/Chronos-Win64-Shipping.exe
---------------------------------------------------------------------------------------------
Offset 0x23E665C (4 bytes): 3B 8E E3 3F (16:9) -> 26 B4 17 40 (5120x2160 / 2.3703703f)
Offset 0x69C8A8C (4 bytes): 39 8E E3 3F (16:9) -> 26 B4 17 40 (5120x2160 / 2.3703703f)
---------------------------------------------------------------------------------------------
```

### Is `Engine.ini` Required?
**No.** The 2-offset binary patch is completely self-contained. `Engine.ini` tweaks (`AspectRatioAxisConstraint`) are not required for this fix to function.

---

## 2. Complete Reverse-Engineering Map: 11 Aspect Ratio Offsets

Through disassembly of `Chronos-Win64-Shipping.exe`, 11 distinct aspect ratio constructor and table locations were identified:

| Index | Hex File Offset | Original Hex (16:9) | Function / Context in Binary | Role in Game |
|:---:|:---:|:---:|:---|:---|
| **1** | `0x23E5558` | `3B 8E E3 3F` | `APlayerCameraManager::APlayerCameraManager()` | Camera manager constructor |
| **2** | `0x23E5739` | `3B 8E E3 3F` | `APlayerCameraManager` sub-routine | Camera manager sub-initialization |
| **3** | `0x23E665C` | `3B 8E E3 3F` | `UCameraComponent::UCameraComponent()` | **Player pawn free-roam exploration camera** |
| **4** | `0x257BDEC` | `39 8E E3 3F` | `ACameraActor::ACameraActor()` | Static camera actor constructor |
| **5** | `0x43FEB0F` | `3B 8E E3 3F` | `FMinimalViewInfo` constructor 1 | View target struct initialization |
| **6** | `0x43FEB58` | `3B 8E E3 3F` | `CameraAnim` / `UCameraModifier` 1 | Cutscene / cinematic camera modifier |
| **7** | `0x43FEFD1` | `3B 8E E3 3F` | `FMinimalViewInfo` copy constructor | View target copy routine |
| **8** | `0x44004BF` | `3B 8E E3 3F` | `FMinimalViewInfo` default constructor | View target default state |
| **9** | `0x440050B` | `3B 8E E3 3F` | `CameraAnim` / `UCameraModifier` 2 | Cutscene / animation camera modifier |
| **10** | `0x4401BBF` | `3B 8E E3 3F` | `FMinimalViewInfo` reset routine | View target reset routine |
| **11** | `0x69C8A8C` | `39 8E E3 3F` | Static Camera Projection Float Table | **Photo-mode view projection & photo renderer** |

---

## 3. The Photo Skewing Breakthrough

### The Problem
Earlier community attempts (such as generic resolution switchers or hooking `APlayerCameraManager`) successfully widened the viewport to 21:9, but photos taken in-game (such as Max's Polaroid photographs and interactive photo mechanics) were rendered with severe horizontal stretching/skewing.

### The Root Cause
Unreal Engine 5 compiles a static projection float constant table at `0x69C8A8C` containing `DF 7C DB 3D 55 55 55 3F 39 8E E3 3F` (where `39 8E E3 3F` is `1.7777778f` / 16:9). The photo camera shader samples this static table rather than reading dynamically from the active viewport.

### The Solution
Patching `0x69C8A8C` directly to the target monitor's aspect ratio (e.g. `26 B4 17 40` for 5120x2160) causes the photo render-target matrix to compute at 21.33:9, resulting in 1:1 undistorted photos.

---

## 4. Cutscene Aspect Ratio & The Vert- vs. Hor+ Dilemma

### Background
Unreal Engine 5 cinematics in *Life is Strange: Double Exposure* are authored using **Cinematic Sequencer with Cine Camera Actors**. These cameras use fixed 16:9 DSLR/cinema sensor dimensions (e.g. 36.0mm x 20.25mm) and fixed focal lengths (e.g. 35mm, 50mm, 85mm portrait lenses).

### Why Full 21:9 Cutscenes Cause Vertical Cropping (Vert-)
When all 11 aspect ratio offsets are patched to 21:9:
1. Unreal Engine forces the 16:9 CineCamera shot to span the full width of the 21:9 monitor.
2. Because the horizontal angle of the lens is fixed by the director's focal length, spanning that fixed angle across a 21:9 width forces the top and bottom ~20% of the frame off-screen.
3. This cuts off characters' heads, foreheads, and chins in cinematic dialogues and close-ups.

### Why the 2-Offset Patch Solves This
By patching **only** `0x23E665C` (Player Camera) and `0x69C8A8C` (Photo Table), while leaving the other 9 locations at stock 16:9:
- Free-roam exploration and photos render in full 21:9.
- Cinematic cutscenes and dialogue cameras remain constrained to their original 16:9 box with side pillarboxes, ensuring 100% complete facial framing and headroom.

---

## 5. Standalone Tools Analysis (`patcher.py` and `LiSUltrawidePatcher.exe`)

### Do `patcher.py` and `LiSUltrawidePatcher.exe` Cause Vertical Crop?
**Yes, in 11-offset mode.**

* **11-Offset Mode (Full Cutscene Ultrawide):**
  The original 11-offset algorithm patches every camera constructor and view target struct in the binary. This expands cutscenes to fill the 21:9 monitor with no black bars, but causes the **~20% vertical crop (Vert-)** in cinematics and dialogues.
* **2-Offset Mode (Clean Exploration + Uncropped 16:9 Cutscenes):**
  Patches only `0x23E665C` and `0x69C8A8C`. Exploration is 21:9 and photos are unskewed, while cutscenes stay in their uncropped 16:9 box with **zero vertical loss**.

Both patchers are documented and updated to support both modes depending on whether the user prioritizes zero black bars or zero vertical cropping in cinematics.

---

## 6. Dead Ends and Technical Pitfalls Explored

### Dead End 1: SUWSF In-Memory Auto-Resolution Limitation
* **Hypothesis:** Use `SUWSF.asi` with `Value="auto"` and `ValueType="float"` to dynamically detect resolution and patch RAM at launch.
* **Finding:** SUWSF's C++ parser (`std::stof` / `stoi`) does not support the string `"auto"` when `ValueType="float"`. It logs `Could not interpret value expression, skipping patch...` and skips the patch entirely.
* **Workaround:** SUWSF requires explicit float values (e.g. `Value="2.3703703"`) and sequential `Match="1"` indices to handle memory shifts during byte replacement.

### Dead End 2: Direct Machine Code Patching of `CalcSceneView` (`0x441A14C`)
* **Hypothesis:** Replace the 38-byte block in `CalcSceneView` that sets `bConstrainAspectRatio` and `AspectRatioAxisConstraint` with `mov byte ptr [rdi+4Ch], 0; mov byte ptr [rdi+50h], 0` followed by NOPs (`0x90`).
* **Finding:** The instruction boundary calculation clobbered the subsequent pointer setup instruction (`mov eax, [rbx + 0x2A4]`), resulting in an immediate `EXCEPTION_ACCESS_VIOLATION` (`0xffffffffc3a3046d`) at game boot.
* **Conclusion:** Hardcoded bytecode replacements inside complex rendering pipelines risk breaking register state. Clean offset replacement of float constants is significantly safer and 100% stable.

### Dead End 3: C++ CDO Base FOV Constructor Patching
* **Hypothesis:** Patching the 9 paired camera constructor FOV instructions (`mov [reg + 0x2A0], 90.0f`) to `106.26f` or `115.0f` would pull the third-person gameplay camera back.
* **Finding:** The gameplay camera in *Life is Strange: Double Exposure* is managed dynamically at runtime by Deck Nine's Blueprint system (`BP_MaxDefault_ChronosCameraArmComponent` and `BP_DefaultCameraStateTrigger`). The Blueprint ticks every frame and overrides base C++ constructor defaults, rendering C++ CDO FOV patches ineffective for free-roam camera distance.

### Dead End 4: `Engine.ini` Axis Constraint Overrides
* **Hypothesis:** Adding `[/Script/Engine.LocalPlayer] AspectRatioAxisConstraint=AspectRatio_MaintainYFOV` to `Engine.ini` would force True Hor+ scaling in CineCameras.
* **Finding:** While `MaintainYFOV` is standard for standard player cameras, Unreal Engine 5's `UCineCameraComponent` overrides viewport constraints when evaluating Sequencer keyframes, so `.ini` tweaks alone do not prevent CineCamera vertical cropping.

---

## 7. Aspect Ratio Reference Table

| Resolution | Aspect Ratio | Decimal Value | 4-Byte IEEE 754 Hex (Little Endian) |
|---|---|---|---|
| **2560x1080** | 21:9 (64:27) | `2.3703703` | `26 B4 17 40` |
| **3440x1440** | 21.5:9 (43:18) | `2.3888888` | `39 8E 18 40` |
| **3840x1600** | 21.6:9 (12:5) | `2.4000000` | `9A 99 19 40` |
| **5120x2160** | 21.33:9 (64:27) | `2.3703703` | `26 B4 17 40` |
| **5120x1440** | 32:9 (32:9) | `3.5555556` | `39 8E 63 40` |
| **1920x1080** | 16:9 (Stock) | `1.7777778` | `3B 8E E3 3F` |

---

## 8. Relevant Community Projects & Research Links

* **SUWSF (Somewhat Universal Widescreen Fix):**  
  [https://github.com/PhantomGamers/SUWSF](https://github.com/PhantomGamers/SUWSF)  
  *Utility for generic pattern-based in-memory widescreen patching.*

* **Lyall's Unreal Engine Ultrawide Fixes:**  
  [https://github.com/Lyall](https://github.com/Lyall)  
  *Reference implementations for UE4/UE5 camera projection, resolution scaling, and cutscene pillarbox handling.*

* **PCGamingWiki - Life is Strange: Double Exposure:**  
  [https://www.pcgamingwiki.com/wiki/Life_Is_Strange:_Double_Exposure](https://www.pcgamingwiki.com/wiki/Life_Is_Strange:_Double_Exposure)

* **Universal Unreal Engine Unlocker (UUU):**  
  [https://framedsc.com/GeneralGuides/universal_ue_unlocker.htm](https://framedsc.com/GeneralGuides/universal_ue_unlocker.htm)  
  *Direct console injection tool for adjusting runtime Blueprint camera parameters (`fov <angle>`).*

---

## 9. Standalone Patcher Tool Suite Created

The following tools and files are bundled in `d:\SteamLibrary\steamapps\common\LifeIsStrangeDoubleExposure\LiS_DoubleExposure_Ultrawide_Fix\`:

1. **`LiSUltrawidePatcher.exe` (& `.cs`):**  
   Windows GUI utility written in C# (.NET 6.0 / Windows Forms) featuring:
   - Automatic monitor resolution detection.
   - Preset resolution selector (3440x1440, 5120x2160, 3840x1600, 5120x1440).
   - Cutscene Mode toggle: Uncropped 16:9 (Zero vertical crop) vs. Full Ultrawide (Edge-to-edge with 16:9 lens crop).
   - Automatic `.original` pristine backup creation.
   - 1-click restore to original stock condition.
2. **`patcher.py`:**  
   Cross-platform Python 3.6+ command-line patcher with 0 external dependencies (ideal for Steam Deck / Linux / Proton / macOS).
3. **`SUWSF.ini`:**  
   Configured SUWSF pattern file for users preferring in-memory patching without modifying disk files.
4. **`README.md`:**  
   Complete user and developer documentation with zero emojis and full technical HxD offset tables.
