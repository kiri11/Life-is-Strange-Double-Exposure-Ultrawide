# Life is Strange: Double Exposure - Ultrawide Reverse Engineering & Research Archive

**Game:** *Life is Strange: Double Exposure*  
**Engine:** Unreal Engine 5.2.1 (Shipping x86-64)  
**Binary:** `Chronos-Win64-Shipping.exe` (~134.5 MB)  
**Target Resolution Tested:** 5120x2160 (21.33:9 / 64:27 Ultra-Wide 4K)  

---

## 1. Executive Summary & Recommended Solution

> **Update 2:** the v2 Hybrid was rejected in field testing (flicker/lag - see 4e) and superseded by the fully static **v3 "Cine Hor+"** mode (section 4e): the classic 2-offset patch for exploration/photos/loading, plus a 1-byte `UCineCameraComponent` constructor patch and the 2-byte Hor+ branch patch for cutscenes. This is the current recommended configuration.
>
> **Update 1:** in-game testing surfaced two regressions in the all-static all-cameras mode below (skewed photos, visible loading pop-in). The solution below remains available as an experimental mode.

### The True Hor+ Solution (4 Patches, 13 Bytes Total) - EXE-ONLY VARIANT
The optimal configuration for playing *Life is Strange: Double Exposure* on Ultrawide (21:9 / 32:9) monitors:
- **Everything (Cutscenes, Dialogues, Exploration):** Full-width Hor+ rendering with **0% vertical crop**. The complete 16:9 vertical framing is preserved and horizontal FOV is expanded to fill the monitor.
- **Polaroid Photos & In-Game Photos:** 1:1 pixel-perfect with zero stretching or distortion.
- **Cutscene-to-Gameplay Transitions:** No zoom-in jump (see section 4c for the root cause).
- **On a 16:9 display:** behavior-neutral (the Hor+ conversion degenerates to identity).

```
Target File: Chronos/Binaries/Win64/Chronos-Win64-Shipping.exe
---------------------------------------------------------------------------------------------
Offset 0x441A14C (7 bytes): 0F B6 83 B4 02 00 00 -> 31 C0 0F 1F 44 00 00
    (UCameraComponent::GetCameraView: force bConstrainAspectRatio=false for all cameras)
Offset 0x440ABC6 (1 byte):  02 -> FF   (kill AspectRatio_MajorAxisFOV branch)
Offset 0x440ABCF (1 byte):  01 -> FF   (kill AspectRatio_MaintainXFOV branch)
    (FMinimalViewInfo::CalculateProjectionMatrixGivenViewRectangle: always take the
     MaintainYFOV Hor+ path)
Offset 0x69C8A8C (4 bytes): 39 8E E3 3F -> 26 B4 17 40 (5120x2160 / photo table)
---------------------------------------------------------------------------------------------
CRITICAL: 0x23E665C (player camera AspectRatio constant) must stay STOCK (3B 8E E3 3F).
The engine's Hor+ math divides by this authored aspect ratio; patching it to the
monitor ratio re-introduces vertical cropping ("zoom"). See section 4b.
```

See section 4b for the full mechanism and disassembly evidence.

### The Legacy 2-Offset Clean Solution (8 Bytes Total)
Previous recommendation, still available as "clean" mode: ultrawide exploration + photos, pillarboxed 16:9 cutscenes. Downsides discovered later: exploration is Vert- (vertically cropped vs 16:9), which shows up as a zoom-in when leaving cutscenes.

```
Offset 0x23E665C (4 bytes): 3B 8E E3 3F (16:9) -> 26 B4 17 40 (5120x2160 / 2.3703703f)
Offset 0x69C8A8C (4 bytes): 39 8E E3 3F (16:9) -> 26 B4 17 40 (5120x2160 / 2.3703703f)
```

### Is `Engine.ini` Required?
**No.** Both binary patch sets are completely self-contained. `Engine.ini` tweaks (`AspectRatioAxisConstraint`) are not required for either mode to function. Optional cosmetic tweak at ultrawide: `r.SceneColorFringeQuality=0` (reduces chromatic aberration at the expanded edges).

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

