import struct, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from oodle import decompress

def fstring(buf, p):
    n = struct.unpack_from('<i', buf, p)[0]; p += 4
    if n == 0: return '', p
    if n < 0: return buf[p:p-2*n-2].decode('utf-16-le'), p + (-2*n)
    return buf[p:p+n-1].decode('utf-8','replace'), p + n

def load_name_batch(buf, p):
    num, nbytes = struct.unpack_from('<II', buf, p); p += 8
    if num == 0: return [], p
    p += 8                       # HashVersion
    p += num * 8                 # Hashes
    hdrs = []
    for i in range(num):
        h = struct.unpack_from('>H', buf, p)[0]; p += 2
        hdrs.append((bool(h & 0x8000), h & 0x7FFF))
    names = []
    for utf16, ln in hdrs:
        if utf16:
            names.append(buf[p:p+ln*2].decode('utf-16-le','replace')); p += ln*2
        else:
            names.append(buf[p:p+ln].decode('utf-8','replace')); p += ln
    return names, p

class Toc:
    def __init__(self, path):
        f = open(path,'rb'); self.f = f
        h = f.read(144)
        (self.hdrsize, self.entries, self.cblocks, self.cbsize, self.cmcount,
         self.cmlen, self.blocksize, self.diridxsize, self.partitions) = struct.unpack_from('<9I', h, 0x14)
        self.flags = h[0x50]
        seeds = struct.unpack_from('<I', h, 0x54)[0]
        nohash = struct.unpack_from('<I', h, 0x60)[0]
        self.chunkids = f.read(self.entries*12)
        self.offlen  = f.read(self.entries*10)
        f.seek(f.tell() + seeds*4 + nohash*4)
        self.blocks  = f.read(self.cblocks*12)
        m = f.read(self.cmcount*self.cmlen)
        self.methods = ['None'] + [m[i*self.cmlen:(i+1)*self.cmlen].rstrip(b'\0').decode()
                                   for i in range(self.cmcount)]
        self.diridx = f.read(self.diridxsize)
        self.ucas = open(path[:-5]+'.ucas','rb')
        self.index = self._dirindex() if self.diridxsize else {}

    def _dirindex(self):
        buf = self.diridx; p = 0
        mount, p = fstring(buf, p)
        nd = struct.unpack_from('<I', buf, p)[0]; p += 4
        dirs = [struct.unpack_from('<4I', buf, p+16*i) for i in range(nd)]; p += 16*nd
        nf = struct.unpack_from('<I', buf, p)[0]; p += 4
        files = [struct.unpack_from('<3I', buf, p+12*i) for i in range(nf)]; p += 12*nf
        ns = struct.unpack_from('<I', buf, p)[0]; p += 4
        strs = []
        for i in range(ns):
            s, p = fstring(buf, p); strs.append(s)
        NONE = 0xFFFFFFFF; out = {}
        def walk(di, prefix):
            while di != NONE:
                name, fc, sib, ff = dirs[di]
                path = prefix if name == NONE else prefix + strs[name] + '/'
                fi = ff
                while fi != NONE:
                    nm, nxt, ud = files[fi]; out[path + strs[nm]] = ud; fi = nxt
                if fc != NONE: walk(fc, path)
                di = sib
        walk(0, mount.replace('../../../',''))
        return out

    def chunk_type(self, i):
        return self.chunkids[i*12+11]

    def read(self, i):
        b = self.offlen[i*10:(i+1)*10]
        off = int.from_bytes(b[0:5],'big'); ln = int.from_bytes(b[5:10],'big')
        first, last = off // self.blocksize, (off+ln-1) // self.blocksize
        out = b''
        for bi in range(first, last+1):
            r = self.blocks[bi*12:(bi+1)*12]
            boff  = int.from_bytes(r[0:5],'little')
            csize = int.from_bytes(r[5:8],'little')
            usize = int.from_bytes(r[8:11],'little')
            meth  = r[11]
            self.ucas.seek(boff); data = self.ucas.read(csize)
            out += data[:usize] if meth == 0 else decompress(data, usize)
        return out[off - first*self.blocksize:][:ln]

    def get(self, pathsub):
        hits = [k for k in self.index if pathsub.lower() in k.lower()]
        if len(hits) != 1: raise KeyError(f"{len(hits)} matches for {pathsub}: {hits[:5]}")
        return hits[0], self.read(self.index[hits[0]])

def load_script_objects(globaltoc):
    t = Toc(globaltoc)
    for i in range(t.entries):
        if t.chunk_type(i) == 5:
            buf = t.read(i)
            names, p = load_name_batch(buf, 0)
            n = struct.unpack_from('<I', buf, p)[0]; p += 4
            ents = {}
            for k in range(n):
                nm, gi, oi, cdo = struct.unpack_from('<QQQQ', buf, p + 32*k)
                idx = nm & 0x3FFFFFFF
                ents[gi] = (names[idx] if idx < len(names) else '?', oi)
            def full(gi, depth=0):
                if gi not in ents or depth > 12: return None
                nm, oi = ents[gi]
                par = full(oi, depth+1) if oi != 0xFFFFFFFFFFFFFFFF else None
                return (par + '.' + nm) if par else nm
            return {gi: (full(gi) or ents[gi][0]) for gi in ents}
    return {}
