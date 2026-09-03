//! The primary display's resolution: the same call the loader uses on
//! Windows, so the two agree; `xrandr` on Linux when it is there.
//!
//! A missing detection is not an error: the loader reads the display itself
//! at launch, and only the UI container needs a resolution, which the
//! prompt or `--width`/`--height` supplies.

#[cfg(windows)]
mod win {
    pub const ENUM_CURRENT_SETTINGS: u32 = 0xFFFF_FFFF;
    /// sizeof(DEVMODEW); the two fields needed are read by offset.
    pub const DEVMODE_SIZE: usize = 220;
    pub const DEVMODE_SIZE_AT: usize = 68;
    pub const DEVMODE_WIDTH_AT: usize = 172;
    pub const DEVMODE_HEIGHT_AT: usize = 176;

    #[link(name = "user32", kind = "raw-dylib")]
    unsafe extern "system" {
        pub fn EnumDisplaySettingsW(device: *const u16, mode: u32, devmode: *mut u8) -> i32;
    }
}

/// The primary display's current mode, in physical pixels whatever the DPI
/// awareness of the process.
#[cfg(windows)]
pub fn detect_resolution() -> Option<(u32, u32)> {
    let mut dm = [0u8; win::DEVMODE_SIZE];
    dm[win::DEVMODE_SIZE_AT..win::DEVMODE_SIZE_AT + 2].copy_from_slice(&(win::DEVMODE_SIZE as u16).to_le_bytes());
    if unsafe { win::EnumDisplaySettingsW(core::ptr::null(), win::ENUM_CURRENT_SETTINGS, dm.as_mut_ptr()) } == 0 {
        return None;
    }
    let at = |o: usize| u32::from_le_bytes(dm[o..o + 4].try_into().unwrap());
    let (w, h) = (at(win::DEVMODE_WIDTH_AT), at(win::DEVMODE_HEIGHT_AT));
    (w > 0 && h > 0).then_some((w, h))
}

/// `xrandr --current` reports `current W x H` for the whole X screen;
/// without xrandr (or under Wayland alone) there is no answer.
#[cfg(not(windows))]
pub fn detect_resolution() -> Option<(u32, u32)> {
    let out = std::process::Command::new("xrandr")
        .arg("--current")
        .stderr(std::process::Stdio::null())
        .output()
        .ok()?;
    parse_xrandr(&String::from_utf8_lossy(&out.stdout))
}

pub fn parse_xrandr(text: &str) -> Option<(u32, u32)> {
    let at = text.find("current ")? + "current ".len();
    let rest = &text[at..];
    let mut it = rest.split_whitespace();
    let w: u32 = it.next()?.trim_end_matches(',').parse().ok()?;
    if it.next()? != "x" {
        return None;
    }
    let h: u32 = it.next()?.trim_end_matches(',').parse().ok()?;
    (w > 0 && h > 0).then_some((w, h))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn xrandr_line() {
        let text = "Screen 0: minimum 320 x 200, current 5120 x 2160, maximum 16384 x 16384\nDP-1 connected primary";
        assert_eq!(parse_xrandr(text), Some((5120, 2160)));
        assert_eq!(parse_xrandr("no displays"), None);
    }

    #[test]
    fn detection_is_sane_when_it_answers() {
        if let Some((w, h)) = detect_resolution() {
            assert!(w > 0 && h > 0);
        }
    }
}
