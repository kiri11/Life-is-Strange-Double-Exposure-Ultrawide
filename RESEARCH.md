# Life is Strange: Double Exposure - Ultrawide Reverse Engineering & Research

**Game:** *Life is Strange: Double Exposure*
**Engine:** Unreal Engine 5.2.1 (Shipping x86-64)
**Binary:** `Chronos-Win64-Shipping.exe` (~134.5 MB)
**Reference Resolution Tested:** 5120x2160 (21.33:9)

Historical iterations of this fix (legacy patch modes, rejected approaches, intermediate experiments) are preserved in git history; the abandoned ideas are summarized in section 6.

---

## 1. The Current Solution

Six patches (four static edits + two code caves), applied by `patcher.py --mode cine` or the GUI patcher. All code sites are located by unique byte signatures with the documented file offsets as fast paths, so the patch survives game updates that shift offsets (it aborts cleanly if a signature disappears).

```
Target File: Chronos/Binaries/Win64/Chronos-Win64-Shipping.exe
------------------------------------------------------------------------------
0x23E665C: 3B 8E E3 3F -> monitor aspect    player/exploration camera class
                                            constructor AspectRatio constant
0x69C8A8C: 39 8E E3 3F -> monitor aspect    static photo projection table
0x440ABC6: 02 -> FF                         disable MajorAxisFOV branch
0x440ABCF: 01 -> FF                         disable MaintainXFOV branch
                                            (forces the Hor+ MaintainYFOV path)
0x441A14C: movzx -> call caveA + 2-byte nop aspect-gated unconstrain
0x4005B87: call displacement -> caveB       cine (loading) views kept boxed
caveA (33 bytes), caveB (18 bytes):         written into int3 padding; located
                                            and linked at patch time
------------------------------------------------------------------------------
```

Result matrix (field-verified):

| Surface | Behavior |
|---|---|
| Cutscenes & dialogues | Full-width Hor+, 0% vertical crop |
| Free-roam exploration | Full-width (constrained at monitor aspect) |
| Photo mode / Polaroids | Correct proportions, unchanged camera UI |
| Main menu | Full-width Hor+ |
| Loading transitions | Known limitation: brief side-peek (section 5) |
| 16:9 displays | Behavior-neutral |

`Engine.ini` is not required. Optional tweaks (chromatic aberration, streaming) are listed in the README.

---

## 2. How It Works

