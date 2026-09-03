//! The managed block in the user's `Engine.ini`: chromatic aberration off,
//! and the optional anti-blur TSR settings. Written between two marker
//! lines that are part of the on-disk contract with existing installs, and
//! removed again on restore without touching anything the user wrote.

use std::path::{Path, PathBuf};

use crate::games::Game;
use crate::report::{InstallError, Report, Result, replace_file, write_failure};
use crate::steam;

pub enum Line {
    Comment(String),
    Setting(&'static str, String),
}

/// Recommended TSR values for this resolution.
///
/// TSR - UE5's temporal upscaler - is what makes this game look soft. The two
/// settings that matter most are rendering at 100% of the output resolution
/// rather than upscaling from a lower one, and giving TSR a history buffer
/// above output resolution to resolve detail from. The history multiplier is
/// the expensive one, so it is scaled back at very high pixel counts.
///
/// These are a sane starting point, not gospel - every line is a normal UE
/// console variable and can be edited in Engine.ini afterwards.
pub fn tsr_settings(width: u32, height: u32) -> Vec<Line> {
    let megapixels = (width as f64 * height as f64) / 1e6;
    let (history, sharpen) = if megapixels < 8.0 {
        (200, "0.5") // up to ~3840x1600 / 3440x1440
    } else {
        (150, "0.7") // 5120x2160, 7680x2160, ...
    };
    vec![
        Line::Comment("Render at 100% of the output resolution instead of upscaling from lower".into()),
        Line::Setting("r.ScreenPercentage", "100".into()),
        Line::Comment("Highest temporal-upsampler quality".into()),
        Line::Setting("r.PostProcessAAQuality", "6".into()),
        Line::Comment("TSR history buffer above output resolution - the main anti-blur knob".into()),
        Line::Comment(format!("200 = sharpest, 100 = cheapest; {megapixels:.1} MP here")),
        Line::Setting("r.TSR.History.ScreenPercentage", history.to_string()),
        Line::Comment("Mild output sharpening to counter the temporal filter".into()),
        Line::Setting("r.Tonemapper.Sharpen", sharpen.into()),
        Line::Comment("Slightly sharper texture mips".into()),
        Line::Setting("r.MipMapLODBias", "-0.5".into()),
    ]
}

pub fn build_ini_block(game: &dyn Game, width: u32, height: u32, chromatic: bool, sharpness: bool) -> String {
    let (begin, end) = game.ini_markers();
    let mut lines = vec![begin.to_string(), "[SystemSettings]".to_string()];
    if chromatic {
        lines.push("; Chromatic aberration is far more obvious at the widened screen edges".into());
        lines.push("r.SceneColorFringeQuality=0".into());
    }
    if sharpness {
        if chromatic {
            lines.push(String::new());
        }
        for line in tsr_settings(width, height) {
            lines.push(match line {
                Line::Comment(c) => format!("; {c}"),
                Line::Setting(k, v) => format!("{k}={v}"),
            });
        }
    }
    lines.push(end.to_string());
    lines.join("\n") + "\n"
}

/// Remove a previously written managed block, so re-runs never stack.
///
/// Only a complete BEGIN..END pair is removed. A BEGIN whose END is missing -
/// a hand-edited or truncated file - is left alone rather than swallowing
/// every line after it, which could be the user's own settings.
pub fn strip_ini_block(text: &str, markers: (&str, &str), r: &mut dyn Report) -> String {
    let (begin, end) = markers;
    let lines: Vec<&str> = text.split_inclusive('\n').collect();
    let mut out: Vec<&str> = Vec::new();
    let mut i = 0;
    while i < lines.len() {
        if lines[i].trim() == begin {
            let mut e = i + 1;
            while e < lines.len() && lines[e].trim() != end {
                e += 1;
            }
            if e < lines.len() {
                // also drop the single blank separator line we insert before it,
                // so removing the block restores the file exactly as it was
                if out.last().is_some_and(|l| l.trim().is_empty()) {
                    out.pop();
                }
                i = e + 1;
                continue;
            }
            r.line(&format!(
                "  !! an unfinished '{}' marker is in this file - leaving everything after it untouched",
                begin.trim_matches(|c| c == ';' || c == ' ' || c == '=')
            ));
        }
        out.push(lines[i]);
        i += 1;
    }
    out.concat()
}

/// Locate the user's Engine.ini: native Windows first, then a Proton prefix.
///
/// `override_` is `--engine-ini`, for a copy of the game that runs in a
/// prefix Steam does not manage (Heroic, Lutris, plain Wine).
///
/// Under Proton the game writes its settings inside a prefix that Steam
/// keeps in the same library as the game, so the game's own location is the
/// best lead; every other library Steam knows about is tried after it.
/// Steam creates the prefix the first time the game is started, so before
/// that there is nowhere to write and this returns None.
pub fn engine_ini_path(game: &dyn Game, exe: Option<&Path>, override_: Option<&Path>) -> Option<PathBuf> {
    if let Some(o) = override_ {
        return Some(std::path::absolute(o).unwrap_or_else(|_| o.to_path_buf()));
    }
    let base = std::env::var_os("LOCALAPPDATA").filter(|v| !v.is_empty()).map(PathBuf::from);
    if let Some(base) = &base {
        let p = base.join(game.engine_ini_relative());
        if p.parent().is_some_and(Path::is_dir) {
            return Some(p);
        }
    }
    let mut libraries = Vec::new();
    if let Some(l) = exe.and_then(steam::library_of) {
        libraries.push(l);
    }
    libraries.extend(steam::libraries());
    let mut seen = std::collections::HashSet::new();
    for library in libraries {
        let pfx = steam::proton_prefix(&library, game.steam_appid());
        if !seen.insert(steam::normcase(&pfx)) {
            continue;
        }
        // drive_c is there once the game has run; the project folders under
        // it may not be yet, and apply_engine_ini creates them
        if pfx.join("drive_c").is_dir() {
            return Some(
                pfx.join("drive_c")
                    .join("users")
                    .join("steamuser")
                    .join("AppData")
                    .join("Local")
                    .join(game.engine_ini_relative()),
            );
        }
    }
    base.map(|b| b.join(game.engine_ini_relative()))
}

/// Write the managed block (or remove it). -> whether Engine.ini was found.
#[allow(clippy::too_many_arguments)]
pub fn apply_engine_ini(
    game: &dyn Game,
    exe: Option<&Path>,
    width: u32,
    height: u32,
    chromatic: bool,
    sharpness: bool,
    remove: bool,
    override_: Option<&Path>,
    r: &mut dyn Report,
) -> Result<bool> {
    let Some(path) = engine_ini_path(game, exe, override_) else {
        r.line("  !! could not locate Engine.ini - skipping the display tweaks");
        if !cfg!(windows) {
            r.line(
                "     It lives in the game's Proton prefix, which Steam creates the first time the game is started. \
                 Start the game once, quit, and run this installer again. If the game runs outside Steam, pass \
                 --engine-ini with the path inside its prefix.",
            );
        }
        return Ok(false);
    };
    let old = if path.is_file() {
        String::from_utf8_lossy(&std::fs::read(&path).map_err(|e| InstallError(format!("could not read {} ({e})", path.display())))?)
            .into_owned()
    } else {
        String::new()
    };
    let mut new = strip_ini_block(&old, game.ini_markers(), r);
    if !remove {
        if !new.is_empty() && !new.ends_with('\n') {
            new.push('\n');
        }
        if !new.trim().is_empty() {
            new.push('\n');
        }
        new.push_str(&build_ini_block(game, width, height, chromatic, sharpness));
    }
    if new == old {
        r.line(&format!("  already up to date: {}", path.display()));
        return Ok(true);
    }
    if let Some(parent) = path.parent()
        && !parent.is_dir() {
            std::fs::create_dir_all(parent).map_err(|e| InstallError(write_failure(parent, &e)))?;
        }
    // through a temporary file, so a failure never truncates settings the
    // user had in there
    replace_file(&path, new.as_bytes())?;
    r.line(&format!("  {} {}", if remove { "removed the managed block from" } else { "wrote" }, path.display()));
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::games::double_exposure::DOUBLE_EXPOSURE;

    #[test]
    fn the_block_goes_in_and_comes_out_leaving_the_rest() {
        let game: &dyn Game = &DOUBLE_EXPOSURE;
        let mut r = Vec::new();
        let user = "[SystemSettings]\nr.Foo=1\n";
        let block = build_ini_block(game, 5120, 2160, true, true);
        assert!(block.starts_with("; ===== BEGIN LiS:DE Ultrawide Fix"));
        assert!(block.contains("r.SceneColorFringeQuality=0\n\n; Render at 100%"));
        assert!(block.contains("r.TSR.History.ScreenPercentage=150\n"));
        assert!(block.contains("; 200 = sharpest, 100 = cheapest; 11.1 MP here\n"));
        assert!(block.contains("r.Tonemapper.Sharpen=0.7\n"));
        assert!(build_ini_block(game, 3440, 1440, false, true).contains("r.TSR.History.ScreenPercentage=200\n"));
        assert!(build_ini_block(game, 3440, 1440, false, true).contains("r.Tonemapper.Sharpen=0.5\n"));
        let written = format!("{user}\n{block}");
        assert_eq!(strip_ini_block(&written, game.ini_markers(), &mut r), user);
        // stacked twice by hand: both go
        let twice = format!("{user}\n{block}\n{block}");
        assert_eq!(strip_ini_block(&twice, game.ini_markers(), &mut r), user);
        // an unfinished marker is left alone
        let broken = format!("{user}\n{}\nr.Bar=2\n", game.ini_markers().0);
        assert_eq!(strip_ini_block(&broken, game.ini_markers(), &mut r), broken);
        assert!(r.iter().any(|l| l.contains("unfinished")));
        // CRLF files are recognised too
        let crlf = written.replace('\n', "\r\n");
        assert_eq!(strip_ini_block(&crlf, game.ini_markers(), &mut r), user.replace('\n', "\r\n"));
    }
}
