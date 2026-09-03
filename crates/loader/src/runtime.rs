//! What happens when the game loads the DLL: work out which game this is,
//! settle the display aspect, plan the patch over the mapped image, write
//! it, and leave LiSUltrawideCamera.log next to the DLL saying what happened.

use core::ffi::c_void;
use core::ptr::null;
use std::fmt::Write as _;
use std::os::windows::ffi::OsStringExt;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use lis_ultrawide_core::games::{self, Game};
use lis_ultrawide_core::plan::Plan;
use lis_ultrawide_core::scan::{Image, Section};
use crate::win;
use lis_ultrawide_core::{VERSION, camera, camera_ini, pe};

const INI: &str = "LiSUltrawideCamera.ini";
const LOG: &str = "LiSUltrawideCamera.log";

pub unsafe fn attach(hinst: *mut c_void) {
    let Some(exe) = module_path(core::ptr::null_mut()) else { return };
    let exe_name = exe.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    // Any other process that picked this DLL up gets the forwarding and
    // nothing else, not even a log file.
    let Some(game) = games::game_for_exe(&exe_name) else { return };
    let Some(me) = module_path(hinst) else { return };
    let dir = me.parent().map(Path::to_path_buf).unwrap_or_default();

    let mut log = String::new();
    let _ = writeln!(log, "LiS Ultrawide Fix camera loader {VERSION} - {}", utc_now());
    let _ = writeln!(log, "process: {}", exe.display());
    let _ = writeln!(log, "loaded as {}", me.display());
    let outcome = unsafe { run(game, &dir, &mut log) };
    match outcome {
        Ok(n) => {
            let _ = writeln!(log, "applied {n} writes - the ultrawide camera fix is active");
        }
        Err(e) => {
            let _ = writeln!(log, "not applied: {e} - the game runs unmodified");
        }
    }
    let _ = std::fs::write(dir.join(LOG), &log);
    for line in log.lines() {
        let text = win::wide(&format!("[LiSUltrawideCamera] {line}"));
        unsafe { win::OutputDebugStringW(text.as_ptr()) };
    }
}

unsafe fn run(game: &dyn Game, dir: &Path, log: &mut String) -> Result<usize, String> {
    // The aspect: the installer's ini when the resolution was chosen by hand,
    // the primary display otherwise.
    let settings = std::fs::read_to_string(dir.join(INI))
        .map(|t| camera_ini::parse_ini(&t))
        .unwrap_or_default();
    let upper = if let Some(a) = settings.upper_aspect {
        let _ = writeln!(log, "gate upper bound {a:.4} taken from {INI}");
        camera::aspect_bytes(a)
    } else if let (Some(w), Some(h)) = (settings.width, settings.height) {
        let _ = writeln!(log, "display {w}x{h} from {INI}");
        camera::gate_upper(w, h)
    } else {
        let (w, h) = primary_display().ok_or(format!(
            "could not read the primary display's resolution, and there is no {INI} \
             next to the DLL to give it"
        ))?;
        let _ = writeln!(log, "display {w}x{h} (the primary display)");
        camera::gate_upper(w, h)
    };
    let _ = writeln!(log, "gate upper bound {:.4}", f32::from_le_bytes(upper));

    // The mapped executable: the loader initialised it before running any
    // DllMain, so its sections are readable as they will be executed.
    let base = unsafe { win::GetModuleHandleW(null()) } as *const u8;
    if base.is_null() {
        return Err("GetModuleHandle failed".into());
    }
    let head_len = pe::size_of_headers(unsafe { slice(base, 0x400) })
        .ok_or("the executable's headers are not readable")?;
    let headers = pe::parse(unsafe { slice(base, head_len) })?;
    let _ = writeln!(
        log,
        "image: base {:p}, timestamp {:#x}, size {:#x}",
        base, headers.timestamp, headers.size_of_image
    );
    let plan = {
        let sections = headers
            .sections
            .iter()
            .filter(|s| s.characteristics & pe::EXECUTABLE != 0)
            .map(|s| Section { va: s.va, data: unsafe { slice(base.add(s.va as usize), s.vsize) } })
            .collect();
        let image = Image { sections };
        game.plan_camera(&image, upper)?
        // the borrows of the image end here, before anything is written
    };
    for n in &plan.notes {
        let _ = writeln!(log, "  {n}");
    }

    unsafe { apply(base as *mut u8, &plan) }?;
    for w in &plan.writes {
        let _ = writeln!(log, "  wrote {} bytes at rva {:#x}: {}", w.bytes.len(), w.va, w.what);
    }
    Ok(plan.writes.len())
}

