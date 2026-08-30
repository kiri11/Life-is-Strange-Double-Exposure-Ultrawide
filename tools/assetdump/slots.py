import struct, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from zen import ZenPackage
from iostore import Toc, load_script_objects
from unver import parse_header

def rd_hdr(d, p):
    props, used, _ = parse_header(d[p:])
    return props, p + used

def decode_slot(d):
    """UCanvasPanelSlot schema (derived-first): 0=LayoutData 1=bAutoSize 2=ZOrder 3=Parent 4=Content"""
    out = {'Offsets': (0.0,0.0,100.0,100.0), 'AnchorMin': (0.0,0.0),
           'AnchorMax': (0.0,0.0), 'Alignment': (0.0,0.0), 'Parent': None, 'Content': None}
    props, p = rd_hdr(d, 0)
    for idx, zero in props:
        if idx == 0:                                   # LayoutData (FAnchorData)
            sub, p = rd_hdr(d, p)
            for sidx, szero in sub:
                if sidx == 0:                          # Offsets (FMargin)
                    m, p = rd_hdr(d, p)
                    vals = [0.0,0.0,100.0,100.0]
                    for mi, mz in m:
                        if mz: vals[mi] = 0.0
                        else:
                            vals[mi] = struct.unpack_from('<f', d, p)[0]; p += 4
                    out['Offsets'] = tuple(vals)
                elif sidx == 1:                        # Anchors (FAnchors)
                    a, p = rd_hdr(d, p)
                    for ai, az in a:
                        if az: v = (0.0, 0.0)
                        else:
                            v = struct.unpack_from('<dd', d, p); p += 16
                        out['AnchorMin' if ai == 0 else 'AnchorMax'] = v
                elif sidx == 2:                        # Alignment (FVector2D)
                    if szero: out['Alignment'] = (0.0,0.0)
                    else:
                        out['Alignment'] = struct.unpack_from('<dd', d, p); p += 16
        elif idx == 1: p += 0 if zero else 1           # bAutoSize
        elif idx == 2: p += 0 if zero else 4           # ZOrder
        elif idx in (3,4):                             # Parent / Content (FPackageIndex, 1-based)
            v = 0 if zero else struct.unpack_from('<i', d, p)[0]
            if not zero: p += 4
            out['Parent' if idx == 3 else 'Content'] = v
    return out

def report(toc, so, sub):
    name, data = toc.get(sub)
    p = ZenPackage(data, so)
    print(f"\n===== {name}")
    byidx = {e['i']: e for e in p.exports}
    for e in p.exports:
        cls = str(p.resolve(e['cls'])).replace('/Script/','')
        if cls != 'UMG.CanvasPanelSlot': continue
        try: s = decode_slot(p.export_data(e))
        except Exception as ex: print('   slot decode err', ex); continue
        par = byidx.get((s['Parent'] or 0)-1, {}).get('name','?')
        con = byidx.get((s['Content'] or 0)-1, {}).get('name','?')
        am, aM = s['AnchorMin'], s['AnchorMax']
        stretch = 'FULL-STRETCH' if am==(0,0) and aM==(1,1) else ''
        print(f"   {par:<20} -> {con:<28} anchors=({am[0]:g},{am[1]:g})-({aM[0]:g},{aM[1]:g}) "
              f"offsets=({s['Offsets'][0]:g},{s['Offsets'][1]:g},{s['Offsets'][2]:g},{s['Offsets'][3]:g}) "
              f"align=({s['Alignment'][0]:g},{s['Alignment'][1]:g}) {stretch}")

if __name__ == '__main__':
    os.chdir(r'D:\SteamLibrary\steamapps\common\LifeIsStrangeDoubleExposure\Chronos\Content\Paks')
    so = load_script_objects('global.utoc')
    toc = Toc('pakchunk0-Windows.utoc')
    for sub in sys.argv[1:]:
        try: report(toc, so, sub)
        except Exception as ex: print(sub, 'ERR', ex)
