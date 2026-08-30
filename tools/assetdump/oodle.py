import ctypes, os
_dll = ctypes.CDLL(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'oodle-data-shared.dll'))
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
