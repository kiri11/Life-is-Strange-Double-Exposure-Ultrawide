import struct, sys, os

TYPES = ['Byte','Bool','Int','Float','Object','Name','Delegate','Double','Array','Struct','Str',
         'Text','Interface','MulticastDelegate','WeakObject','LazyObject','AssetObject','SoftObject',
         'UInt64','UInt32','UInt16','Int64','Int16','Int8','Map','Set','Enum','FieldPath','Optional',
         'Utf8Str','AnsiStr']

class R:
    def __init__(self, b): self.b = b; self.p = 0
    def u8(self):  v = self.b[self.p]; self.p += 1; return v
    def u16(self): v = struct.unpack_from('<H', self.b, self.p)[0]; self.p += 2; return v
    def u32(self): v = struct.unpack_from('<I', self.b, self.p)[0]; self.p += 4; return v
    def i32(self): v = struct.unpack_from('<i', self.b, self.p)[0]; self.p += 4; return v

def parse(path):
    raw = open(path, 'rb').read()
    magic, ver = struct.unpack_from('<HB', raw, 0)
    assert magic == 0x30C4, hex(magic)
    p = 3
    if ver >= 1:                                   # PackageVersioning
        has = struct.unpack_from('<i', raw, p)[0]; p += 4
        if has:
            p += 8
            n = struct.unpack_from('<i', raw, p)[0]; p += 4 + n*20
            p += 4
    comp = raw[p]; p += 1
    csize, dsize = struct.unpack_from('<II', raw, p); p += 8
    body = raw[p:p+csize]
    if comp != 0:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from oodle import decompress
        body = decompress(body, dsize)
    r = R(body)

    names = []
    for _ in range(r.u32()):
        ln = r.u16() if ver >= 2 else r.u8()
        names.append(body[r.p:r.p+ln].decode('utf-8', 'replace')); r.p += ln

    enums = {}
    for _ in range(r.u32()):
        ni = r.i32()
        cnt = r.u16() if ver >= 3 else r.u8()
        enums[names[ni]] = [names[r.i32()] for _ in range(cnt)]

    def ptype():
        t = r.u8()
        n = TYPES[t] if t < len(TYPES) else 'Unknown%d' % t
        if n == 'Enum':   return 'Enum<%s:%s>' % (ptype(), names[r.i32()])
        if n == 'Struct': return 'Struct<%s>' % names[r.i32()]
        if n in ('Set','Array','Optional'): return '%s<%s>' % (n, ptype())
        if n == 'Map':    return 'Map<%s,%s>' % (ptype(), ptype())
        return n

    structs = {}
    for _ in range(r.u32()):
        ni, si = r.i32(), r.i32()
        pcount, scount = r.u16(), r.u16()
        props = []
        for _ in range(scount):
            schema_idx = r.u16(); arr = r.u8(); nidx = r.i32()
            props.append((schema_idx, arr, names[nidx], ptype()))
        structs[names[ni]] = dict(super=names[si] if si >= 0 else None,
                                  count=pcount, props=props)
    return names, enums, structs

def chain(structs, name, depth=0):
    out = []
    cur = name
    while cur and depth < 30:
        s = structs.get(cur)
        if not s:
            out.append((cur, None)); break
        out.append((cur, s))
        cur = s['super']; depth += 1
    return out

if __name__ == '__main__':
    names, enums, structs = parse(sys.argv[1])
    print('names=%d enums=%d structs=%d' % (len(names), len(enums), len(structs)))
    for target in sys.argv[2:]:
        hits = [k for k in structs if target.lower() == k.lower()] or \
               [k for k in structs if target.lower() in k.lower()]
        for h in hits[:4]:
            print('\n=== %s ===' % h)
            for cls, s in chain(structs, h):
                if not s:
                    print('  (super %s not in map)' % cls); continue
                print('  -- %s (%d props)' % (cls, s['count']))
                for schema_idx, arr, nm, ty in s['props']:
                    print('     [%3d] %-42s %s%s' % (schema_idx, nm, ty, '' if arr == 1 else ' [%d]' % arr))
