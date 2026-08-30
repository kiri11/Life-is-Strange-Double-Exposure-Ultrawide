import struct, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from iostore import Toc, load_name_batch, load_script_objects

def objidx(v):
    t = v >> 62
    return ['Export','ScriptImport','PackageImport','Null'][t], v & ((1<<62)-1)

class ZenPackage:
    def __init__(self, buf, scriptobjs):
        self.buf = buf; self.so = scriptobjs
        (self.hasver, self.hdrsize) = struct.unpack_from('<II', buf, 0)
        self.nameidx = struct.unpack_from('<I', buf, 8)[0] & 0x3FFFFFFF
        (self.pkgflags, self.cookedhdrsize, self.hashoff, self.importoff,
         self.exportoff, self.bundleoff, self.graphoff) = struct.unpack_from('<7I', buf, 16)
        self.names, p = load_name_batch(buf, 44)
        self.name = self.names[self.nameidx]
        self.imports = [struct.unpack_from('<Q', buf, self.importoff + 8*i)[0]
                        for i in range((self.exportoff - self.importoff)//8)]
        n = (self.bundleoff - self.exportoff) // 72
        self.exports = []
        for i in range(n):
            o = self.exportoff + 72*i
            ser_off, ser_size = struct.unpack_from('<QQ', buf, o)
            objname = struct.unpack_from('<I', buf, o+16)[0] & 0x3FFFFFFF
            outer, cls, sup, tmpl = struct.unpack_from('<QQQQ', buf, o+24)
            self.exports.append(dict(i=i, off=ser_off, size=ser_size,
                                     name=self.names[objname] if objname < len(self.names) else '?',
                                     outer=outer, cls=cls, super=sup, tmpl=tmpl))

    def resolve(self, v):
        t, idx = objidx(v)
        if t == 'Null': return None
        if t == 'ScriptImport': return self.so.get(v, f'<script {idx:x}>')
        if t == 'Export': return f'[export {idx}] {self.exports[idx]["name"]}' if idx < len(self.exports) else f'<export {idx}>'
        return f'<pkgimport {idx:x}>'

    def _bundle_layout(self):
        # export data is stored in export-bundle (Serialize command) order
        n = (self.graphoff - self.bundleoff) // 8
        pos = self.hdrsize
        lay = {}
        for i in range(n):
            li, cmd = struct.unpack_from('<II', self.buf, self.bundleoff + 8*i)
            if cmd == 1:
                lay[li] = pos
                pos += self.exports[li]['size']
        return lay

    def export_data(self, e):
        if not hasattr(self, '_lay'): self._lay = self._bundle_layout()
        o = self._lay[e['i']]
        return self.buf[o : o + e['size']]

def dump(path, sub, show_floats=True):
    so = load_script_objects('global.utoc')
    t = Toc(path)
    name, data = t.get(sub)
    pkg = ZenPackage(data, so)
    print(f"=== {name}  ({len(data)} bytes, {len(pkg.exports)} exports) ===")
    for e in pkg.exports:
        cls = pkg.resolve(e['cls']) or '?'
        outer = pkg.resolve(e['outer'])
        outn = outer.split('] ')[-1] if outer else '-'
        print(f"  [{e['i']:>3}] {e['name']:<38} class={str(cls).replace('/Script/',''):<42} outer={outn} size={e['size']}")
    return pkg

if __name__ == '__main__':
    os.chdir(r'D:\SteamLibrary\steamapps\common\LifeIsStrangeDoubleExposure\Chronos\Content\Paks')
    dump(sys.argv[1], sys.argv[2])
