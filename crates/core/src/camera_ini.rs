//! `LiSUltrawideCamera.ini`, written by the installer next to the DLL when the
//! resolution was chosen by hand. Without it the loader reads the display.

#[derive(Default, Debug, PartialEq)]
pub struct Settings {
    pub width: Option<u32>,
    pub height: Option<u32>,
    /// The cave A upper bound itself, for research: replaces the value
    /// derived from the width and height when set.
    pub upper_aspect: Option<f64>,
}

pub fn parse_ini(text: &str) -> Settings {
    let mut s = Settings::default();
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with([';', '#', '[']) {
            continue;
        }
        let Some((key, value)) = line.split_once('=') else { continue };
        let value = value.trim();
        match key.trim().to_ascii_lowercase().as_str() {
            "width" => s.width = value.parse().ok(),
            "height" => s.height = value.parse().ok(),
            "upperaspect" => s.upper_aspect = value.parse().ok(),
            _ => {}
        }
    }
    s
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_what_the_installer_writes() {
        let s = parse_ini("; LiS Ultrawide Fix\n[Camera]\nWidth = 5120\nHeight=2160\n# x\n");
        assert_eq!(s, Settings { width: Some(5120), height: Some(2160), upper_aspect: None });
        assert_eq!(parse_ini("UpperAspect=2.4\n").upper_aspect, Some(2.4));
        assert_eq!(parse_ini("Width=wide\n"), Settings::default());
    }
}
