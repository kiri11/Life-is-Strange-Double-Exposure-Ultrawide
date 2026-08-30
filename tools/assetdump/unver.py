import struct
def parse_header(d):
    """Returns (list_of_(schema_index, is_zero), bytes_consumed)."""
    p = 0; frags = []
    while True:
        packed = struct.unpack_from('<H', d, p)[0]; p += 2
        skip = packed & 0x7F
        haszero = bool(packed & 0x80)
        islast = bool(packed & 0x100)
        vnum = packed >> 9
        frags.append((skip, haszero, vnum, islast))
        if islast: break
    nzero = sum(v for s,h,v,l in frags if h)
    zbits = []
    if nzero:
        if nzero <= 8:
            m = d[p]; p += 1; zbits = [(m>>i)&1 for i in range(8)]
        elif nzero <= 16:
            m = struct.unpack_from('<H', d, p)[0]; p += 2; zbits = [(m>>i)&1 for i in range(16)]
        else:
            words = (nzero+31)//32
            zbits = []
            for w in range(words):
                m = struct.unpack_from('<I', d, p)[0]; p += 4
                zbits += [(m>>i)&1 for i in range(32)]
    out = []; idx = 0; zi = 0
    for skip, haszero, vnum, islast in frags:
        idx += skip
        for k in range(vnum):
            z = bool(zbits[zi]) if haszero else False
            if haszero: zi += 1
            out.append((idx, z)); idx += 1
    return out, p, frags
