//! A camera patch as a list of byte writes, and how a planner finds its
//! sites. A planner only decides what to write and where; nothing here
//! touches memory, so the same function runs over the file on disk in the
//! tests, where its output is held against the patch the Python installer
//! used to write.

use crate::scan::{Image, Sig};

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

/// A patch site: how it looks in the shipped game, where it was in the
/// build the fix was written against, and how it looks once patched.
pub struct Site {
    pub name: &'static str,
    pub sig: &'static str,
    /// Tried first, so a normal launch does no scanning at all.
    pub expected: u64,
    /// How the site looks once patched, to say so instead of "not found".
    pub patched: &'static [&'static str],
}

pub fn locate(img: &Image, site: &Site, notes: &mut Vec<String>) -> Result<u64, String> {
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

pub fn rel32(target: u64, next_instruction: u64) -> Result<i32, String> {
    i32::try_from(target as i64 - next_instruction as i64)
        .map_err(|_| format!("rva {target:#x} is out of rel32 reach from {next_instruction:#x}"))
}
