//! Wine and Proton: the per-application DLL override in a prefix's
//! `user.reg`, where the prefix is, and whether the game is running.
//!
//! Wine loads its own winhttp.dll unless told otherwise. The override is the
//! one winecfg would write, scoped to the game's executable so nothing else
//! in the prefix is affected. Wine writes the registry back when it shuts
//! down, over anything changed in the file while it ran, so the edit is
//! refused while the game runs.

use std::path::{Path, PathBuf};

use crate::games::Game;
use crate::report::{InstallError, Report, Result, replace_file};
use crate::steam;

/// The key as it appears in user.reg: `Software\\Wine\\AppDefaults\\<exe>\\DllOverrides`.
pub fn override_key(game: &dyn Game) -> String {
    format!("Software\\\\Wine\\\\AppDefaults\\\\{}\\\\DllOverrides", game.exe_name())
}

/// `"winhttp"="native,builtin"`.
pub fn override_value(dll: &str) -> String {
    format!("\"{}\"=\"native,builtin\"", dll_base(dll))
}

fn dll_base(dll: &str) -> &str {
    dll.strip_suffix(".dll").or_else(|| dll.strip_suffix(".DLL")).unwrap_or(dll)
}

fn now_secs() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

/// `user.reg` with the DLL set to native for the game, or without that.
///
/// The file is line-based: a key is a `[path] time` line, then its values,
/// up to a blank line; everything else is left exactly as it was.
pub fn set_dll_override(text: &str, game: &dyn Game, dll: &str, remove: bool) -> String {
    let mut lines: Vec<String> = text.split('\n').map(str::to_string).collect();
    let header = format!("[{}]", override_key(game));
    let value = override_value(dll);
    let value_prefix = format!("\"{}\"=", dll_base(dll));
    let start = lines.iter().position(|l| {
        l.starts_with(&header) && matches!(l[header.len()..].chars().next(), None | Some(' '))
    });
    let Some(start) = start else {
        if remove {
            return text.to_string();
        }
        let mut out = text.to_string();
        if !out.is_empty() && !out.ends_with('\n') {
            out.push('\n');
        }
        return format!("{out}\n{header} {}\n{value}\n", now_secs());
    };
    let mut end = start + 1;
    while end < lines.len() && !lines[end].is_empty() && !lines[end].starts_with('[') {
        end += 1;
    }
    let body: Vec<String> = lines[start + 1..end].iter().filter(|l| !l.starts_with(&value_prefix)).cloned().collect();
    if remove {
        if body.iter().any(|l| l.starts_with('"')) {
            let mut replacement = vec![lines[start].clone()];
            replacement.extend(body);
            lines.splice(start..end, replacement);
        } else {
            // the key held nothing else: drop it and the blank line after it
            if end < lines.len() && lines[end].is_empty() {
                end += 1;
            }
            lines.drain(start..end);
        }
        return lines.join("\n");
    }
    if lines[start + 1..end].contains(&value) {
        return text.to_string();
    }
    let mut replacement = vec![lines[start].clone()];
    replacement.extend(body);
    replacement.push(value);
    lines.splice(start..end, replacement);
    lines.join("\n")
}

/// The Proton or Wine prefix the game runs in: `--engine-ini`'s, else the
/// one Steam keeps for the game. None before the game has been started once.
pub fn wine_prefix(game: &dyn Game, exe: &Path, engine_ini: Option<&Path>) -> Option<PathBuf> {
    if let Some(ini) = engine_ini {
        let abs = std::path::absolute(ini).unwrap_or_else(|_| ini.to_path_buf());
        let mut path = abs.as_path();
        loop {
            let parent = path.parent()?;
            if path.file_name().is_some_and(|n| n.to_string_lossy().eq_ignore_ascii_case("drive_c")) {
                return Some(parent.to_path_buf());
            }
            path = parent;
        }
    }
    let mut libraries = Vec::new();
    if let Some(l) = steam::library_of(exe) {
        libraries.push(l);
    }
    libraries.extend(steam::libraries());
    libraries
        .into_iter()
        .map(|l| steam::proton_prefix(&l, game.steam_appid()))
        .find(|pfx| pfx.join("user.reg").is_file())
}

/// Linux: is the game's process alive?
pub fn game_is_running(game: &dyn Game) -> bool {
    let Ok(entries) = std::fs::read_dir("/proc") else { return false };
    let needle = game.exe_name().to_lowercase();
    for e in entries.flatten() {
        if !e.file_name().to_string_lossy().bytes().all(|b| b.is_ascii_digit()) {
            continue;
        }
        if let Ok(cmd) = std::fs::read(e.path().join("cmdline"))
            && String::from_utf8_lossy(&cmd).to_lowercase().contains(&needle) {
                return true;
            }
    }
    false
}

