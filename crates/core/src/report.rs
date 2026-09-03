//! Where the installer's progress lines go, and the one error type a user
//! can act on.

use std::fmt;
use std::io::Write as _;

/// Progress lines, one call per line: the console in the installer, a
/// `Vec<String>` in the tests.
pub trait Report {
    fn line(&mut self, text: &str);
}

impl Report for Vec<String> {
    fn line(&mut self, text: &str) {
        self.push(text.to_string());
    }
}

/// Standard output. Rust writes UTF-8 to a pipe and Unicode to a console
/// whatever the code page, which is what the Windows front-end relies on;
/// a closed pipe is ignored rather than fatal.
pub struct Stdout;

impl Report for Stdout {
    fn line(&mut self, text: &str) {
        let out = std::io::stdout();
        let mut out = out.lock();
        let _ = writeln!(out, "{text}");
        let _ = out.flush();
    }
}

/// A problem the user can act on: reported as one line, without a
/// backtrace. Anything else is a bug in the fix and panics with its
/// location, which is what a useful issue report needs.
#[derive(Debug, Clone, PartialEq)]
pub struct InstallError(pub String);

impl fmt::Display for InstallError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for InstallError {}

impl From<String> for InstallError {
    fn from(s: String) -> Self {
        InstallError(s)
    }
}

impl From<&str> for InstallError {
    fn from(s: &str) -> Self {
        InstallError(s.to_string())
    }
}

pub type Result<T> = std::result::Result<T, InstallError>;

/// Turn a write error into something the person at the keyboard can fix.
pub fn write_failure(path: &std::path::Path, err: &std::io::Error) -> String {
    use std::io::ErrorKind;
    let os = err.raw_os_error();
    let name = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    if cfg!(windows) && os == Some(32) {
        return format!("{name} is in use - close the game (and Steam) and try again.");
    }
    if err.kind() == ErrorKind::PermissionDenied || (cfg!(windows) && os == Some(5)) {
        return format!(
            "The system refused permission to write {}. Close the game, and if it is \
             installed under Program Files, run this installer as administrator.",
            path.display()
        );
    }
    if (cfg!(windows) && os == Some(112)) || (cfg!(unix) && os == Some(28)) {
        return format!("the drive holding {} is full.", path.display());
    }
    format!("could not write {} ({err}).", path.display())
}

/// Write through a temporary file, so a failure never leaves half a file.
pub fn replace_file(path: &std::path::Path, data: &[u8]) -> Result<()> {
    replace_via_tmp(path, |tmp| std::fs::write(tmp, data))
}

/// `replace_file` with the bytes of another file, streamed rather than read
/// into memory.
pub fn replace_file_from(path: &std::path::Path, source: &std::path::Path) -> Result<()> {
    replace_via_tmp(path, |tmp| std::fs::copy(source, tmp).map(|_| ()))
}

fn replace_via_tmp(path: &std::path::Path, write: impl FnOnce(&std::path::Path) -> std::io::Result<()>) -> Result<()> {
    let mut tmp = path.as_os_str().to_owned();
    tmp.push(".tmp");
    let tmp = std::path::PathBuf::from(tmp);
    let attempt = write(&tmp).and_then(|()| std::fs::rename(&tmp, path));
    if let Err(err) = attempt {
        let _ = std::fs::remove_file(&tmp);
        return Err(InstallError(write_failure(path, &err)));
    }
    Ok(())
}
