//! Unversioned-property headers (fragments plus a zero mask), and the one
//! struct the fix decodes with them: `UCanvasPanelSlot`.
//!
//! Property values decode without a `.usmap` only because the schema is
//! hardcoded: unversioned property order is derived-class-first, then base
//! (`TFieldIterator` order), so for `UCanvasPanelSlot` it is
//! `0=LayoutData 1=bAutoSize 2=ZOrder 3=Parent 4=Content`. Object references
//! inside export payloads are 4-byte 1-based `FPackageIndex` values, not
//! the 8-byte `FPackageObjectIndex` used in the import and export maps.

fn u16_at(d: &[u8], p: usize) -> Result<u16, String> {
    Ok(u16::from_le_bytes(d.get(p..p + 2).ok_or("truncated property header")?.try_into().unwrap()))
}

fn u32_at(d: &[u8], p: usize) -> Result<u32, String> {
    Ok(u32::from_le_bytes(d.get(p..p + 4).ok_or("truncated property header")?.try_into().unwrap()))
}

fn f32_at(d: &[u8], p: usize) -> Result<f32, String> {
    Ok(f32::from_le_bytes(d.get(p..p + 4).ok_or("truncated property value")?.try_into().unwrap()))
}

fn f64_at(d: &[u8], p: usize) -> Result<f64, String> {
    Ok(f64::from_le_bytes(d.get(p..p + 8).ok_or("truncated property value")?.try_into().unwrap()))
}

/// The properties present in a payload: `(schema index, is zero)` in order,
/// and how many bytes the header took.
pub fn parse_header(d: &[u8]) -> Result<(Vec<(usize, bool)>, usize), String> {
    let mut p = 0;
    let mut frags = Vec::new();
    loop {
        let packed = u16_at(d, p)?;
        p += 2;
        let skip = (packed & 0x7F) as usize;
        let has_zero = packed & 0x80 != 0;
        let is_last = packed & 0x100 != 0;
        let vnum = (packed >> 9) as usize;
        frags.push((skip, has_zero, vnum));
        if is_last {
            break;
        }
    }
    let nzero: usize = frags.iter().filter(|f| f.1).map(|f| f.2).sum();
    let mut zbits = Vec::new();
    if nzero > 0 {
        if nzero <= 8 {
            let m = *d.get(p).ok_or("truncated zero mask")?;
            p += 1;
            zbits.extend((0..8).map(|i| (m >> i) & 1 == 1));
        } else if nzero <= 16 {
            let m = u16_at(d, p)?;
            p += 2;
            zbits.extend((0..16).map(|i| (m >> i) & 1 == 1));
        } else {
            for _ in 0..nzero.div_ceil(32) {
                let m = u32_at(d, p)?;
                p += 4;
                zbits.extend((0..32).map(|i| (m >> i) & 1 == 1));
            }
        }
    }
    let mut out = Vec::new();
    let mut idx = 0;
    let mut zi = 0;
    for (skip, has_zero, vnum) in frags {
        idx += skip;
        for _ in 0..vnum {
            let z = if has_zero {
                let z = *zbits.get(zi).ok_or("zero mask too short")?;
                zi += 1;
                z
            } else {
                false
            };
            out.push((idx, z));
            idx += 1;
        }
    }
    Ok((out, p))
}

/// What a `UCanvasPanelSlot` says about its widget.
#[derive(Debug, Clone, PartialEq)]
pub struct Slot {
    /// `FMargin` Left, Top, Right, Bottom.
    pub offsets: [f32; 4],
    pub anchor_min: (f64, f64),
    pub anchor_max: (f64, f64),
    pub alignment: (f64, f64),
    /// 1-based `FPackageIndex`; 0 when absent.
    pub parent: i32,
    pub content: i32,
}

impl Slot {
    /// The export index the slot's `Content` points at.
    pub fn content_export(&self) -> Option<usize> {
        (self.content > 0).then(|| (self.content - 1) as usize)
    }
}

pub fn decode_slot(d: &[u8]) -> Result<Slot, String> {
    let mut out = Slot {
        offsets: [0.0, 0.0, 100.0, 100.0],
        anchor_min: (0.0, 0.0),
        anchor_max: (0.0, 0.0),
        alignment: (0.0, 0.0),
        parent: 0,
        content: 0,
    };
    let (props, mut p) = parse_header(d)?;
    for (idx, zero) in props {
        match idx {
            0 => {
                // LayoutData (FAnchorData)
                let (sub, used) = parse_header(&d[p..])?;
                p += used;
                for (sidx, szero) in sub {
                    match sidx {
                        0 => {
                            // Offsets (FMargin)
                            let (m, used) = parse_header(&d[p..])?;
                            p += used;
                            let mut vals = [0.0f32, 0.0, 100.0, 100.0];
                            for (mi, mz) in m {
                                if mi > 3 {
                                    return Err("FMargin has four fields".into());
                                }
                                if mz {
                                    vals[mi] = 0.0;
                                } else {
                                    vals[mi] = f32_at(d, p)?;
                                    p += 4;
                                }
                            }
                            out.offsets = vals;
                        }
                        1 => {
                            // Anchors (FAnchors)
                            let (a, used) = parse_header(&d[p..])?;
                            p += used;
                            for (ai, az) in a {
                                let v = if az {
                                    (0.0, 0.0)
                                } else {
                                    let v = (f64_at(d, p)?, f64_at(d, p + 8)?);
                                    p += 16;
                                    v
                                };
                                if ai == 0 {
                                    out.anchor_min = v;
                                } else {
                                    out.anchor_max = v;
                                }
                            }
                        }
                        2 => {
                            // Alignment (FVector2D)
                            if szero {
                                out.alignment = (0.0, 0.0);
                            } else {
                                out.alignment = (f64_at(d, p)?, f64_at(d, p + 8)?);
                                p += 16;
                            }
                        }
                        _ => return Err(format!("unknown FAnchorData field {sidx}")),
                    }
                }
            }
            1 => p += if zero { 0 } else { 1 }, // bAutoSize
            2 => p += if zero { 0 } else { 4 }, // ZOrder
            3 | 4 => {
                // Parent / Content (FPackageIndex, 1-based)
                let v = if zero {
                    0
                } else {
                    let v = u32_at(d, p)? as i32;
                    p += 4;
                    v
                };
                if idx == 3 {
                    out.parent = v;
                } else {
                    out.content = v;
                }
            }
            _ => return Err(format!("unknown UCanvasPanelSlot field {idx}")),
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn header_fragments_and_zero_mask() {
        // one fragment: skip 0, 3 values, has zeros, last; mask 0b010 -> the middle one is zero
        let d = [0x80 | (3 << 9) as u8, ((0x100 | (3 << 9)) >> 8) as u8, 0b010];
        let packed = u16::from_le_bytes([d[0], d[1]]);
        assert_eq!(packed & 0x7F, 0);
        let (props, used) = parse_header(&d).unwrap();
        assert_eq!(used, 3);
        assert_eq!(props, vec![(0, false), (1, true), (2, false)]);
        assert!(parse_header(&[0x00]).is_err());
    }
}
