import ctypes, glob, os


def _locate():
    """Find an Oodle decompressor: an explicit override, then this directory.

    LISDE_OODLE_DLL is set by patcher.py when it locates or downloads one -
    including a DLL shipped by some other Unreal Engine game on the machine,
    which exports the same OodleLZ_Decompress entry point.
    """
    env = os.environ.get('LISDE_OODLE_DLL')
    if env and os.path.isfile(env):
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    for pattern in ('oodle-data-shared.dll', 'oo2core_*_win64.dll',
                    'liboodle-data-shared.so'):
        hits = sorted(glob.glob(os.path.join(here, pattern)))
        if hits:
            return hits[0]
    raise RuntimeError(
        'No Oodle decompressor found. Run patcher.py, which can fetch one, '
        'or see tools/assetdump/README.md.')


_dll = ctypes.CDLL(_locate())
_f = _dll.OodleLZ_Decompress
_f.restype = ctypes.c_ssize_t
_f.argtypes = [ctypes.c_char_p, ctypes.c_ssize_t, ctypes.c_char_p, ctypes.c_ssize_t,
               ctypes.c_int, ctypes.c_int, ctypes.c_int,
               ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_void_p,
               ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_int]
def decompress(src, dstlen):
    dst = ctypes.create_string_buffer(dstlen)
    n = _f(src, len(src), dst, dstlen, 1, 0, 0, None, 0, None, None, None, 0, 3)
    if n != dstlen:
        raise RuntimeError(f"oodle returned {n}, expected {dstlen}")
    return dst.raw[:dstlen]
