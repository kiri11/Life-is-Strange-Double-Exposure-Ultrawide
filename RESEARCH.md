# Life is Strange: Double Exposure - Ultrawide Reverse Engineering & Research

**Game:** *Life is Strange: Double Exposure*
**Engine:** Unreal Engine 5.2.1 (Shipping x86-64)
**Binary:** `Chronos-Win64-Shipping.exe` (~134.5 MB)
**Reference Resolution Tested:** 5120x2160 (21.33:9)

Historical iterations of this fix (legacy patch modes, rejected approaches, intermediate experiments) are preserved in git history; the abandoned ideas are summarized in section 6.

---

## 1. The Current Solution

Three code changes (one 2-byte branch edit + two code caves) and **no aspect-ratio constants**, applied by `patcher.py` or the GUI. All code sites are located by unique byte signatures with the documented file offsets as fast paths, so the patch survives game updates that shift offsets (it aborts cleanly if a signature disappears).

```
Target File: Chronos/Binaries/Win64/Chronos-Win64-Shipping.exe
------------------------------------------------------------------------------
0x440ABC6: 02 -> FF                         disable MajorAxisFOV branch
0x440ABCF: 01 -> FF                         disable MaintainXFOV branch
                                            (forces the Hor+ MaintainYFOV path)
0x441A14C: movzx -> call caveA + 2-byte nop aspect-gated unconstrain + divisor pin
0x4005B87: call displacement -> caveB       cine (loading) views kept boxed
caveA (40 bytes), caveB (18 bytes):         written into int3 padding; located
                                            and linked at patch time
------------------------------------------------------------------------------
```

Result matrix (field-verified at 5120x2160):

| Surface | Behavior |
|---|---|
| Cutscenes & dialogues | Full-width Hor+, 0% vertical crop |
| Free-roam exploration | Full-width Hor+, 0% vertical crop |
| Dialogue / cutscene hand-off | Seamless - no pillarbox sweep, no zoom, no snap |
| Photo mode / Polaroids | Correct proportions; pipeline bit-identical to vanilla |
| Main menu | Full-width Hor+ |
| Loading transitions | Overlay covers the full screen (with the UI patch, section 9) |
| 16:9 displays | Behavior-neutral |

**Superseded in v9.** Earlier releases also wrote the monitor aspect into two float constants - the player-camera constructor default at `0x23E665C` and the photo projection table at `0x69C8A8C`. Runtime measurement (section 10) showed they never governed what they were believed to: free-roam already renders Hor+ through cave A, and the photo pipeline is correct precisely *because* both constants keep their shipped 16:9 values. Patching them only desynchronised the dialogue hand-off. They are now left stock, and the patch is smaller for it.

`Engine.ini` is not required for the camera fix. The installer can optionally write chromatic-aberration and anti-blur TSR settings; see the README.

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

Critically, the component's `AspectRatio` is copied into the output view in the two instructions *immediately before* the patched site, so inside the cave `rdi` already points at a view whose aspect can be rewritten:

```
0x441A143: 8B 83 B0 02 00 00      mov   eax, [rbx+0x2B0]        ; component AspectRatio
0x441A149: 89 47 48               mov   [rdi+0x48], eax         ; view.AspectRatio = it
```

The 7-byte `movzx` becomes `call caveA` + 2-byte nop:

```
caveA: 0F B6 83 B4 02 00 00   movzx eax, byte [rbx+0x2B4]   ; original instruction
       8B 8B B0 02 00 00      mov   ecx, [rbx+0x2B0]        ; component AspectRatio
       81 F9 00 00 E0 3F      cmp   ecx, 1.75f              ; IEEE754 order == integer order
       76 12                  jbe   done
       81 F9 <display*1.002>  cmp   ecx, upper bound
       73 0A                  jae   done
       83 E0 FE               and   eax, -2                 ; clear bConstrainAspectRatio
       C7 47 48 39 8E E3 3F   mov   dword [rdi+0x48], 1.7777778f   ; pin the divisor
done:  C3                     ret
```

Every camera authored **narrower than the display** is unconstrained and renders Hor+ - the cutscene, dialogue, menu and free-roam cameras alike. Square capture cameras (~1.0) fall below the window and keep their constraint and classic behaviour, which is what protects the photo pipeline. `ecx` is reloaded immediately after the patched site, so nothing is clobbered; the cave uses no stack and needs no alignment.

**The divisor pin is the part that matters most**, and it is not obvious from static reading. The game *animates* a camera's `AspectRatio` from the authored 16:9 up to the viewport aspect when it hands control back from a dialogue - its letterbox-open animation. Under the forced `MaintainYFOV` branch that member is the FOV divisor, so without the pin the animation is re-read as a vertical-FOV change: a zoom-in while the ramp climbs, a pillarbox sweep if the ramp leaves the gate window, and a framing snap when the gameplay camera takes over. Writing the authored aspect back into `[rdi+0x48]` restores the rule from 2a - the divisor must be the aspect the FOV was authored for. Full measurements in section 10.