/// Every write or none: the expected bytes are checked again right before
/// writing, and every page is made writable before the first byte changes.
/// Protections go back in reverse order: two writes on one page each saw a
/// different "old" value, and only the first one's is the page's own.
unsafe fn apply(base: *mut u8, plan: &Plan) -> Result<(), String> {
    for w in &plan.writes {
        let now = unsafe { slice(base.add(w.va as usize), w.expected.len()) };
        if now != &w.expected[..] {
            return Err(format!("the bytes at rva {:#x} changed between planning and writing", w.va));
        }
    }
    let mut previous = Vec::with_capacity(plan.writes.len());
    for w in &plan.writes {
        let addr = unsafe { base.add(w.va as usize) } as *const c_void;
        let mut old = 0u32;
        if unsafe { win::VirtualProtect(addr, w.bytes.len(), win::PAGE_EXECUTE_READWRITE, &mut old) } == 0 {
            let err = unsafe { win::GetLastError() };
            for (done, old) in plan.writes.iter().zip(&previous).rev() {
                let addr = unsafe { base.add(done.va as usize) } as *const c_void;
                let mut ignore = 0;
                unsafe { win::VirtualProtect(addr, done.bytes.len(), *old, &mut ignore) };
            }
            return Err(format!("VirtualProtect refused rva {:#x} (error {err})", w.va));
        }
        previous.push(old);
    }
    for w in &plan.writes {
        unsafe { core::ptr::copy_nonoverlapping(w.bytes.as_ptr(), base.add(w.va as usize), w.bytes.len()) };
    }
    for (w, old) in plan.writes.iter().zip(previous).rev() {
        let addr = unsafe { base.add(w.va as usize) } as *const c_void;
        let mut ignore = 0;
        unsafe {
            win::VirtualProtect(addr, w.bytes.len(), old, &mut ignore);
            win::FlushInstructionCache(win::GetCurrentProcess(), addr, w.bytes.len());
        }
    }
    Ok(())
}

unsafe fn slice<'a>(p: *const u8, len: usize) -> &'a [u8] {
    unsafe { core::slice::from_raw_parts(p, len) }
}

fn module_path(module: *mut c_void) -> Option<PathBuf> {
    let mut buf = vec![0u16; 32 * 1024];
    let n = unsafe { win::GetModuleFileNameW(module, buf.as_mut_ptr(), buf.len() as u32) } as usize;
    if n == 0 || n >= buf.len() {
        return None;
    }
    Some(PathBuf::from(std::ffi::OsString::from_wide(&buf[..n])))
}

/// The primary display's current mode, in physical pixels whatever the DPI
/// awareness of the process.
fn primary_display() -> Option<(u32, u32)> {
    let mut dm = [0u8; win::DEVMODE_SIZE];
    dm[win::DEVMODE_SIZE_AT..win::DEVMODE_SIZE_AT + 2]
        .copy_from_slice(&(win::DEVMODE_SIZE as u16).to_le_bytes());
    if unsafe { win::EnumDisplaySettingsW(null(), win::ENUM_CURRENT_SETTINGS, dm.as_mut_ptr()) } == 0 {
        return None;
    }
    let at = |o: usize| u32::from_le_bytes(dm[o..o + 4].try_into().unwrap());
    let (w, h) = (at(win::DEVMODE_WIDTH_AT), at(win::DEVMODE_HEIGHT_AT));
    (w > 0 && h > 0).then_some((w, h))
}

fn utc_now() -> String {
    let secs = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
    let (days, rem) = (secs / 86400, secs % 86400);
    // civil date from days since 1970-01-01 (Howard Hinnant's algorithm)
    let z = days as i64 + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(m <= 2);
    format!("{y:04}-{m:02}-{d:02} {:02}:{:02}:{:02} UTC", rem / 3600, rem % 3600 / 60, rem % 60)
}