/// Tell the game's Wine prefix to load the fix's DLL, or stop.
pub fn apply_dll_override(game: &dyn Game, exe: &Path, dll: &str, engine_ini: Option<&Path>, remove: bool, r: &mut dyn Report) -> Result<()> {
    let Some(pfx) = wine_prefix(game, exe, engine_ini) else {
        if remove {
            return Ok(());
        }
        r.line("  !! no Proton prefix found for the game, so Wine does not know to");
        r.line("     load the fix yet. Start the game once through Steam, quit, and");
        r.line("     run Install again - or add this to the game's launch options:");
        r.line(&format!("       WINEDLLOVERRIDES=\"{}=n,b\" %command%", dll_base(dll)));
        return Ok(());
    };
    let reg = pfx.join("user.reg");
    if game_is_running(game) {
        return Err(InstallError(
            "the game is running - quit it and run this again (Wine would overwrite the registry change when it exits)"
                .into(),
        ));
    }
    let bytes = std::fs::read(&reg).map_err(|e| InstallError(format!("could not read {} ({e})", reg.display())))?;
    let text = String::from_utf8_lossy(&bytes);
    let new = set_dll_override(&text, game, dll, remove);
    if new == text {
        r.line(&format!(
            "  Wine prefix: {dll} {} already",
            if remove { "not overridden" } else { "set to native for the game" }
        ));
        return Ok(());
    }
    replace_file(&reg, new.as_bytes())?;
    r.line(&format!(
        "  Wine prefix: {dll} {} in {}",
        if remove { "override removed" } else { "set to native for the game" },
        reg.display()
    ));
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::games::double_exposure::DOUBLE_EXPOSURE;

    const REG: &str = "WINE REGISTRY Version 2\n;; All keys relative to \\\\User\\\\S-1-5-21-0-0-0-1000\n\n#arch=win64\n\n[Control Panel\\\\Desktop] 1700000000\n#time=1da0000\n\"FontSmoothing\"=\"2\"\n\n[Software\\\\Wine\\\\DllOverrides] 1700000000\n\"d3d11\"=\"native\"\n";

    #[test]
    fn the_override_is_added_and_removed_cleanly() {
        let game: &dyn Game = &DOUBLE_EXPOSURE;
        let key = format!("[{}]", override_key(game));
        let value = override_value("winhttp.dll");
        assert_eq!(value, "\"winhttp\"=\"native,builtin\"");
        let set = |t: &str| set_dll_override(t, game, "winhttp.dll", false);
        let unset = |t: &str| set_dll_override(t, game, "winhttp.dll", true);

        let added = set(REG);
        assert!(added.starts_with(REG), "adding the override keeps everything that was there");
        let tail = &added[REG.len()..];
        assert!(tail.starts_with(&format!("\n{key} ")) && tail.ends_with(&format!("\n{value}\n")), "a new key of its own at the end");
        assert_eq!(set(&added), added, "adding it twice changes nothing");
        assert_eq!(unset(&added), REG, "removing it gives the original back");
        assert_eq!(unset(REG), REG, "removing what is not there changes nothing");

        // the key already exists with another value in it
        let shared = format!("{REG}\n{key} 1700000000\n\"dinput8\"=\"native,builtin\"\n");
        let added = set(&shared);
        assert_eq!(added, format!("{shared}{value}\n"), "the value joins an existing key");
        assert_eq!(unset(&added), shared, "removing it leaves the other value and the key");
        // a stale value of ours is replaced, not duplicated
        let stale = format!("{REG}\n{key} 1700000000\n\"winhttp\"=\"builtin\"\n");
        assert_eq!(set(&stale), format!("{REG}\n{key} 1700000000\n{value}\n"), "a different value for winhttp is replaced");
        // a key that is not the last block in the file
        let middle = REG.replace(
            "[Software\\\\Wine\\\\DllOverrides]",
            &format!("{key} 1700000000\n\"winhttp\"=\"native,builtin\"\n\n[Software\\\\Wine\\\\DllOverrides]"),
        );
        assert_eq!(set(&middle), middle, "present in the middle: recognised");
        assert_eq!(unset(&middle), REG, "removed from the middle cleanly");
    }

    #[test]
    fn the_prefix_is_found_from_an_engine_ini_path() {
        let game: &dyn Game = &DOUBLE_EXPOSURE;
        let ini = Path::new("/lib/steamapps/compatdata/1874000/pfx/drive_c/users/steamuser/AppData/Local/Chronos/Saved/Config/Windows/Engine.ini");
        let got = wine_prefix(game, Path::new("/nowhere/x.exe"), Some(ini)).unwrap();
        assert!(got.ends_with("compatdata/1874000/pfx") || got.ends_with("compatdata\\1874000\\pfx"), "{}", got.display());
    }
}