### Why the 2-Offset Patch Avoided Cutscene Cropping (But Was Still Vert- In Gameplay)
By patching **only** `0x23E665C` (Player Camera) and `0x69C8A8C` (Photo Table), while leaving the other 9 locations at stock 16:9:
- Free-roam exploration and photos render full-width 21:9 - but **Vert-**: the player camera stays constrained with a fixed 16:9-authored horizontal FOV spanned across 2.37, so ~20% of the vertical content is cropped relative to 16:9 (this is what causes the "zoom-in" perception when leaving cutscenes; see 4c).
- Cinematic cutscenes and dialogue cameras remain constrained to their original 16:9 box with side pillarboxes, ensuring 100% complete facial framing and headroom.

---

## 4b. The Hor+ Breakthrough (Current Solution)

### UE5 Already Contains Perfect Hor+ Math
Disassembly of `FMinimalViewInfo::CalculateProjectionMatrixGivenViewRectangle` (function start file `0x440AAB0` / VA `0x14440B4B0`) shows the UE 5.x unconstrained projection path (confirmed against Epic's `CameraTypes.cpp`):

```cpp
const bool bMaintainXFOV =
    ((SizeX > SizeY) && (AspectRatioAxisConstraint == AspectRatio_MajorAxisFOV)) ||
    (AspectRatioAxisConstraint == AspectRatio_MaintainXFOV) ||
    (ViewInfo.ProjectionMode == ECameraProjectionMode::Orthographic);
...
if (!bMaintainXFOV && ViewInfo.AspectRatio != 0.f && !CVarUseLegacyMaintainYFOV...)
{
    // The view-info FOV is horizontal. Convert it to a vertical FOV using the
    // aspect ratio it was AUTHORED with, then expand horizontally to the viewport.
    const float HalfXFOV = FMath::DegreesToRadians(FMath::Max(0.001f, ViewInfo.FOV) / 2.f);
    const float HalfYFOV = FMath::Atan(FMath::Tan(HalfXFOV) / ViewInfo.AspectRatio);
    MatrixHalfFOV = HalfYFOV;
}
```

With `bConstrainAspectRatio == false` and the `MaintainYFOV` branch active, a camera authored horizontal-for-16:9 renders **true Hor+ with unchanged vertical framing** on any wider viewport - the engine does the `atan(tan(hFOV/2)/1.7778)` conversion itself. No runtime hook or trigonometry patch is needed; the entire fix reduces to (a) clearing the constraint flag and (b) forcing this branch.

This is also exactly how Lyall's UE5 ASI fixes work (SHfFix, TormentedSouls2Fix: clear `bConstrainAspectRatio` + force constraint enum 0 = `MaintainYFOV`), and Lyall's Bramble SUWSF pattern (community-reported to work on LiS:DE) is the same pair as generic byte patches.

### Patch 1: Unconstrain All Cameras - `0x441A14C`
The function previously mislabeled "CalcSceneView" (Dead End 2) is actually **`UCameraComponent::GetCameraView`** copying component state (`rbx` = UCameraComponent: FieldOfView at `+0x2A0`, AspectRatio at `+0x2B0`, bitfield at `+0x2B4`, ProjectionMode at `+0x2B5`) into the output `FMinimalViewInfo` (`rdi`: FOV at `+0x30`, AspectRatio at `+0x48`, bitfield at `+0x4C`, ProjectionMode at `+0x50`):

```
0x441A14C: 0F B6 83 B4 02 00 00   movzx eax, byte [rbx+0x2B4]   ; component bitfield
0x441A153: 33 47 4C               xor   eax, [rdi+0x4C]
0x441A156: 83 E0 01               and   eax, 1                  ; bit 0 = bConstrainAspectRatio
0x441A159: 31 47 4C               xor   [rdi+0x4C], eax         ; merge bit 0 into output
0x441A15C: ...                    (identical merge for bit 1 = bUseFieldOfViewForLOD)
```

Replacing the 7-byte `movzx` with `31 C0` (`xor eax,eax`) + `0F 1F 44 00 00` (5-byte NOP) makes the merge **clear** bit 0 instead of copying it: `bConstrainAspectRatio` is forced false for every camera view. Cinematic cameras are covered because `UCineCameraComponent::GetCameraView` calls `Super::GetCameraView` (this code). The patch is instruction-boundary exact - this is the correct version of what Dead End 2 attempted.

Blends are covered too: `FMinimalViewInfo::BlendViewInfo` propagates the flag with `bConstrainAspectRatio |= OtherInfo.bConstrainAspectRatio;` - since no source view ever has it set, blended views stay unconstrained throughout cutscene-to-gameplay transitions.

### Patches 2+3: Force the MaintainYFOV Branch - `0x440ABC6` / `0x440ABCF`
Inside `CalculateProjectionMatrixGivenViewRectangle`, the resolved axis-constraint enum arrives in `dl`:

```
0x440ABC0: 3B C1                  cmp  eax, ecx        ; SizeX vs SizeY
0x440ABC2: 7E 09                  jle  +9
0x440ABC4: 80 FA 02               cmp  dl, 2           ; AspectRatio_MajorAxisFOV?
0x440ABC7: 0F 84 D2 01 00 00      je   Vert- path
0x440ABCD: 80 FA 01               cmp  dl, 1           ; AspectRatio_MaintainXFOV?
0x440ABD0: 0F 84 C9 01 00 00      je   Vert- path
0x440ABD6: 0F B6 53 50            movzx edx, byte [rbx+0x50]   ; ProjectionMode
0x440ABDA: 80 FA 01               cmp  dl, 1           ; Orthographic?
0x440ABDD: 0F 84 ...              je   Vert- path
           ...                    ; else: MaintainYFOV Hor+ path (reads AspectRatio at +0x48)
```

Rewriting the two immediates (`02` at `0x440ABC6`, `01` at `0x440ABCF`) to `FF` makes both comparisons unsatisfiable for any real enum value (0-2), so **every perspective camera falls through into the Hor+ path**, regardless of `ULocalPlayer` settings, sequencer overrides, or the per-camera `TOptional<EAspectRatioAxisConstraint>` (UE 5.1+) - all of which are resolved into `dl` *before* this branch. The orthographic check is preserved.

### Patch 4: Photo Table - `0x69C8A8C`
Unchanged from the previous solution (section 3).

### Why 0x23E665C Must Stay Stock
`ViewInfo.AspectRatio` is the divisor in the engine's `HalfYFOV = atan(tan(HalfXFOV) / AspectRatio)` conversion - it means "the aspect ratio this FOV was authored for". The legacy clean mode overwrote it with the monitor ratio (2.37), which shrinks the computed vertical FOV by exactly the ultrawide factor: that is the Vert- crop / zoom. With the constant left at 1.7777778, vertical framing is preserved and only horizontal expands.

---

## 4c. The Cutscene-to-Gameplay Zoom-In Bug: Root Cause

Two compounding mechanisms were identified:

1. **Legacy fix was Vert- in gameplay.** With `0x23E665C` patched to 2.37, exploration rendered the 16:9 image with ~20% of the vertical content cropped and the remainder magnified to full width. A pillarboxed 16:9 cutscene handing control back to that camera visibly "zooms in" at the seam. Inherent to the legacy mode; resolved by the Hor+ patch set.

2. **Known UE engine bug: sequencer leaks `MaintainXFOV`.** `ULevelSequencePlayer` overrides the local player's `AspectRatioAxisConstraint` to `AspectRatio_MaintainXFOV` during playback and is supposed to restore it afterwards, but an early-return path skips restoration when the final camera cut lands on an identical view target (UE-76517; UE forum threads 445043, 491662). On a wide viewport, a leaked `MaintainXFOV` renders gameplay with fixed horizontal FOV = vertically cropped = "zoomed in" until something resets the constraint. Patches 2+3 make the leaked value irrelevant by ignoring the enum entirely at the projection branch.

---

## 4d. Field Results of the Static Hor+ Patch and the v2 Hybrid Architecture

### In-Game Test Results (Static 4-Patch Hor+, 5120x2160)
- Cutscenes: confirmed true Hor+, full vertical framing, no zoom. Working as designed.
- Cutscene-to-gameplay: the zoom ramp is gone; a hard FOV *cut* ("jump") remains at some handoffs (the game cuts between cinematic and gameplay FOV; cosmetic, tracked separately).
- **Regression 1 - photos skewed.** With every camera unconstrained, the photography pipeline no longer matches the static projection table. Under the legacy fix (viewport constrained to 2.37 + table at 2.37) captures were consistent; with the Hor+ viewport the FOV<->table relationship changes and Polaroids render skewed.
- **Regression 2 - loading pop-in visible.** The view during/after level loads was previously a constrained 16:9 camera (black side bars covered streaming). Unconstrained, the screen sides briefly show geometry/texture streaming.

Both regressions share one root cause: patch `0x441A14C` unconstrains *every* camera unconditionally, but photo mode and loading views are contexts that *should* stay 16:9. A static byte patch cannot be conditional.

### The v2 Hybrid Architecture (Current Recommended)
Split the fix into a static part (safe, context-free) and a runtime policy part:

- **Exe (2 bytes):** only patches 2+3 (`0x440ABC6`/`0x440ABCF`, force the MaintainYFOV Hor+ branch). This is inert for constrained cameras: the constrained path never consults the axis-constraint enum. The photo table `0x69C8A8C` stays STOCK.
- **UE4SS Lua mod (`UltrawideCameraFix` v2):** owns the `bConstrainAspectRatio` policy per camera *component member* (not the per-frame camera cache, so there is no race with the render loop - `UCameraComponent::GetCameraView` copies the member every frame):
  - Steady state: all `CameraComponent`/`CineCameraComponent` instances get `bConstrainAspectRatio=false` -> Hor+ everywhere (equivalent to the static patch). `NotifyOnNewObject` on `CineCameraComponent` catches cutscene cameras the frame they spawn; a 400 ms reconcile loop covers everything else.
  - Photography UI open (`BP_PhotographyWindow_C` construct/destruct hooks + visibility polling): non-cine cameras are re-constrained -> pillarboxed 16:9 viewfinder, and since the exe carries no other patches, the photo pipeline is **bit-identical to vanilla** - photos cannot skew by construction.
  - Post-load grace (`PlayerController:ClientRestart` hook + configurable `GraceMs`, default 4000 ms): non-cine cameras stay constrained -> black 16:9 pillars mask streaming pop-in.
  - The mod never writes `AspectRatio` (the Hor+ conversion divides by the camera's own authored value; see 4b).
- Failure mode: if UE4SS does not load, the game renders stock pillarboxed 16:9 (the 2 exe bytes alone are effectively invisible) - a safe fallback rather than a broken state.

### Why Photo Mode Cannot Skew in Hybrid Mode
While `BP_PhotographyWindow_C` is on screen, the active camera is constrained at its authored 16:9 aspect and the static projection table holds the stock value: every byte the photo pipeline reads is identical to an unpatched game. Correctness is inherited from vanilla instead of being re-derived.

---

## 4e. Hybrid Rejected in Field Testing; the v3 "Cine Hor+" Static Solution (CURRENT)

### Why the Hybrid Failed
In-game testing of the v2 hybrid showed flickering, lag, and delayed aspect-ratio transitions. Root cause: the mod's member-level `bConstrainAspectRatio` writes contend with Deck Nine's Blueprint camera system, which re-asserts camera state per frame; a polling Lua loop (any interval) loses that race visibly. Runtime property fighting is a dead end in this game - constraint policy must live in native code.

### Discovery: Deck Nine's Camera Component Class Landscape
Systematically scanning the binary for instructions that set `bConstrainAspectRatio` (`or byte [reg+0x2B4], 1` and `mov byte [reg+0x2B4], imm`) revealed:

- The **plain `UCameraComponent` constructor** (entry VA `0x1444024E0`, discovered via the `UCineCameraComponent` ctor's base-call) does **not** set the flag - stock UE behavior.
- **Multiple subclass constructors set it explicitly**, each its own patchable site: the function containing the famous `0x23E665C` aspect constant also contains a flag-set at file `0x23E6648` - this is the *exploration/player camera component class* (which is why patching `0x23E665C` affected exploration). Additional constrained classes set the flag at `0x43FEAFB`, `0x44004AB`, and several `mov byte [reg+0x2B4], 1` sites in large Chronos component constructors (`0x2A519F5`, `0x2B82860`, `0x2C4959E`, ...) - among these live the photo and loading camera classes. Note: the original 11-offset map's attribution of offsets 5-10 to "FMinimalViewInfo constructors" is wrong; those functions write `AspectRatio` at `+0x2B0` and the constraint bit at `+0x2B4`, i.e. they are camera-component subclass constructors too.
- **`UCineCameraComponent::UCineCameraComponent()`** (VA `0x144005322`) is unambiguous - it stores the filmback defaults (24.89/18.67 sensor, 50mm focal length) and executes:

```
0x40049E9: 80 4F 3A 02              or  byte [rdi+0x3A], 2
0x40049ED: 33 C0                    xor eax, eax
0x40049EF: 80 8F 8A 00 00 00 02     or  byte [rdi+0x8A], 2
0x40049F6: 80 8F B4 02 00 00 01     or  byte [rdi+0x2B4], 1   ; bConstrainAspectRatio = true
```

Changing the final immediate `01 -> 00` (file offset `0x40049FC`) turns the instruction into a no-op: **cinematic cameras - and only cinematic cameras - default to unconstrained.** Cooked assets inherit the new default because the property equaled the old default at cook time and was therefore never delta-serialized.

### The v3 Patch Set (5 edits, 13 bytes)
```
0x23E665C: 3B 8E E3 3F -> monitor aspect   (exploration camera class - classic behavior)
0x69C8A8C: 39 8E E3 3F -> monitor aspect   (photo projection table   - classic behavior)
0x40049FC: 01 -> 00                        (cine cameras unconstrained)
0x440ABC6: 02 -> FF                        (Hor+ branch, part 1)
0x440ABCF: 01 -> FF                        (Hor+ branch, part 2)
```

### v3.1: The Constructor Patch Alone Was Not Enough - the GetCameraView Cave
Field test of v3: exploration, photos, camera UI and loading all correct, but **cutscenes stayed pillarboxed** - the constructor-default change did not reach the actual cutscene cameras. The flag evidently gets re-supplied downstream of the constructor (cooked asset delta-serialization against an archetype that stored the value, a Deck Nine cine subclass constructor - note the base ctor has zero direct E8 callers, implying subclass constructors inline it - or Blueprint initialization).

The robust fix clears the flag at the point where no later source can override it. Resolving the chained `.pdata` unwind records (the earlier "function starts" were fragments; e.g. `UCameraComponent::GetCameraView`'s true entry is VA `0x144419EC0`, and the cine ctor fragment at `0x144005322` belongs to an entry at `0x144005310`) revealed a decisive fact: **the entire 134 MB binary contains exactly one direct call to `UCameraComponent::GetCameraView`** - the `Super::GetCameraView` call inside `UCineCameraComponent::GetCameraView`:

```
0x4005B6F: mov  rdi, r8          ; rdi = FMinimalViewInfo& DesiredView (non-volatile)
...
0x4005B7D: mov  r8, rdi
0x4005B80: movaps xmm1, xmm6
0x4005B83: mov  rcx, rbx
0x4005B86: call 0x144419EC0      ; Super::GetCameraView  <- rerouted
0x4005B8B: mov  rax, [rbx]
0x4005B97: call [rax+0x5F0]      ; UpdateCameraLens (virtual)
```

Every other invocation of the base `GetCameraView` is virtual (non-cine cameras). Rerouting that one call through an 18-byte code cave written into `int3` inter-function padding:

```
cave: 48 83 EC 28          sub  rsp, 0x28        ; shadow space for Super
      E8 <rel32>           call 0x144419EC0      ; original Super call
      48 83 C4 28          add  rsp, 0x28
      80 67 4C FE          and  byte [rdi+0x4C], 0xFE   ; bConstrainAspectRatio = false
      C3                   ret
```

clears the flag on **every cinematic camera view and nothing else**, regardless of where the value came from - constructors, serialized assets, or Blueprints all write the component member, but the view copy is produced here and consumed after. `rdi` survives the call (callee-saved), stack alignment and shadow-space rules are respected, and the cave is never reached by any other code path. The patchers compute the cave location and both rel32 displacements at patch time (first sufficiently large `int3` run in `.text`), keeping the patch game-update-resilient via the call-site signature `E8 ?? ?? ?? ?? 4C 8B C7 0F 28 CE 48 8B CB E8`.

Deck Nine cine subclasses that override `GetCameraView` are still covered as long as they call up the chain (virtual dispatch lands in the cine override, which runs the patched call). The constructor byte (`0x40049FC`) is kept as harmless belt-and-braces.

### v3.1 Field Result: UCineCameraComponent Is the LOADING Camera, Not the Cutscene Camera
Testing v3.1 inverted the symptoms: **loading views went wide** (bad) while **cutscenes stayed pillarboxed** (bad). The cave demonstrably executed - on the wrong content. Conclusion: in this game `UCineCameraComponent` drives the loading/transition views, while cutscenes/dialogues use Deck Nine's own camera component stack, which reaches `UCameraComponent::GetCameraView` virtually (bypassing the cine override entirely). A UTF-16 string sweep of the binary confirms a native Deck Nine camera zoo: `UDUCameraComponent`, `UChronosCameraArmComponent`, `UD9CameraArmComponent`, `UD9VertigoCameraComponent`, `UChronosCameraColliderComponent`, `AChronosCameraPawn`, `AD9CameraPawn`, `UD9FreeroamCameraOffsetComponent`, `StandaloneCameraAnimationLinker`, and more. (Name-to-constructor mapping also identified the "offsets 8+9" function as the `ACineCameraActor` constructor.)

---

## 4f. v4 (CURRENT): The Aspect-Gated Double Cave

Class identity proved unreliable for separating "cutscene" from "loading" - but the v3 field test handed us a perfect *runtime* discriminator. With the classic exploration patch active, camera views arriving at `UCameraComponent::GetCameraView` differ by their authored `AspectRatio` member:

| View | AspectRatio member | Wanted |
|---|---|---|
| Cutscene/dialogue cameras | ~1.7778 (16:9 authored) | UNCONSTRAINED -> Hor+ |
| Exploration/photo cameras | monitor aspect (e.g. 2.3703, patched CDO) | constrained (classic) |
| Photo capture cameras | ~1.0 (square) | constrained (classic) |
| Loading views (`UCineCameraComponent`) | ~1.7778 (16:9 filmback) | constrained (pillarbox) |

Only the loading cine cameras collide with the cutscene window - and they are exactly the cameras that pass through the unique cine `Super::GetCameraView` call site from 4e. Hence two caves with opposite polarity:

**Cave A - aspect-gated unconstrain.** The 7-byte `movzx eax, byte [rbx+0x2B4]` flag-copy preamble at `0x441A14C` (the site whose unconditional clear produced the field-proven Hor+ cutscenes in v1) becomes `call caveA ; nop2`:

```
caveA: 0F B6 83 B4 02 00 00   movzx eax, byte [rbx+0x2B4]   ; original instruction
       8B 8B B0 02 00 00      mov   ecx, [rbx+0x2B0]        ; component AspectRatio
       81 F9 00 00 E0 3F      cmp   ecx, 1.75f              ; IEEE754 order == integer order
       76 0B                  jbe   done
       81 F9 66 66 E6 3F      cmp   ecx, 1.8f
       73 03                  jae   done
       83 E0 FE               and   eax, -2                 ; clear bConstrainAspectRatio
done:  C3                     ret
```

Cameras authored 16:9 are unconstrained (-> Hor+ via the forced MaintainYFOV branch); everything outside the (1.75, 1.8) window keeps its constraint. `ecx` is reloaded immediately after the patched site, so no state is clobbered; positive IEEE-754 floats compare correctly as integers.

**Cave B - cine views forced constrained.** Same reroute as v3.1's cave but with the epilogue inverted to `or byte [rdi+0x4C], 1`: every `UCineCameraComponent` (loading) view is re-constrained AFTER cave A ran inside Super, overriding the gate for the one 16:9 class that must stay pillarboxed.

### The v4 Patch Set
```
0x23E665C:  3B 8E E3 3F -> monitor aspect       (exploration camera class - classic)
0x69C8A8C:  39 8E E3 3F -> monitor aspect       (photo projection table   - classic)
0x440ABC6:  02 -> FF                            (Hor+ MaintainYFOV branch, part 1)
0x440ABCF:  01 -> FF                            (Hor+ MaintainYFOV branch, part 2)
0x441A14C:  movzx -> call caveA + nop2          (aspect-gated unconstrain)
0x4005B87:  rel32 -> caveB                      (cine/loading views forced 16:9)
caveA (33B), caveB (18B): written into int3 inter-function padding
0x40049FC:  left STOCK (cine ctor default stays constrained)
```

Expected result matrix: cutscenes/dialogues Hor+ full-width (v1-equivalent rendering); exploration classic full-width; photos classic; loading pillarboxed both via untouched non-16:9 camera classes and via cave B for cine views; menus and any other 16:9-authored views become Hor+ (harmless). Both cave locations and all displacements are computed at patch time from unique signatures.

Result matrix:
- **Cutscenes/dialogues (cine cameras):** unconstrained -> forced MaintainYFOV branch -> true Hor+ with the authored aspect as divisor. Identical rendering to the user-approved v1 cutscenes.
- **Exploration:** constrained at the patched monitor aspect - identical to the proven classic 2-offset fix.
- **Photos:** viewfinder and projection table exactly as the classic fix - confirmed correct in testing.
- **Loading/menus:** their camera classes remain constrained at stock 16:9 -> pillarboxed, streaming pop-in stays covered.
- **Known cosmetic seam:** a hard cut from a Hor+ cutscene (full 16:9 vertical) to the classic exploration framing (vertically tighter) shifts vertical FOV. Eliminating it would require the exploration camera to be Hor+ too, which is only safe if the photo camera is a different class - a candidate experiment: flip the exploration class's flag-set at `0x23E6648` (imm at `0x23E664E`, `01 -> 00`), revert `0x23E665C` to stock, then verify photos in-game.

---

## 5. Standalone Tools Analysis (`patcher.py` and `LiSUltrawidePatcher.exe`)

### Supported Modes
Both patchers (and the SUWSF configuration) now support three modes:

* **True Hor+ Mode (Recommended):**
  The 4-patch solution from section 4b. Full-width rendering everywhere with zero vertical crop, no black bars, and no zoom jump after cutscenes. Signature-scan fallback locates all code sites if a game update shifts file offsets.
* **2-Offset Legacy Clean Mode:**
  Patches only `0x23E665C` and `0x69C8A8C`. Exploration is full-width (but Vert-) and photos are unskewed, while cutscenes stay in their uncropped 16:9 pillarbox.
* **11-Offset Legacy Full Mode:**
  Patches every camera constructor and view target struct. Expands cutscenes to fill the monitor with no black bars, but causes the **~20% vertical crop (Vert-)** in cinematics and dialogues.

`patcher.py` additionally supports non-interactive CLI usage (`--mode horplus|clean|full|stock --width W --height H`).

---

## 6. Dead Ends and Technical Pitfalls Explored

### Dead End 1: SUWSF In-Memory Auto-Resolution Limitation
* **Hypothesis:** Use `SUWSF.asi` with `Value="auto"` and `ValueType="float"` to dynamically detect resolution and patch RAM at launch.
* **Finding:** SUWSF's C++ parser (`std::stof` / `stoi`) does not support the string `"auto"` when `ValueType="float"`. It logs `Could not interpret value expression, skipping patch...` and skips the patch entirely.
* **Workaround:** SUWSF requires explicit float values (e.g. `Value="2.3703703"`) and sequential `Match="1"` indices to handle memory shifts during byte replacement.

### Dead End 2: Direct Machine Code Patching of `CalcSceneView` (`0x441A14C`) - LATER RESOLVED
* **Hypothesis:** Replace the 38-byte block that sets `bConstrainAspectRatio` and `AspectRatioAxisConstraint` with `mov byte ptr [rdi+4Ch], 0; mov byte ptr [rdi+50h], 0` followed by NOPs (`0x90`).
* **Finding:** The instruction boundary calculation clobbered the subsequent pointer setup instruction (`mov eax, [rbx + 0x2A4]`), resulting in an immediate `EXCEPTION_ACCESS_VIOLATION` (`0xffffffffc3a3046d`) at game boot.
* **Resolution (section 4b):** The idea was correct but the execution was wrong on three counts: (1) the function is `UCameraComponent::GetCameraView`, not `CalcSceneView`; (2) `[rdi+0x50]` is `ProjectionMode`, not `AspectRatioAxisConstraint` - zeroing it was unnecessary; (3) only the single 7-byte `movzx` at `0x441A14C` needs replacing (`31 C0` + 5-byte NOP), which is instruction-boundary exact and stable. Clearing the flag via the existing xor/and/xor merge requires no other bytes to change.

### Dead End 3: C++ CDO Base FOV Constructor Patching
* **Hypothesis:** Patching the 9 paired camera constructor FOV instructions (`mov [reg + 0x2A0], 90.0f`) to `106.26f` or `115.0f` would pull the third-person gameplay camera back.
* **Finding:** The gameplay camera in *Life is Strange: Double Exposure* is managed dynamically at runtime by Deck Nine's Blueprint system (`BP_MaxDefault_ChronosCameraArmComponent` and `BP_DefaultCameraStateTrigger`). The Blueprint ticks every frame and overrides base C++ constructor defaults, rendering C++ CDO FOV patches ineffective for free-roam camera distance.

### Dead End 4: `Engine.ini` Axis Constraint Overrides - LATER EXPLAINED
* **Hypothesis:** Adding `[/Script/Engine.LocalPlayer] AspectRatioAxisConstraint=AspectRatio_MaintainYFOV` to `Engine.ini` would force True Hor+ scaling in CineCameras.
* **Finding:** The `.ini` tweak alone did not prevent CineCamera vertical cropping.
* **Explanation (section 4b):** Two reasons. (1) The axis constraint is only consulted in the *unconstrained* projection path; cine cameras have `bConstrainAspectRatio=true`, so the setting never applied to them. (2) Even for unconstrained views, `ULevelSequencePlayer` overrides the LocalPlayer constraint to `MaintainXFOV` during sequence playback (and can leak it afterwards - see section 4c). Both are bypassed by the binary patches: the constraint flag is cleared at the source and the enum comparisons are disabled at the projection branch.

### Dead End 5 (Historical): UE4SS Lua Per-Tick Camera Overrides
* **Approach (found in `UE4SS_Backup/Mods/UltrawideCameraFix`):** A Lua mod running a 16ms async loop setting `bConstrainAspectRatio=false` and `AspectRatioAxisConstraint=0` on the PlayerCameraManager, pawn CameraComponents, and LocalPlayer, with photo-mode detection to re-constrain to 16:9 during photography.
* **Why it was inferior:** (1) it never touched the spawned cinematic `CineCameraActor` components, so cutscenes stayed pillarboxed; (2) the async loop raced the engine's per-frame camera update (`CameraCachePrivate.POV` is overwritten before rendering); (3) `UCameraComponent.AspectRatioAxisConstraint` only applies when `bOverrideAspectRatioAxisConstraint` is set; (4) it required the full UE4SS runtime. The 13-byte binary patch achieves strictly more with zero runtime.

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

* **Lyall's current fix repositories (migrated to Codeberg):**  
  [https://codeberg.org/Lyall](https://codeberg.org/Lyall) - SHfFix and TormentedSouls2Fix implement the same "clear bConstrainAspectRatio + force MaintainYFOV" pair as safetyhook mid-hooks; `UltrawidePatches` contains the Bramble SUWSF.ini community-reported to also work on LiS:DE.

* **UE sequencer axis-constraint leak (zoom bug root cause):**  
  [https://forums.unrealengine.com/t/4-24-onwards-aspectratioaxisconstraint-is-not-restored-if-a-camera-cut-occurs-between-two-identical-viewtargets/491662](https://forums.unrealengine.com/t/4-24-onwards-aspectratioaxisconstraint-is-not-restored-if-a-camera-cut-occurs-between-two-identical-viewtargets/491662)  
  [https://forums.unrealengine.com/t/aspectratioaxisconstraint-is-resets-after-playing-sequencer/445043](https://forums.unrealengine.com/t/aspectratioaxisconstraint-is-resets-after-playing-sequencer/445043)

* **UE5 projection pipeline deep dive (MaintainYFOV / frustum math):**  
  [https://80.lv/articles/deep-dive-ue5-camera-vs-scenecapture-maintain-axis-frustum-math-projection-pipeline](https://80.lv/articles/deep-dive-ue5-camera-vs-scenecapture-maintain-axis-frustum-math-projection-pipeline)

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