### 2a. UE5 Already Contains Perfect Hor+ Math
`FMinimalViewInfo::CalculateProjectionMatrixGivenViewRectangle` (function fragment at file `0x440AAB0`, VA `0x14440B4B0`) implements, for unconstrained cameras in the `MaintainYFOV` branch (confirmed against Epic's `CameraTypes.cpp`):

```cpp
const bool bMaintainXFOV =
    ((SizeX > SizeY) && (AspectRatioAxisConstraint == AspectRatio_MajorAxisFOV)) ||
    (AspectRatioAxisConstraint == AspectRatio_MaintainXFOV) ||
    (ViewInfo.ProjectionMode == ECameraProjectionMode::Orthographic);
...
if (!bMaintainXFOV && ViewInfo.AspectRatio != 0.f && ...)
{
    // The view-info FOV is horizontal. Convert it to a vertical FOV using the
    // aspect ratio it was AUTHORED with, then expand horizontally.
    const float HalfXFOV = FMath::DegreesToRadians(FMath::Max(0.001f, ViewInfo.FOV) / 2.f);
    const float HalfYFOV = FMath::Atan(FMath::Tan(HalfXFOV) / ViewInfo.AspectRatio);
    MatrixHalfFOV = HalfYFOV;
}
```

With `bConstrainAspectRatio == false` and this branch active, a camera authored horizontal-for-16:9 renders **true Hor+ with unchanged vertical framing** on any wider viewport - the engine does the `atan(tan(hFOV/2)/authoredAspect)` conversion itself. No runtime FOV hook is needed; the fix reduces to (a) forcing this branch and (b) deciding which cameras are unconstrained.

**Critical rule:** `ViewInfo.AspectRatio` is the divisor - it must remain the *authored* value for any camera that is unconstrained. Overwriting it with the monitor ratio shrinks the computed vertical FOV by exactly the ultrawide factor (Vert- crop, perceived as zoom).

### 2b. Forcing the Hor+ Branch - `0x440ABC6` / `0x440ABCF`
The resolved axis-constraint enum arrives in `dl`:

```
0x440ABC0: 3B C1                  cmp  eax, ecx        ; SizeX vs SizeY
0x440ABC2: 7E 09                  jle  +9
0x440ABC4: 80 FA 02               cmp  dl, 2           ; MajorAxisFOV?
0x440ABC7: 0F 84 D2 01 00 00      je   Vert- path
0x440ABCD: 80 FA 01               cmp  dl, 1           ; MaintainXFOV?
0x440ABD0: 0F 84 C9 01 00 00      je   Vert- path
0x440ABD6: 0F B6 53 50            movzx edx, byte [rbx+0x50]   ; ProjectionMode
0x440ABDA: 80 FA 01               cmp  dl, 1           ; Orthographic?
```

Rewriting the two immediates to `FF` makes both comparisons unsatisfiable for any real enum value (0-2), so **every perspective camera takes the Hor+ path**, regardless of `ULocalPlayer` settings, sequencer overrides, or the per-camera `TOptional<EAspectRatioAxisConstraint>` (UE 5.1+) - all resolved into `dl` before this branch. The orthographic check is preserved.

This also fixes the **post-cutscene zoom bug** at its root: `ULevelSequencePlayer` overrides the local player's `AspectRatioAxisConstraint` to `MaintainXFOV` during playback and can fail to restore it afterwards (early-return when the final camera cut lands on an identical view target; UE-76517, UE forum threads 445043 / 491662). On a wide viewport a leaked `MaintainXFOV` renders gameplay vertically cropped ("zoomed in"). With the branch forced, the leaked value is irrelevant.

### 2c. Cave A: Aspect-Gated Unconstrain - `0x441A14C`
`UCameraComponent::GetCameraView` (true entry VA `0x144419EC0`) copies component state (`rbx` = component: FieldOfView `+0x2A0`, AspectRatio `+0x2B0`, bitfield `+0x2B4`, ProjectionMode `+0x2B5`) into the output `FMinimalViewInfo` (`rdi`: FOV `+0x30`, AspectRatio `+0x48`, bitfield `+0x4C`, ProjectionMode `+0x50`). The constraint flag is merged bit-exactly:

```
0x441A14C: 0F B6 83 B4 02 00 00   movzx eax, byte [rbx+0x2B4]   ; <- patched
0x441A153: 33 47 4C               xor   eax, [rdi+0x4C]
0x441A156: 83 E0 01               and   eax, 1                  ; bit 0 = bConstrainAspectRatio
0x441A159: 31 47 4C               xor   [rdi+0x4C], eax
```

The 7-byte `movzx` becomes `call caveA` + 2-byte nop:

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

Cameras authored ~16:9 (the cutscene, dialogue and menu cameras) are unconstrained and render Hor+; cameras carrying the patched monitor aspect (exploration, photo view - via the `0x23E665C` constant) and square capture cameras (~1.0) keep their constraint and classic behavior. `ecx` is reloaded immediately after the patched site, so nothing is clobbered; the cave uses no stack and needs no alignment.

Because `FMinimalViewInfo::BlendViewInfo` propagates the flag with `|=`, views blended from unconstrained sources stay unconstrained through cutscene-to-gameplay transitions.

### 2d. Cave B: Cine Views Kept Boxed - `0x4005B86`
In this game `UCineCameraComponent` drives **loading/transition views**, not cutscenes (Deck Nine's cutscene cameras are their own component classes - see 3c). Cine sensors are 16:9, so cave A would unconstrain them; cave B overrides that. The entire binary contains exactly **one direct call** to `UCameraComponent::GetCameraView`: the `Super::GetCameraView` call inside `UCineCameraComponent::GetCameraView`:

```
0x4005B6F: mov  rdi, r8          ; rdi = FMinimalViewInfo& DesiredView (non-volatile)
...
0x4005B7D: mov  r8, rdi
0x4005B80: movaps xmm1, xmm6
0x4005B83: mov  rcx, rbx
0x4005B86: call 0x144419EC0      ; Super::GetCameraView  <- rerouted to caveB
```

```
caveB: 48 83 EC 28          sub  rsp, 0x28        ; shadow space for Super
       E8 <rel32>           call 0x144419EC0      ; original Super call
       48 83 C4 28          add  rsp, 0x28
       80 4F 4C 01          or   byte [rdi+0x4C], 1   ; bConstrainAspectRatio = true
       C3                   ret
```

Every other invocation of the base `GetCameraView` is a virtual call (non-cine cameras), so this affects exactly the cine class. `rdi` survives the call (callee-saved); stack alignment and Win64 shadow-space rules are respected.

### 2e. Cave Placement
Both caves are written into `int3` inter-function padding runs in `.text` (first run large enough, found at patch time; rel32 displacements computed then too). The caves execute but never store data - `.text` is mapped R-X, so any future patch needing writable storage must add an RW section (see 5).

---

## 3. Binary Reference Map

### 3a. Patched Constants and Functions

| File Offset | VA | What |
|---|---|---|
| `0x23E665C` | - | AspectRatio constant (`3B 8E E3 3F`) in the exploration camera class constructor (function entry VA `0x1423E6F90`) |
| `0x69C8A8C` | - | Static photo projection float table: `DF 7C DB 3D 55 55 55 3F 39 8E E3 3F` (aspect at +8) |
| `0x440AAB0` | `0x14440B4B0` | `FMinimalViewInfo::CalculateProjectionMatrixGivenViewRectangle` (fragment; branch at `0x440ABC0`) |
| `0x44194C0` | `0x144419EC0` | `UCameraComponent::GetCameraView` true entry (flag merge at `0x441A14C`) |
| `0x4005B60` | `0x144006560` | `UCineCameraComponent::GetCameraView` (Super call at `0x4005B86`) |
| `0x4004910` | `0x144005310` | `UCineCameraComponent` constructor (filmback defaults 24.89/18.67, 50mm; `bConstrainAspectRatio=true` at `0x40049F6`) |
| `0x44003D0` | `0x144400DD0` | `ACineCameraActor` constructor |

Notes for anyone continuing this work:
- `.pdata` records here are heavily **chained** (hot/cold splits): resolve `UNW_FLAG_CHAININFO` to find true function entries, or caller scans come up empty.
- UObject constructors are reached via `InternalConstructor` thunks / inlining, not direct `E8` calls - map classes to constructors via the UTF-16 class-name strings passed to `GetPrivateStaticClassBody`.

### 3b. Aspect Ratio Float Constants
The binary uses two distinct "16:9" floats one bit apart - relevant when comparing aspect values exactly:
- `0x3FE38E3B` (`3B 8E E3 3F`, 1.7777779f) - camera-component constructor default;
- `0x3FE38E39` (`39 8E E3 3F`, 1.7777778f, closest float to 16/9) - `ACineCameraActor` constructor, the photo table, computed filmback ratios, and (field-verified) the cutscene cameras' serialized aspect.

Monitor-aspect replacement values:

| Resolution | Decimal | Hex (LE) |
|---|---|---|
| 2560x1080 / 5120x2160 | `2.3703703` | `26 B4 17 40` |
| 3440x1440 | `2.3888888` | `39 8E 18 40` |
| 3840x1600 | `2.4000000` | `9A 99 19 40` |
| 5120x1440 / 3840x1080 | `3.5555556` | `39 8E 63 40` |
| 1920x1080 (stock) | `1.7777778` | `3B 8E E3 3F` |

### 3c. Deck Nine Camera Class Landscape
The game does not use Sequencer CineCameras for cutscenes. Native classes found via UTF-16 string sweep: `UDUCameraComponent`, `UChronosCameraArmComponent`, `UD9CameraArmComponent`, `UD9VertigoCameraComponent`, `UChronosCameraColliderComponent`, `UChronosAICameraControlComponent`, `AChronosCameraPawn`, `AD9CameraPawn`, `UD9FreeroamCameraOffsetComponent`, `StandaloneCameraAnimationLinker`, and others. Multiple subclass constructors set `bConstrainAspectRatio` themselves (`or/mov byte [reg+0x2B4]` sites at `0x23E6648`, `0x43FEAFB`, `0x44004AB`, `0x2A519F5`, `0x2B82860`, `0x2C4959E`, ...); the plain `UCameraComponent` constructor does not. Deck Nine's Blueprints re-assert camera state per frame (`BP_MaxDefault_ChronosCameraArmComponent`, `BP_DefaultCameraStateTrigger`), which is why the fix operates on the per-frame view copy rather than component members.

---

## 4. The Photo Pipeline

UE compiles a static projection float table at `0x69C8A8C` (`DF 7C DB 3D 55 55 55 3F 39 8E E3 3F`, where the last dword is the 16:9 aspect). The photo render matrix samples this table rather than the live viewport. Patching the table's aspect to the monitor ratio, together with the exploration camera constant `0x23E665C` at the same ratio, keeps Max's Polaroids and the in-game photo mechanics 1:1 and unskewed. Both constants must carry the same value - the photo view and the capture matrix have to agree.

---

## 5. Known Limitation: The Loading Side-Peek

### Analysis
During loading transitions, narrow strips of the still-streaming world can appear briefly at the screen sides. Field triangulation across gate variants (see git history) established: the loading view is **the destination scene's own cutscene-class camera held behind a loading overlay UI that is sized/anchored to 16:9**. The wide camera renders correctly; the overlay just does not cover the extra width. Because the very same camera renders the (wanted-wide) cutscene a moment later, no camera-identity or aspect gate can pillarbox loading without regressing cutscenes - it is a UI/temporal problem, accepted as a cosmetic limitation.

### Candidate Solutions (Future Work)
1. **Streaming mitigation (`Engine.ini`, low risk, partial):** shrink the ugly window so side content appears already-loaded (still visible, but finished): `r.Streaming.PoolSize=4096`, `r.Streaming.FramesForFullUpdate=1`, optionally `s.AsyncLoadingTimeLimit=10`.
2. **Loading overlay widget fix (blocked):** stretch the loading UI to full viewport. The game ships UE5 IoStore containers (`pakchunk*.utoc/.ucas`) with an encrypted/hashed directory index - no plaintext asset paths recoverable without the AES key, so identifying and patching the overlay asset is currently impractical.
3. **Minimal runtime mask (complete fix, needs opt-in):** a load-event-scoped mod (e.g. UE4SS) that, during the load window only, writes the held camera's `AspectRatio` member outside cave A's (1.75, 1.8) window (e.g. 1.9f) and restores it once the level is interactive. One write per load; no per-frame contention with the caves (unlike naive per-tick flag toggling, which visibly fights the game's Blueprint camera system).
4. **Native temporal gate (advanced):** extend cave A with a "recently loaded" check - needs a writable storage slot (new RW PE section; `.text` is R-X), a reset signal hooked into a once-per-load code path, and a time source such as `GFrameCounter`. Feasible but disproportionate.
5. **Diagnostics:** a recorder cave appending (vtable, AspectRatio, constrain) tuples per view into an RW ring buffer, dumped externally via `ReadProcessMemory` - maps camera classes to on-screen moments with certainty, without any runtime mod inside the game.

---

## 6. Dead Ends and Technical Pitfalls Explored

1. **Patching all 11 aspect constants to the monitor ratio ("full ultrawide")**: fills the screen but is Vert- - the fixed 16:9 horizontal FOV spans the wider frame, cropping ~20% of the vertical content (cut heads/chins in cinematics).
2. **Patching only the player camera + photo table ("2-offset clean")**: correct photos and covered loading, but cutscenes stay pillarboxed and exploration is Vert- (the constrained camera at monitor aspect crops vertically, perceived as a zoom-in when leaving cutscenes). Both constants remain part of the current solution; as a *standalone* fix this mode is superseded.
3. **Unconstraining every camera unconditionally**: produces perfect Hor+ cutscenes and exploration but breaks the photo pipeline (viewfinder/capture mismatch -> skewed Polaroids) and exposes streaming during loads. Constraint policy must be selective - hence the aspect gate.
4. **`Engine.ini` `AspectRatioAxisConstraint=AspectRatio_MaintainYFOV` alone**: ineffective - the axis constraint is only consulted on the *unconstrained* path (cutscene cameras are constrained), and the sequencer overrides/leaks the LocalPlayer value at runtime. Superseded by the branch patch (2b).
5. **38-byte NOP replacement of the flag-copy block**: crashed at boot (`EXCEPTION_ACCESS_VIOLATION`) - the replacement clobbered instruction boundaries. Only the single 7-byte `movzx` needs replacing; also, `[rdi+0x50]` in that function is `ProjectionMode`, not the axis constraint.
6. **C++ constructor-default patches for camera behavior** (CDO FOV values, `UCineCameraComponent::bConstrainAspectRatio` default): unreliable - cooked asset delta-serialization and Deck Nine's per-frame Blueprint camera logic re-supply values downstream of constructors. Patch the per-frame view copy instead.
7. **UE4SS Lua per-tick camera overrides**: a polling loop toggling `bConstrainAspectRatio`/axis constraint on components visibly fights the game's Blueprint camera system (flicker, lag, delayed aspect transitions). Runtime property wrestling is a dead end in this game; if a runtime component is ever reintroduced, it must be event-scoped (see 5.3).
8. **Class-identity gates for the loading view** (cine-only unconstrain, exact-16/9 float matching in either direction): field A/B testing proved the loading view shares camera identity and aspect constants with cutscene/menu cameras in every combination tried - see section 5 for why no such gate can work.
9. **SUWSF in-memory patching for this fix**: SUWSF cannot express patch-time-computed code caves and rel32 displacements; its float-value patches also can't implement conditional logic. (Historical note: SUWSF's `Value="auto"` doesn't parse for floats; `Value="aspectratio"` works for plain constants.)

---

## 7. Reference Links

* **Lyall's UE ultrawide fixes:** [https://codeberg.org/Lyall](https://codeberg.org/Lyall) - SHfFix and TormentedSouls2Fix implement the same "clear bConstrainAspectRatio + force MaintainYFOV" pair as safetyhook mid-hooks; `UltrawidePatches` contains generic SUWSF patterns for simpler UE titles.
* **UE sequencer axis-constraint leak (zoom bug root cause):**
  [https://forums.unrealengine.com/t/4-24-onwards-aspectratioaxisconstraint-is-not-restored-if-a-camera-cut-occurs-between-two-identical-viewtargets/491662](https://forums.unrealengine.com/t/4-24-onwards-aspectratioaxisconstraint-is-not-restored-if-a-camera-cut-occurs-between-two-identical-viewtargets/491662)
  [https://forums.unrealengine.com/t/aspectratioaxisconstraint-is-resets-after-playing-sequencer/445043](https://forums.unrealengine.com/t/aspectratioaxisconstraint-is-resets-after-playing-sequencer/445043)
* **UE5 projection pipeline deep dive:** [https://80.lv/articles/deep-dive-ue5-camera-vs-scenecapture-maintain-axis-frustum-math-projection-pipeline](https://80.lv/articles/deep-dive-ue5-camera-vs-scenecapture-maintain-axis-frustum-math-projection-pipeline)
* **PCGamingWiki:** [https://www.pcgamingwiki.com/wiki/Life_Is_Strange:_Double_Exposure](https://www.pcgamingwiki.com/wiki/Life_Is_Strange:_Double_Exposure)
* **SUWSF:** [https://github.com/PhantomGamers/SUWSF](https://github.com/PhantomGamers/SUWSF)

---

## 8. Tools in This Package

1. **`patcher.py`** - cross-platform Python 3.6+ patcher, zero dependencies. Modes: `cine` (recommended), `horplus`, `hybrid`, `clean`, `full` (see `--help`), `stock` (restore). Signature-scan fallback for game updates; always patches from the pristine `.original` backup so modes never stack.
2. **`LiSUltrawidePatcher.exe` (& `.cs`)** - Windows GUI (WinForms; buildable with the stock .NET Framework `csc.exe`): auto-detects the game executable and monitor resolution, applies the recommended mode, 1-click restore.
