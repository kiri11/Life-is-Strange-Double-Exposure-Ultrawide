"""
Oodle decompression for the container readers.

The fix decodes with the pure-Python Kraken decoder in kraken.py - no native
library, nothing to download, the same code on Windows, Linux and macOS.

A native Oodle library is still useful for the research scripts, which read
far more than the fix ever does: set LISDE_OODLE_DLL to a path to use one.
Nothing looks for a DLL on its own any more, so an installer run can never
load a library it did not ask for.
"""
import ctypes, os, sys

import kraken

_native = None


def _load_native(path):
    if sys.platform == 'win32' and not path.lower().endswith('.dll'):
        raise RuntimeError('LISDE_OODLE_DLL must name a Windows DLL on Windows: ' + path)
    if sys.platform != 'win32' and path.lower().endswith('.dll'):
        raise RuntimeError('LISDE_OODLE_DLL names a Windows DLL, which cannot be '
                           'loaded on this platform: ' + path)
    dll = ctypes.CDLL(path)
    fn = dll.OodleLZ_Decompress
    fn.restype = ctypes.c_ssize_t
    fn.argtypes = [ctypes.c_char_p, ctypes.c_ssize_t, ctypes.c_char_p,
                   ctypes.c_ssize_t, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                   ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ssize_t,
                   ctypes.c_int]
    return fn


def _native_fn():
    global _native
    if _native is None:
        path = os.environ.get('LISDE_OODLE_DLL')
        _native = _load_native(path) if path and os.path.isfile(path) else False
    return _native


def decompress(src, dstlen):
    fn = _native_fn()
    if fn:
        dst = ctypes.create_string_buffer(dstlen)
        n = fn(src, len(src), dst, dstlen, 1, 0, 0, None, 0, None, None, None, 0, 3)
        if n != dstlen:
            raise RuntimeError("oodle returned {}, expected {}".format(n, dstlen))
        return dst.raw[:dstlen]
    return kraken.decompress(src, dstlen)