The gate's upper bound is `display aspect * 1.002` rather than the display aspect exactly, so the ramp's endpoint stays inside the window despite float rounding and any easing overshoot.

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

### 3a. Patched Sites and Reference Functions

The first two rows are **no longer patched** as of v9 (see 1 and 4); they are kept here because they remain useful landmarks.

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

Monitor-aspect values, retained for the legacy `clean`/`full` modes and because cave A's gate bound is derived from the same ratio:

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

UE compiles a static projection float table at `0x69C8A8C` (`DF 7C DB 3D 55 55 55 3F 39 8E E3 3F`, where the last dword is the 16:9 aspect). The photo render matrix samples this table rather than the live viewport.

**Corrected in v9.** Earlier releases patched this table's aspect to the monitor ratio, together with the player-camera constant at `0x23E665C`, on the reasoning that the photo view and the capture matrix have to agree. Both are now left **stock**, and the photo pipeline is correct precisely because of it: the capture matrix stays bit-identical to vanilla, and the square capture cameras (~1.0) sit below cave A's gate so they keep their constraint and classic projection. Field-verified - Polaroids and the in-game camera behave normally with both constants untouched. See section 10 for the measurements that overturned the original reasoning.

---

## 5. The Loading Side-Peek (RESOLVED)

### Analysis
During loading transitions, narrow strips of the still-streaming world can appear briefly at the screen sides. Field triangulation across gate variants (see git history) established that the loading view is **the destination scene's own cutscene-class camera held behind a loading overlay UI that does not cover the extra width**. The wide camera renders correctly. Because the very same camera renders the (wanted-wide) cutscene a moment later, no camera-identity or aspect gate can pillarbox loading without regressing cutscenes - it is a UI problem, not a camera problem.

**Corrected 2026-08-30 (see section 9):** the earlier assumption that the overlay widget is *itself* authored/anchored at 16:9 is **false**. `BP_LoadingWindow`'s `BlackScreen` image sits in a `CanvasPanelSlot` with anchors `(0,0)-(1,1)`, offsets `(0,0,0,0)` - full viewport stretch. `BP_TransitionWindow`'s `FullscreenImage` is identical. Those widgets would cover an ultrawide viewport correctly. The 16:9 boxing is applied **upstream of the widgets**, at the UI host, and it affects every window (loading, main menu, pause, notifications) uniformly - so it is one shared cause, not a per-asset defect.

