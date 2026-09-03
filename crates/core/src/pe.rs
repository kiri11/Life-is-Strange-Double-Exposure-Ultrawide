//! Just enough of the PE format: the section table, which reads the same from
//! the file on disk and from the mapped image.

use crate::scan::{Image, Section};

pub const EXECUTABLE: u32 = 0x2000_0000;

pub struct SectionHeader {
    pub name: String,
    pub va: u64,
    pub vsize: usize,
    pub raw_off: usize,
    pub raw_size: usize,
    pub characteristics: u32,
}

pub struct Headers {
    pub timestamp: u32,
    pub size_of_image: u32,
    pub sections: Vec<SectionHeader>,
}

fn u16_at(d: &[u8], o: usize) -> Option<u16> {
    Some(u16::from_le_bytes(d.get(o..o + 2)?.try_into().ok()?))
}

fn u32_at(d: &[u8], o: usize) -> Option<u32> {
    Some(u32::from_le_bytes(d.get(o..o + 4)?.try_into().ok()?))
}

/// Where the optional header says the headers end: read this many bytes of a
/// mapped image before calling [`parse`].
pub fn size_of_headers(head: &[u8]) -> Option<usize> {
    if head.get(..2)? != b"MZ" {
        return None;
    }
    let pe = u32_at(head, 0x3C)? as usize;
    Some(u32_at(head, pe + 24 + 60)? as usize)
}

pub fn parse(head: &[u8]) -> Result<Headers, String> {
    if head.get(..2) != Some(&b"MZ"[..]) {
        return Err("not a Windows executable (no MZ header)".into());
    }
    let pe = u32_at(head, 0x3C).ok_or("truncated DOS header")? as usize;
    if head.get(pe..pe + 4) != Some(&b"PE\0\0"[..]) {
        return Err("not a PE image".into());
    }
    let count = u16_at(head, pe + 6).ok_or("truncated PE header")? as usize;
    let timestamp = u32_at(head, pe + 8).ok_or("truncated PE header")?;
    let opt_size = u16_at(head, pe + 20).ok_or("truncated PE header")? as usize;
    let size_of_image = u32_at(head, pe + 24 + 56).ok_or("truncated optional header")?;
    let table = pe + 24 + opt_size;
    let mut sections = Vec::with_capacity(count);
    for i in 0..count {
        let o = table + i * 40;
        let row = head.get(o..o + 40).ok_or("truncated section table")?;
        let name_len = row[..8].iter().position(|&b| b == 0).unwrap_or(8);
        sections.push(SectionHeader {
            name: String::from_utf8_lossy(&row[..name_len]).into_owned(),
            vsize: u32_at(row, 8).unwrap() as usize,
            va: u32_at(row, 12).unwrap() as u64,
            raw_size: u32_at(row, 16).unwrap() as usize,
            raw_off: u32_at(row, 20).unwrap() as usize,
            characteristics: u32_at(row, 36).unwrap(),
        });
    }
    Ok(Headers { timestamp, size_of_image, sections })
}

/// The executable sections of a file on disk, addressed by RVA as they will
/// be once mapped. What the tests run the planner over.
pub fn file_image(data: &[u8]) -> Result<(Headers, Image<'_>), String> {
    let headers = parse(data)?;
    let mut sections = Vec::new();
    for s in &headers.sections {
        if s.characteristics & EXECUTABLE == 0 {
            continue;
        }
        let len = s.raw_size.min(s.vsize);
        let bytes = data
            .get(s.raw_off..s.raw_off + len)
            .ok_or_else(|| format!("section {} runs past the end of the file", s.name))?;
        sections.push(Section { va: s.va, data: bytes });
    }
    Ok((headers, Image { sections }))
}
