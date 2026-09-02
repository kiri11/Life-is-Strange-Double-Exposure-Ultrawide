//! What gets patched, per game.
//!
//! The Double Exposure set is RESEARCH.md sections 1 and 2, byte for byte:
//! the branch edit, cave A and cave B. A planner only decides what to write
//! and where; nothing here touches memory, so the same function runs over
//! the file on disk in the tests, where its output is held against the patch
//! the Python installer used to write.

use crate::hex;
use crate::scan::{Image, Sig, find_cave};

/// One run of bytes to write, and what has to be there before it is.
#[derive(Debug)]
pub struct Write {
    pub va: u64,
    pub expected: Vec<u8>,
    pub bytes: Vec<u8>,
    pub what: String,
}

#[derive(Debug)]
pub struct Plan {
    pub writes: Vec<Write>,
    pub notes: Vec<String>,
}

pub type Planner = fn(&Image, [u8; 4]) -> Result<Plan, String>;

pub struct Game {
    /// The executable's file name; the loader does nothing in any other process.
    pub exe: &'static str,
    pub plan: Planner,
}

pub const GAMES: &[Game] = &[Game { exe: "Chronos-Win64-Shipping.exe", plan: plan_double_exposure }];

pub fn game_for(exe_name: &str) -> Option<&'static Game> {
    GAMES.iter().find(|g| g.exe.eq_ignore_ascii_case(exe_name))
}

/// Cave A's upper bound for a display: its aspect plus a little slack so the
/// letterbox ramp's endpoint stays inside the gate (RESEARCH 2c). The same
/// arithmetic the Python installer used, so the bytes match the reference
/// patch exactly.
pub fn gate_upper(width: u32, height: u32) -> [u8; 4] {
    let ratio = width as f64 / height as f64;
    aspect_bytes((ratio.max(1.8) * 1.002 * 10000.0).round() / 10000.0)
}

pub fn aspect_bytes(aspect: f64) -> [u8; 4] {
    (aspect as f32).to_le_bytes()
}

/// 1.7777778f, the aspect the game's cameras are authored for.
const AUTHORED_ASPECT: &str = "398EE33F";

pub fn cave_a(upper: [u8; 4]) -> Vec<u8> {
    let mut v = hex("0FB683B4020000 8B8BB0020000 81F90000E03F 7612 81F9");
    v.extend_from_slice(&upper);
    v.extend(hex("730A 83E0FE C74748"));
    v.extend(hex(AUTHORED_ASPECT));
    v.push(0xC3);
    v
}

pub fn cave_b(rel_to_super: i32) -> Vec<u8> {
    let mut v = hex("4883EC28 E8");
    v.extend_from_slice(&rel_to_super.to_le_bytes());
    v.extend(hex("4883C428 804F4C01 C3"));
    v
}

struct Site {
    name: &'static str,
    sig: &'static str,
    /// Where it is in the build the fix was written against: tried first,
    /// so a normal launch does no scanning at all.
    expected: u64,
    /// How the site looks once patched, to say so instead of "not found".
    patched: &'static [&'static str],
}

const AXIS: Site = Site {
    name: "Hor+ projection branch (CalculateProjectionMatrixGivenViewRectangle)",
    sig: "3B C1 7E 09 80 FA 02 0F 84 ?? ?? ?? ?? 80 FA 01 0F 84 ?? ?? ?? ??",
    expected: 0x440B5C0,
    patched: &["3B C1 7E 09 80 FA FF 0F 84 ?? ?? ?? ?? 80 FA FF 0F 84 ?? ?? ?? ??"],
};

const GATE: Site = Site {
    name: "GetCameraView flag copy (cave A site)",
    sig: "0F B6 83 B4 02 00 00 33 47 4C 83 E0 01",
    expected: 0x441AB4C,
    patched: &[
        "E8 ?? ?? ?? ?? 66 90 33 47 4C 83 E0 01",
        "31 C0 0F 1F 44 00 00 33 47 4C 83 E0 01",
    ],
};

const CINE: Site = Site {
    name: "cine Super::GetCameraView call (cave B site)",
    sig: "E8 ?? ?? ?? ?? 4C 8B C7 0F 28 CE 48 8B CB E8",
    expected: 0x4006578,
    patched: &[],
};
/// The Super call's E8 within the CINE signature.
const CINE_CALL_AT: u64 = 14;

fn locate(img: &Image, site: &Site, notes: &mut Vec<String>) -> Result<u64, String> {
    let sig = Sig::parse(site.sig);
    if img.read(site.expected, sig.len()).is_some_and(|w| sig.matches(w)) {
        notes.push(format!("{}: at rva {:#x}, where it was expected", site.name, site.expected));
        return Ok(site.expected);
    }
    let hits = sig.find_all(img, 4);
    match hits.len() {
        1 => {
            notes.push(format!("{}: moved to rva {:#x} (a game update?)", site.name, hits[0]));
            Ok(hits[0])
        }
        0 => {
            let patched = site.patched.iter().any(|p| !Sig::parse(p).find_all(img, 1).is_empty());
            if patched {
                Err(format!(
                    "{} is already patched in the executable file itself - an older \
                     version of this fix edited the file. Run the installer again to put \
                     the stock executable back, or use Steam's Verify Integrity of Game Files",
                    site.name
                ))
            } else {
                Err(format!(
                    "{}: not found - this build of the game is not one the fix knows",
                    site.name
                ))
            }
        }
        n => Err(format!(
            "{}: found {} times, so the fix cannot tell which one to patch",
            site.name, n
        )),
    }
}

