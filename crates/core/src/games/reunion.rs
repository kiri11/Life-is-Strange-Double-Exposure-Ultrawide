//! Life is Strange: Reunion (Iris, UE 5.5.4, Denuvo).
//!
//! Two of Double Exposure's three changes, at the sites RESEARCH.md section
//! 13 documents: the two immediates in the projection function and cave A at
//! the constraint copy in `UCameraComponent::GetCameraView`. There is no
//! cave B: Reunion's cutscenes are cine cameras (`UD9CineCameraComponent`,
//! 13f), and re-asserting the constraint on the cine component's `Super`
//! call boxes every one of them (13h). The registers and offsets differ from
//! Double Exposure (13c, 13e); the executable's layout differs too (13a),
//! which is why the cave goes in the section that holds the sites and
//! nowhere else.

use crate::camera::cave_a_reunion;
use crate::plan::{Plan, Site, Write, locate, rel32};
use crate::scan::{Image, find_cave};
use crate::ui_layout::UiFix;

use super::Game;

pub struct Reunion;

pub static REUNION: Reunion = Reunion;

const AXIS: Site = Site {
    name: "Hor+ projection branch (CalculateProjectionMatrixGivenViewRectangle)",
    sig: "3B C1 7E 06 40 80 FE 02 74 ?? 40 80 FE 01 74 ??",
    expected: 0x3698BD4,
    patched: &["3B C1 7E 06 40 80 FE FF 74 ?? 40 80 FE FF 74 ??"],
};

const GATE: Site = Site {
    name: "GetCameraView flag copy (cave A site)",
    sig: "0F B6 8B 59 02 00 00 33 4F 68 83 E1 01",
    expected: 0x36A687D,
    patched: &["E8 ?? ?? ?? ?? 66 90 33 4F 68 83 E1 01"],
};

/// RESEARCH.md section 13: the branch edit and cave A.
pub fn plan_reunion(img: &Image, upper: [u8; 4]) -> Result<Plan, String> {
    let mut notes = Vec::new();
    let mut writes = Vec::new();

    // 13d: both "cmp sil, <enum>" immediates become 0xFF.
    let axis = locate(img, &AXIS, &mut notes)?;
    writes.push(Write {
        va: axis + 7,
        expected: vec![0x02],
        bytes: vec![0xFF],
        what: "MajorAxisFOV compare disabled".into(),
    });
    writes.push(Write {
        va: axis + 13,
        expected: vec![0x01],
        bytes: vec![0xFF],
        what: "MaintainXFOV compare disabled".into(),
    });

    // 13e: the 7-byte movzx becomes "call caveA ; nop2".
    let gate = locate(img, &GATE, &mut notes)?;
    let blob_a = cave_a_reunion(upper);
    let a = find_cave(img, gate, blob_a.len() + 8, &[])
        .ok_or("no int3 padding run large enough for cave A in the code section")?;
    let mut site_a = vec![0xE8];
    site_a.extend_from_slice(&rel32(a, gate + 5)?.to_le_bytes());
    site_a.extend([0x66, 0x90]);
    writes.push(Write {
        va: a,
        expected: vec![0xCC; blob_a.len()],
        bytes: blob_a,
        what: format!("cave A: aspect-gated unconstrain, upper bound {:.4}", f32::from_le_bytes(upper)),
    });
    writes.push(Write {
        va: gate,
        expected: img.read(gate, 7).ok_or("cave A site is not readable")?.to_vec(),
        bytes: site_a,
        what: "GetCameraView flag copy -> call cave A".into(),
    });

    notes.push(format!("cave A at rva {a:#x}; no cave B for this game (RESEARCH 13e)"));
    Ok(Plan { writes, notes })
}

impl Game for Reunion {
    fn id(&self) -> &'static str {
        "reunion"
    }
    fn title(&self) -> &'static str {
        "Life is Strange: Reunion"
    }
    fn steam_appid(&self) -> u32 {
        2624870
    }
    fn exe_name(&self) -> &'static str {
        "Iris-Win64-Shipping.exe"
    }
    fn project(&self) -> &'static str {
        "Iris"
    }
    fn install_dir(&self) -> &'static str {
        "LifeisStrangeReunion"
    }
    fn folder_hint(&self) -> &'static str {
        "reunion"
    }
    fn plan_camera(&self, image: &Image, gate_upper: [u8; 4]) -> Result<Plan, String> {
        plan_reunion(image, gate_upper)
    }
    /// None until the game's UI packages are known (RESEARCH 13g).
    fn ui(&self) -> Option<&'static UiFix> {
        None
    }
    fn ini_markers(&self) -> (&'static str, &'static str) {
        (
            "; ===== BEGIN LiS:Reunion Ultrawide Fix (managed block - safe to delete) =====",
            "; ===== END LiS:Reunion Ultrawide Fix =====",
        )
    }
}