### Candidate Solutions (Future Work)
1. **Streaming mitigation (`Engine.ini`, low risk, partial):** shrink the ugly window so side content appears already-loaded (still visible, but finished): `r.Streaming.PoolSize=4096`, `r.Streaming.FramesForFullUpdate=1`, optionally `s.AsyncLoadingTimeLimit=10`.
2. **Loading overlay widget fix (ruled out, not blocked):** the containers are *not* encrypted and the overlay asset has been read (section 9). The overlay already stretches to the full viewport, so there is nothing to fix in the asset. Superseded by candidate 6.
3. **Minimal runtime mask (complete fix, needs opt-in):** a load-event-scoped mod (e.g. UE4SS) that, during the load window only, writes the held camera's `AspectRatio` member outside cave A's (1.75, 1.8) window (e.g. 1.9f) and restores it once the level is interactive. One write per load; no per-frame contention with the caves (unlike naive per-tick flag toggling, which visibly fights the game's Blueprint camera system).
4. **Native temporal gate (advanced):** extend cave A with a "recently loaded" check - needs a writable storage slot (new RW PE section; `.text` is R-X), a reset signal hooked into a once-per-load code path, and a time source such as `GFrameCounter`. Feasible but disproportionate.
5. **Diagnostics:** a recorder cave appending (vtable, AspectRatio, constrain) tuples per view into an RW ring buffer, dumped externally via `ReadProcessMemory` - maps camera classes to on-screen moments with certainty, without any runtime mod inside the game.
6. **UI host width (SOLVED - see 9c/9c-1):** `BP_UIWindowManager`'s `WindowParent` slot is a fixed 3840x2160 centred box that clips every window to 16:9. Widening it to `2160 * monitorAspect` makes the loading overlay cover the side strips by itself and puts right-anchored HUD elements on the real screen edge - both open issues close at once. Ships as an IoStore mod, not an exe patch.
7. **Native temporal gate, revised:** section 5.4 assumed a `GFrameCounter` heuristic and a new RW PE section, and was judged disproportionate. Two facts soften it: `UChronosLoadingWindow` is a *native* class, so its construct/destruct is a clean, exact load-window signal rather than a heuristic; and `.data` alignment padding is already writable, so no new PE section is needed - one flag byte plus two small caves, with cave A consulting the flag. Still more RE work than candidate 6, but it is the exe-only route if shipping assets is undesirable.

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
9. **Patching `FMinimalViewInfo::BlendViewInfo`'s flag merge** (`0x4408E19`): the function does lerp `AspectRatio` and merge `bConstrainAspectRatio` with `|=`, so a view-target blend really is constrained from weight 0 while the aspect is still 16:9. Rewriting the merge to `AND` was correct in itself and had **zero observable effect** - the constraint is re-asserted per frame from the component, so the blended value never survives. A good reminder that a real mechanism is not automatically the active one.
10. **Chasing the dialogue-exit zoom statically** (three iterations - intrinsic Hor+/Vert- framing difference, the blend flag merge above, then the patched `0x23E665C` constant as the ramp target). All three were wrong; see section 10d. The system is *animated*, and no amount of disassembly showed that. One runtime capture did.
11. **SUWSF in-memory patching for this fix**: SUWSF cannot express patch-time-computed code caves and rel32 displacements; its float-value patches also can't implement conditional logic. (Historical note: SUWSF's `Value="auto"` doesn't parse for floats; `Value="aspectratio"` works for plain constants.)

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

1. **`patcher.py`** - the installer and the single source of truth for everything this fix writes. Four independent parts, all on by default: the executable camera patch, the full-width UI container patch (delegated to `tools/assetdump/`), and two `Engine.ini` tweaks (chromatic aberration off, anti-blur TSR settings) written as one removable managed block. `--restore` undoes all of it byte-for-byte. Carries PEP 723 inline metadata, so `uv run patcher.py` needs no virtualenv and fetches `blake3` itself - though nothing requires it: the standard library alone is enough (see `blake3_pure.py` below). Signature-scan fallback for game updates; always patches from the pristine `.original` backup, so re-runs never stack.
   Advanced: `--mode` patches only the executable, in one of `cine` (shipped behaviour), `horplus`, `hybrid`, `clean`, `full`, `stock`.
2. **`LiSUltrawidePatcher.exe` (& `.cs`)** - Windows GUI (WinForms; buildable with the stock .NET Framework `csc.exe`). It also guarantees an interpreter: uv, then `py`, then `python`, and failing all three it downloads python.org's embeddable build (~11 MB zip, ~22 MB unpacked) into `tools/python/` and runs the patcher with that - no installer, no `PATH` change, removed with the fix. A **thin front-end only**: it finds the game (its own folder, then Steam libraries, Epic manifests and the usual game roots - the same order `find_exe()` uses), detects the display, shows the four options as checkboxes, and shells out to `patcher.py` (preferring `uv run`, then `py`, then `python`), streaming its output. The stock / already patched / unrecognised badge under the path is `patcher.py --check-exe` run in the background, so the verdict comes from the same signature table the patcher writes with. It contains no patch logic. An earlier version reimplemented the byte patches in C# and silently drifted out of sync - keeping one implementation is deliberate.
3. **`tools/assetdump/`** - the IoStore/Zen container reader and the UI layout patcher (section 9), plus **`blake3_pure.py`**: BLAKE3 in the standard library alone, ~200x slower than the Rust extension and irrelevantly so, since a run hashes about a dozen widget packages of a few dozen KB. It exists so the full-width UI step cannot depend on installing a compiled wheel - verified against the spec's test vectors and differentially against the `blake3` module over random inputs (`python blake3_pure.py`).
4. **`tools/make_icon.py`** - regenerates `LiSUltrawidePatcher.ico` (7 sizes, 16-256 px) with no imaging dependencies: shapes are supersampled by hand and written as PNG-compressed ICO entries via `zlib` and `struct`.

### 8a. Where the Installer Looks for the Game

Neither entry point cares where it is run from - `find_exe()` in `patcher.py` and `FindGameExe()` in the GUI look in the same order:

1. next to the installer itself, and next to the current folder (`Chronos/Binaries/Win64/`, up to two levels up);
2. **every Steam library on the machine** - the Steam installations named in the registry and the usual `Program Files` locations, then each library folder listed in their `libraryfolders.vdf`, with the game's folder name read from `appmanifest_1874000.acf`;
3. the **Epic Games Launcher** manifests in `%PROGRAMDATA%\Epic\EpicGamesLauncher\Data\Manifests`;
4. the usual game roots on every fixed drive (`Games`, `Program Files`, `Program Files (x86)`, `GOG Games`, `Epic Games`, `SteamLibrary\steamapps\common`), including any folder whose name looks like the game's.

Nothing is ever scanned recursively, so this costs a few milliseconds. On Linux, the Steam Deck and macOS the same search covers `~/.steam`, `~/.local/share/Steam` and the Flatpak Steam data folder, which is where Proton installs live. The tool reports which of the four found it, and you can always override the result - **Browse** in the GUI, `--exe` on the command line.

---

## 9. Asset Pipeline and the UI Layer

Everything in this section was verified on 2026-08-30 by reading the shipped containers directly. The reader used is in `tools/assetdump/` (pure Python; the only external dependency is an Oodle decompressor DLL).

### 9a. The Containers Are Not Encrypted

Section 5 previously stated that the IoStore directory index is encrypted and that asset paths are unrecoverable without an AES key. **That is wrong.** Decoded `FIoStoreTocHeader` for `pakchunk0-Windows.utoc`:

```
Version                5
TocEntryCount          56411
CompressionBlockCount  675076        CompressionMethod = Oodle
DirectoryIndexSize     2550433
ContainerFlags         0x09  = Compressed(0x01) | Indexed(0x08)
                             ... Encrypted(0x02) NOT set, Signed(0x04) NOT set
EncryptionKeyGuid      00000000-0000-0000-0000-000000000000
```

The directory index parses as plaintext with no key: **50,983 asset paths from `pakchunk0`, 8,843 from `pakchunk1`**. `pakchunk0-Windows.pak` (UE pak version 11) likewise carries a plaintext index and holds the cooked `.ini` config.

The real obstacle was never encryption - it is that UE 5.2 links Oodle statically into the shipping exe, so no `oo2core_*.dll` ships with the game. A standalone decompressor from Epic's Oodle-for-UE source build (`WorkingRobot/OodleUE`, `bin/oodle-data-shared.dll`) supplies `OodleLZ_Decompress` and everything opens. Containers are also unsigned, and UE mounts `.pak`/`.utoc` recursively under `Content/Paks`, so loose asset mods mount natively - this install already carries one (`Content/Paks/Mods/PSButtons-3161.*`).

Cooked packages use unversioned property serialization (`PackageFlags=0x80002200` = `FilterEditorOnly|UnversionedProperties|Cooked`, `bHasVersioningInfo=0`), so *general* property editing needs a `.usmap` (UE4SS `dumpusmap` or Dumper-7). Package structure - name map, imports, exports, class names, outer chains - decodes without one, and so do individual structs whose schema order is known.

### 9b. Verified Widget Layout

`BP_LoadingWindow` (`Chronos/Content/UI/BP/Window/`), full export map: root `D9CanvasPanel` under `WidgetTree`, children `BlackScreen` (`UMG.Image`) and `LoadingVisuals` (`D9CanvasPanel`), the latter holding `D9Image`, `D9RichTextBlock` and `AnimatedImage`. **No `ScaleBox`, `SizeBox` or `SafeZone` anywhere.** Decoded `UCanvasPanelSlot` layout data:

| Parent | Child | Anchors | Offsets | Alignment |
|---|---|---|---|---|
| `D9CanvasPanel` | `BlackScreen` | (0,0)-(1,1) | 0,0,0,0 | 0,0 |
| `D9CanvasPanel` | `LoadingVisuals` | (0,0)-(1,1) | 0,0,0,0 | 0,0 |
| `LoadingVisuals` | `D9Image` | (0,0)-(1,1) | 0,0,0,0 | 0,0 |
| `LoadingVisuals` | `D9RichTextBlock` | (0,1)-(0,1) | 374,-174,100,100 | 0,1 |
| `LoadingVisuals` | `AnimatedImage` | (0,1)-(0,1) | 220,-140,97,136 | 0,1 |

The black plate is a full-viewport stretch. `BP_TransitionWindow`'s `FullscreenImage` is the same. Neither asset can be the reason the sides are exposed.

`BP_NotificationWindow` is equally innocent: `MainPanel -> NotificationPanel` at anchors `(1,0)-(1,0)`, alignment `(1,0)` - anchored and aligned to the **right edge of the viewport**, not to any fixed coordinate. `BP_Notification_SMS` likewise pins its content with `(1,*)` anchors. If the UI host spanned the viewport, these would already sit on the physical right edge at any aspect ratio.

`BP_PauseWindow` fixes the design canvas: `MainButtons -> Pause` carries offsets `(1920, 590, 300, 80)` at anchor `(0,0)` with alignment `(0.5,0)`, and `VignetteTop`/`VignetteBottom` split at `Y=1080`. **The UI is authored on a 3840x2160 canvas** (1920 = its horizontal centre, 1080 = its vertical centre).

### 9c. Where the 16:9 Boxing Comes From - SOLVED

`Chronos/Config/DefaultEngine.ini` (extracted from `pakchunk0-Windows.pak`):

```ini
[/Script/Engine.UserInterfaceSettings]
UIScaleRule=ScaleToFit
DesignScreenSize=(X=3840,Y=2160)
UIScaleCurve=(... ExternalCurve=CurveFloat "/Game/UI/Data/FC_UI_DPI_Scale.FC_UI_DPI_Scale")
```

`FC_UI_DPI_Scale` decodes to keys `(0, 0.25) (620, 0.5) (1080, 0.5, constant) (1440, 1.0) (2160, 1.0)`.

This is **not** the cause. `EUIScalingRule::ScaleToFit` computes `min(W/3840, H/2160)` and ignores the curve; at 5120x2160 that is `min(1.333, 1.0) = 1.0`, confirmed at runtime (`viewportSize=5120x2160  dpiScale=1.0`). A DPI scale of 1.0 makes UMG's design space equal the real viewport, so config overrides cannot move anything - and because the rule is height-bound, the scale is 1.0 at *every* aspect wider than 16:9. Config is ruled out. So is `UChronosUISceneTextureSubsystem`, whose entire reflected surface is a single `MaterialParamColl` object - it feeds material parameters, it does not composite the UI through a render target. A keyword sweep of the whole `.usmap` also confirms **no Deck Nine class has any aspect, safe-zone, letterbox or design-size property**.

The cause is a single hardcoded slot in `Chronos/Content/UI/BP/BP_UIWindowManager.uasset`:

```
WidgetTree.RootWidget = ScaleBox        (Stretch not serialized -> CDO default, ScaleToFit)
  \- WindowManager (UMG.CanvasPanel)
       |- GameStyle
       |- WindowParent (D9Runtime.D9CanvasPanel)
       |       anchors = (0.5,0.5)-(0.5,0.5)   alignment = (0.5,0.5)
       |       offsets = (0, 0, 3840, 2160)             <-- THE BUG
       |    |- InputBlocker    anchors (0,0)-(1,1) offsets 0        full stretch
       |    |- SharedBGPanel   anchors (0,0)-(1,1) offsets 0        full stretch
       |    |    \- Darken / BackgroundBlur / ModalDarkenBG        full stretch
       |    \- BuildInfoLabel anchors (1,0)-(1,0) alignment (1,0)
       \- WatermarkLabel
```

`WindowParent` is the `D9UIWindowManager::WindowParent` member - the panel **every** game window is reparented into at runtime. A point-anchored slot with alignment `(0.5,0.5)` and offsets `(0,0,3840,2160)` is a **fixed 3840x2160 box centred in the viewport**. At 5120x2160 with DPI scale 1.0 that leaves 640 px of dead space on each side, and every window inside it - loading, transition, main menu, pause, notifications - is clipped to exactly 16:9 no matter how correctly its own slots are anchored. One slot explains every symptom.

The root `ScaleBox` reinforces it: with the default `ScaleToFit` it scales its content by the content's own desired size, which `WindowParent`'s fixed 3840x2160 supplies.

### 9c-1. The Fix and Its Trade-off

The design space is always `2160 * aspect` wide by 2160 tall for any aspect at or above 16:9 (the `ScaleToFit` DPI rule is height-bound). So the minimal, mechanism-preserving change is **one float** in `WindowParent`'s `CanvasPanelSlot`:

```
offsets.Right : 3840.0  ->  2160 * monitorAspect
                            5120  @ 21.33:9      5160  @ 3440x1440
                            7680  @ 32:9         6912  @ 32:10
```

That keeps the existing centred-box mechanism intact and simply sizes the box to the real design space, so `ScaleToFit` resolves to scale 1.0 filling the viewport exactly. Full-stretch children (the loading `BlackScreen`, `SharedBGPanel`) then cover the whole screen, and edge-anchored children (`NotificationPanel` at `(1,0)`, `BuildInfoLabel` at `(1,0)`) land on the physical screen edge.

**Trade-off to test:** widening `WindowParent` moves its local origin 640 px left, so any descendant positioned by *absolute offsets* rather than fractional anchors shifts with it. `BP_PauseWindow`'s `MainButtons -> Pause` is a known example - anchor `(0,0)`, offsets `(1920, 590, 300, 80)`, alignment `(0.5,0)` - i.e. centred by hardcoding half of 3840. After the change it would sit 640 px left of centre. Elements using fractional anchors (the great majority, including `MainButtons -> D9VerticalBox` at `(0.5,0.5)`) are unaffected. The blast radius is enumerable statically: scan every `BP_*Window` for point-anchored slots whose offsets cluster near 1920/3840 and adjust that handful.

### 9c-2. Blast-Radius Audit

`tools/assetdump/audit_offsets.py` walks every widget package under `Chronos/Content/UI/`, decodes each `UCanvasPanelSlot`, resolves the in-package ancestor chain, and reports the slots whose horizontal placement is tied to the 3840 canvas. Result over **197 packages / 1847 slots**, for a 5120x2160 target (origin shifts 640 px left):

| Class | Count | Meaning | Action |
|---|---|---|---|
| `HIGH` | 1 | centred by hardcoding half of 3840 | fix |
| `FIXW` | 6 | fixed 3840-wide and *not* centred - stops spanning the widened parent | fix |
| `MED` | 108 | positioned by an absolute X on the 3840 canvas | mostly covered by one upstream change, see below |
| `BOX` | 31 | a deliberate, *centred* 3840x2160 box | none - stays a 16:9 island exactly as today |

The `BOX` class is the key discovery: **the centred 3840x2160 slot is a recurring Deck Nine idiom**, used 38 times across the UI (`BP_SettingsWindow`, all seven `BP_KeybindingSettings*`, the `BP_PlayerChoices*` family, `BP_PhotographyWindow` backgrounds, and three of the six player-menu tabs). Those screens are self-protecting: widening the master `WindowParent` leaves them centred 16:9 islands, i.e. visually unchanged.

The 7 slots that genuinely need a value change:

```
[HIGH] BP_PauseWindow             MainButtons -> Pause        anchor(0,*) align(0.5,*) offsets=(1920,590,300,80)
[FIXW] BP_SettingsWindow          MainPanel   -> Background                 fixed 3840x2162 at (0,0)
[FIXW] BP_SaveSelectWindow        MainPanel   -> D9Image                    fixed 3840x2160 at (0,0)
[FIXW] BP_SquareEnixAccountWindow MainPanel   -> CanvasPanel_Background     fixed 3840x2162 at (0,0)
[FIXW] BP_SquareEnixAccountWindow MainPanel   -> WidgetSwitcher_CurrentView fixed 3840x2162 at (0,0)
[FIXW] BP_UISettings              MainPanel   -> Buttons                    fixed 3840x2160 at (0,0)
[FIXW] BP_CollectiblePosterUI     ReadPanel   -> D9Image                    fixed 3840x2160 at (0,0)
[FIXW] BP_ShiftChoiceUI           ChoicesMenu -> ChoiceButton               fixed 3840x2160 at (0,1)
```

Each is a one-value edit: convert `HIGH` to a fractional anchor (`anchorX 0 -> 0.5`, `offsets.Left 1920 -> 0`), and convert each `FIXW` to a horizontal stretch (`anchorMax.X 0 -> 1`, width offset -> 0).

**93 of the 108 `MED` findings live under `BP/Controls/PlayerMenu/`** - journal pages and menu tabs. `BP_PlayerMenuWindow`'s `ContentPanel` already boxes three of its six tabs (`ObjectivesTabUI`, `CluesTabUI`, `SocialMediaTabUI` at the centred 3840x2160 idiom) while `JournalTabUI`, `SMSTabUI` and `CollectiblesTabUI` are full-stretch. Giving those three the same centred box their siblings already have is **three slot edits that neutralise the whole 93** - and it matches the game's own convention rather than inventing one.

That leaves roughly a dozen individually-placed `MED` slots (7 in `BP/Window`, 6 button controls, 2 player-choice controls) to eyeball in-game after the change.

**Caveat on the numbers:** `chain_preserves_width` resolves ancestors only *within* a package. A control whose host in another package is itself a fixed box does not move, so `MED` is an upper bound; the `BOX` inventory above is what resolves most of those cases by hand.

There is no exe-side equivalent - the value lives in cooked asset data, so this is the one part of the fix that cannot be a byte patch. See 9c-3 for how it is actually delivered.


### 9c-3. Implementation - `tools/assetdump/patch_ui_layout.py`

Delivered as an in-place container patch rather than a loose IoStore mod: writing a valid patch container means authoring an `FIoContainerHeader` with package store entries, whereas every edit needed here rewrites an *existing* float, so package sizes never change and the container can simply be repointed.

Method, per modified package:

1. read and decompress the package chunk, decode the target `UCanvasPanelSlot` (located by the widget its `Content` points at, never by hardcoded offsets, so it survives game updates);
2. verify the current value matches the expected old value, and that the float occurs exactly once in the slot payload;
3. write the new float, append the whole chunk to the end of the `.ucas` as a new **uncompressed** block (compression method index 0 is always valid);
4. repoint that chunk's compression-block entry in the TOC (offset / size / method);
5. recompute the chunk's meta hash - **BLAKE3 truncated to 20 bytes**, identified by reproducing a stock chunk's stored hash exactly;
6. re-read every edited slot back through the container reader as a verification pass.

Nothing existing is ever overwritten. `.utoc` is copied to `.utoc.original` and the original `.ucas` length is recorded, so `--restore` is exact and re-runs always start from the pristine container.

The 15 edits, all derived from the design space rather than hardcoded:

| Package | Slot | Field | Change |
|---|---|---|---|
| `BP_UIWindowManager` | `WindowParent` | Right | `3840 -> designW` |
| `BP_PauseWindow` | `Pause` | Left | `1920 -> designW/2` |
| `BP_SettingsWindow` | `Background` | Right | `3840 -> designW` |
| `BP_SaveSelectWindow` | `D9Image` | Right | `3840 -> designW` |
| `BP_SquareEnixAccountWindow` | `CanvasPanel_Background`, `WidgetSwitcher_CurrentView` | Right | `3840 -> designW` |
| `BP_UISettings` | `Buttons` | Right | `3840 -> designW` |
| `BP_CollectiblePosterUI` | `D9Image` | Right | `3840 -> designW` |
| `BP_ShiftChoiceUI` | `ChoiceButton` | Right | `3840 -> designW` |
| `BP_MainMenuWindow` | `MainButtons`, `D9Image`, `GamerTag` | Left | `+ (designW-3840)/2` |
| `BP_MainMenuWindow` | `InfocastPanel` | Left | `- (designW-3840)/2` |
| `BP_TitleWindow` | `GamerTag`, `PressAnyKey` | Left | `+ (designW-3840)/2` |

The main-menu and title screens are full-bleed compositions with no 3840 box to widen - their elements are inset from the box edges, so widening dragged them onto the physical screen edge. Re-inseting restores Deck Nine's authored framing; this is a deliberate choice to keep those two screens 16:9-composed rather than a limitation.

**Field-verified at 5120x2160:** loading overlay covers the full width (side-peek gone), phone notifications sit on the physical right edge, pause title centred, main menu back to its authored 16:9 composition. The three full-stretch player-menu tabs (`JournalTabUI`, `SMSTabUI`, `CollectiblesTabUI`) are *not* patched - boxing them the way their three siblings already are would need a structural edit (adding serialized properties, which changes package size). They are the known remaining gap in this pass.

### 9d. Binary Reference - UI Classes

Native window classes recovered from the exe's UTF-16 string table: `UUIWindow`, `UOverlayUIWindow`, `UModalUIWindow`, `UUIObjectWindow`, `UUIWindowLogic`, `UIWindowProps`, `UD9UIWindowManager`, `UChronosUIWindowManager`, `UChronosUISceneTextureSubsystem`, plus one `UChronos*Window` / `U*Window` pair per screen (`UChronosLoadingWindow`, `UChronosNotificationWindow`, `UChronosMainMenuWindow`, `UChronosPauseWindow`, ...). `UScaleBox`, `USafeZone` and their slots are linked in but unused by the windows inspected so far.

### 9e. Obtaining the Oodle Decompressor

The game's data files are 97% Oodle-compressed, and Oodle ships *statically linked* inside the game executable, so reading a package needs a standalone decompressor. It cannot be bundled with this fix - it is proprietary, and redistributing it is governed by the Unreal Engine EULA.

`patcher.py` resolves this itself, in order:

1. a copy already in `tools/assetdump/`;
2. one shipped by **another Unreal Engine game on the machine** - most UE titles carry `oo2core_*_win64.dll`, which exports the same entry point, so nothing is downloaded;
3. otherwise Epic's Oodle-for-UE build (~7 MB), downloaded from [WorkingRobot/OodleUE](https://github.com/WorkingRobot/OodleUE).

Only step 3 touches the network, and only when the full-width UI step is actually going to run and the first two came up empty; the DLL lands in `tools/assetdump/` and is reused from then on. `--no-fetch-oodle` turns the download off, which skips just that one step.

## 10. Runtime Camera Measurement - The Letterbox Ramp

Sections 2-9 were derived statically. The dialogue-exit zoom resisted that approach through three wrong hypotheses, so it was settled by measurement instead: a read-only UE4SS Lua mod sampling `APlayerCameraManager` every frame and logging `ViewTarget.Target`, `ViewTarget.POV.{FOV, AspectRatio, bConstrainAspectRatio, Location}` and `CameraCachePrivate.POV` (the finished, post-blend view that reaches the renderer). It hooked nothing and wrote nothing back. **Debug only - it is not part of the shipped fix.**

`FMinimalViewInfo` and `FTViewTarget` are fully reflected, so all of the above is readable from Lua. (`FGeometry`, used for the UI work in section 9, is not - hence that being done from static asset reads.)

### 10a. What a dialogue exit actually does

Compressed capture of one hand-off, with the vertical FOV derived as `2*atan(tan(FOV/2)/aspect)`:

```
rows      view target               aspect     con     FOV h            vFOV
348-352   BP_ChronosCameraPawn_C    1.77778    false   35.39 -> 65.08   20.35 -> 39.49
353-411   BP_ChronosCameraPawn_C    1.778 .. 2.370  false   65.09 -> 72.31   39.43 -> 34.27
412-481   BP_ChronosCameraPawn_C    2.37037    true    72.39 -> 75.00   34.32 -> 35.88
482-507   BP_Max_Coat01A_C          1.77778    false   75.00            46.69   <-- +10.8 deg snap
```

Three facts fall out of this and none of them were guessable:

1. **The camera's `AspectRatio` member is animated**, over roughly a second, from the authored 16:9 up to the **viewport** aspect. It is not a blend between two static cameras and it is not read from any constant we patch - the capture above was taken with both aspect constants at their stock 16:9 values and the ramp still ended at `2.37037` on a 5120x2160 display.
2. **The horizontal FOV is identical at both ends of the hand-off** (75.00 on the camera pawn, 75.00 on the gameplay camera). The entire visible artifact was the aspect.
3. **Free-roam exploration runs at `1.77778` unconstrained** - it was already rendering Hor+ through cave A, and had been since cave A was introduced. The documentation claiming "exploration constrained at monitor aspect" was wrong.

### 10b. Why it looked like two different bugs

The ramp is the game's **letterbox-open animation**: it widens the cinematic frame out to the screen when returning control. On a 16:9 display it is a no-op, because the authored aspect already equals the viewport aspect. In stock UE on an ultrawide display it is still harmless - a constrained camera takes the `MaintainXFOV` path, where `AspectRatio` only sizes the view *rect*.

Under the forced `MaintainYFOV` branch (2b), `AspectRatio` becomes the FOV **divisor**. The same animation then produced two symptoms that looked unrelated:

- while the ramp was inside cave A's old `(1.75, 1.8)` window the camera was unconstrained, and the climbing divisor narrowed the vertical FOV - **a zoom-in**;
- the moment the ramp crossed `1.8` the camera fell *out* of the window, the constraint came back at an aspect well below the viewport, and the view **pillarboxed**, the bars then shrinking to nothing as the ramp reached the viewport aspect;
- and when the gameplay camera finally took over at 16:9, the framing **snapped** by +10.8 degrees.

### 10c. The fix

Cave A now does two things for every camera it unconstrains: clears `bConstrainAspectRatio` as before, **and pins the view's `AspectRatio` to the authored `1.7777778`**, writing it into `[rdi+0x48]`. That is safe because `GetCameraView` copies the component's aspect into the output view immediately *before* the patched site:

```
0x441A143  mov eax, [rbx+0x2B0]     ; component AspectRatio (the animated value)
0x441A149  mov [rdi+0x48], eax      ; view.AspectRatio = it
0x441A14C  call caveA               ; rdi still points at the view
```

The gate's upper bound also moves from a fixed `1.8` to `display aspect * 1.002`, so the whole ramp - endpoint included - stays inside the window instead of falling out of it mid-animation.

This restores the rule already stated in 2a: **the divisor must be the aspect the FOV was authored for.** The letterbox animation moves `AspectRatio` away from that authored value; pinning puts it back. Cameras below the gate (square capture cameras, ~1.0) are untouched, which is why the photo pipeline is unaffected.

### 10d. Hypotheses this replaced

Recorded because each looked convincing and each was wrong - the sequence is a fair warning about static-only reasoning on an animated system.

1. *"It is the intrinsic Hor+ / Vert- framing difference between cinematic and exploration cameras."* Wrong: exploration was never Vert-.
2. *"`FMinimalViewInfo::BlendViewInfo` merges `bConstrainAspectRatio` with `|=`, so a blend is constrained at weight 0 while the aspect is still 16:9."* The mechanism is real - the OR is at `0x4408E19`, and `AspectRatio` genuinely is lerped there - but changing it to `AND` had **no observable effect**, because the constraint is re-asserted per frame from the component rather than surviving in the blended view. Not the cause.
3. *"The ramp converges on the constant we patched at `0x23E665C`."* Wrong: reverting both constants to stock left the ramp ending at the same `2.37037`. The target is the viewport aspect.

Only the fourth attempt - measuring instead of inferring - produced the answer, and it took a single capture to do it.

---

## 11. Possible Improvements

- **Player-menu tabs:** three full-stretch tabs (`JournalTabUI`, `SMSTabUI`, `CollectiblesTabUI`) are not repositioned by the UI patch (section 9c). Giving them the centred box their three siblings already have needs a *structural* package edit - adding serialized properties changes the package size - which the in-place float patcher deliberately does not support.
- **Per-shot aspect variants:** if a specific cinematic ever appears pillarboxed, its camera is authored at an aspect outside cave A's gate window (section 2c); the gate can be extended per report.