fn rel32(target: u64, next_instruction: u64) -> Result<i32, String> {
    i32::try_from(target as i64 - next_instruction as i64)
        .map_err(|_| format!("rva {target:#x} is out of rel32 reach from {next_instruction:#x}"))
}

/// RESEARCH.md section 1: the branch edit, cave A and cave B.
pub fn plan_double_exposure(img: &Image, upper: [u8; 4]) -> Result<Plan, String> {
    let mut notes = Vec::new();
    let mut writes = Vec::new();

    // 2b: both "cmp dl, <enum>" immediates become 0xFF, which no real enum
    // value equals, so every perspective camera takes the Hor+ path.
    let axis = locate(img, &AXIS, &mut notes)?;
    writes.push(Write {
        va: axis + 6,
        expected: vec![0x02],
        bytes: vec![0xFF],
        what: "MajorAxisFOV compare disabled".into(),
    });
    writes.push(Write {
        va: axis + 15,
        expected: vec![0x01],
        bytes: vec![0xFF],
        what: "MaintainXFOV compare disabled".into(),
    });

    // 2c: the 7-byte movzx becomes "call caveA ; nop2", and cave A gates the
    // constraint on the camera's authored aspect and pins the FOV divisor.
    let gate = locate(img, &GATE, &mut notes)?;
    let blob_a = cave_a(upper);
    let a = find_cave(img, blob_a.len() + 8, &[])
        .ok_or("no int3 padding run large enough for cave A")?;
    let mut site_a = vec![0xE8];
    site_a.extend_from_slice(&rel32(a, gate + 5)?.to_le_bytes());
    site_a.extend([0x66, 0x90]);
    let a_len = blob_a.len();
    writes.push(Write {
        va: a,
        expected: vec![0xCC; a_len],
        bytes: blob_a,
        what: format!("cave A: aspect-gated unconstrain, upper bound {:.4}", f32::from_le_bytes(upper)),
    });
    writes.push(Write {
        va: gate,
        expected: img.read(gate, 7).ok_or("cave A site is not readable")?.to_vec(),
        bytes: site_a,
        what: "GetCameraView flag copy -> call cave A".into(),
    });

    // 2d: the cine component's Super::GetCameraView call goes through cave B,
    // which re-asserts the constraint so loading views stay boxed.
    let cine = locate(img, &CINE, &mut notes)?;
    let call = cine + CINE_CALL_AT;
    let call_bytes = img.read(call, 5).ok_or("cave B site is not readable")?;
    if call_bytes[0] != 0xE8 {
        return Err("cave B site: the Super call is not a direct call".into());
    }
    let old_disp = i32::from_le_bytes(call_bytes[1..5].try_into().unwrap());
    let super_va = (call as i64 + 5 + old_disp as i64) as u64;
    let b = find_cave(img, 18 + 8, &[(a, a_len)])
        .ok_or("no int3 padding run large enough for cave B")?;
    let blob_b = cave_b(rel32(super_va, b + 4 + 5)?);
    let b_len = blob_b.len();
    writes.push(Write {
        va: b,
        expected: vec![0xCC; b_len],
        bytes: blob_b,
        what: format!("cave B: cine views kept boxed, Super::GetCameraView at rva {super_va:#x}"),
    });
    writes.push(Write {
        va: call + 1,
        expected: call_bytes[1..5].to_vec(),
        bytes: rel32(b, call + 5)?.to_le_bytes().to_vec(),
        what: "cine Super::GetCameraView call -> cave B".into(),
    });

    notes.push(format!("cave A at rva {a:#x}, cave B at rva {b:#x}"));
    Ok(Plan { writes, notes })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hex4;

    #[test]
    fn gate_bound_matches_the_python_installer() {
        // 5120x2160 -> 2.3751, the value in the reference patch
        assert_eq!(gate_upper(5120, 2160), hex4("A3011840"));
        // narrower than 1.8 is treated as 1.8, the old default
        assert_eq!(gate_upper(1920, 1080), aspect_bytes(1.8036));
        assert_eq!(gate_upper(3440, 1440), aspect_bytes(2.3937));
    }

    #[test]
    fn cave_bytes_are_the_documented_ones() {
        assert_eq!(
            cave_a(hex4("A3011840")),
            hex("0FB683B4020000 8B8BB0020000 81F90000E03F 7612 81F9 A3011840 730A 83E0FE C74748 398EE33F C3")
        );
        assert_eq!(cave_b(0x03D6785C), hex("4883EC28 E85C78D603 4883C428 804F4C01 C3"));
    }
}
