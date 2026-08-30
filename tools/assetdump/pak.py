import struct, sys, os, zlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from oodle import decompress as oodle_dec
from iostore import fstring

MAGIC = 0x5A6F12E1

class Pak:
    def __init__(self, path):
        self.f = open(path,'rb')
        self.f.seek(0,2); self.size = self.f.tell()
        tail_n = 400
        self.f.seek(self.size - tail_n); tail = self.f.read(tail_n)
        pos = tail.rfind(struct.pack('<I', MAGIC))
        if pos < 0: raise RuntimeError('pak magic not found')
        p = pos + 4
        self.version = struct.unpack_from('<i', tail, p)[0]; p += 4
        self.idx_off, self.idx_size = struct.unpack_from('<qq', tail, p); p += 16
        self.idx_hash = tail[p:p+20]; p += 20
        nmeth = 5 if self.version >= 8 else 0
        self.methods = ['None']
        for i in range(nmeth):
            nm = tail[p:p+32].rstrip(b'\0').decode(); p += 32
            if nm: self.methods.append(nm)
        self.encrypted_index = tail[pos-4-16-1] if False else tail[pos-1]
        self.f.seek(self.idx_off); idx = self.f.read(self.idx_size)
        self._parse_index(idx)

    def _parse_index(self, idx):
        p = 0
        self.mount, p = fstring(idx, p)
        self.num_entries = struct.unpack_from('<i', idx, p)[0]; p += 4
        p += 8                                        # PathHashSeed
        has_ph = struct.unpack_from('<i', idx, p)[0]; p += 4
        if has_ph: p += 8 + 8 + 20
        has_fd = struct.unpack_from('<i', idx, p)[0]; p += 4
        if has_fd:
            fd_off, fd_size = struct.unpack_from('<qq', idx, p); p += 16
            p += 20
        enc_size = struct.unpack_from('<i', idx, p)[0]; p += 4
        self.encoded = idx[p:p+enc_size]; p += enc_size
        self.files = {}
        if has_fd:
            self.f.seek(fd_off); fd = self.f.read(fd_size)
            q = 0
            ndirs = struct.unpack_from('<i', fd, q)[0]; q += 4
            for _ in range(ndirs):
                dname, q = fstring(fd, q)
                nf = struct.unpack_from('<i', fd, q)[0]; q += 4
                for _ in range(nf):
                    fname, q = fstring(fd, q)
                    eoff = struct.unpack_from('<i', fd, q)[0]; q += 4
                    self.files[(self.mount + dname + fname).replace('../../../','')] = eoff

    def _decode_entry(self, off):
        d = self.encoded; p = off
        v = struct.unpack_from('<I', d, p)[0]; p += 4
        cmi = (v >> 23) & 0x3F
        encrypted = bool((v >> 22) & 1)
        nblocks = (v >> 6) & 0xFFFF
        bsize = (v & 0x3F) << 11
        if (v & 0x3F) == 0x3F:
            bsize = struct.unpack_from('<I', d, p)[0]; p += 4
        def rd(bit):
            nonlocal p
            if v & (1 << bit):
                x = struct.unpack_from('<I', d, p)[0]; p += 4
            else:
                x = struct.unpack_from('<q', d, p)[0]; p += 8
            return x
        offset = rd(31)
        usize  = rd(30)
        size   = rd(29) if cmi != 0 else usize
        blocks = []
        if cmi != 0:
            if nblocks == 1:
                blocks = [(0, size)]
            elif nblocks > 1:
                for i in range(nblocks):
                    s, e = struct.unpack_from('<II', d, p); p += 8
                    blocks.append((s, e))
        return dict(offset=offset, size=size, usize=usize, cmi=cmi,
                    blocks=blocks, bsize=bsize, nblocks=nblocks, encrypted=encrypted)

    def read(self, path):
        e = self._decode_entry(self.files[path])
        # skip the repeated FPakEntry header stored before the payload
        self.f.seek(e['offset'])
        hdr = self.f.read(200)
        hp = 8+8+8+4+20
        cmi = struct.unpack_from('<I', hdr, 24)[0]
        if cmi != 0:
            n = struct.unpack_from('<i', hdr, hp)[0]; hp += 4 + n*16
        hp += 1 + 4
        data_off = e['offset'] + hp
        if e['cmi'] == 0:
            self.f.seek(data_off); return self.f.read(e['usize'])
        meth = self.methods[e['cmi']]
        out = b''
        for i, (s, en) in enumerate(e['blocks']):
            if e['nblocks'] == 1:
                self.f.seek(data_off); raw = self.f.read(e['size'])
                ul = e['usize']
            else:
                self.f.seek(e['offset'] + s); raw = self.f.read(en - s)
                ul = min(e['bsize'], e['usize'] - len(out))
            if meth.lower().startswith('zlib'): out += zlib.decompress(raw)
            elif meth.lower().startswith('oodle'): out += oodle_dec(raw, ul)
            elif meth.lower().startswith('gzip'): out += zlib.decompress(raw, 31)
            else: raise RuntimeError('method ' + meth)
        return out
