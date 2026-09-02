#!/usr/bin/env python3
"""
Checks the installer's camera-loader bookkeeping without a game or a DLL:
the Wine registry edit, the loader recognition and the status it reports.
Run: python tests/test_camera.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import patcher

failures = []


def check(cond, message):
    print(('ok   ' if cond else 'FAIL ') + message)
    if not cond:
        failures.append(message)


KEY = '[' + patcher.WINE_OVERRIDE_KEY + ']'
REG = ('WINE REGISTRY Version 2\n;; All keys relative to \\\\User\\\\S-1-5-21-0-0-0-1000\n\n'
       '#arch=win64\n\n'
       '[Control Panel\\\\Desktop] 1700000000\n#time=1da0000\n"FontSmoothing"="2"\n\n'
       '[Software\\\\Wine\\\\DllOverrides] 1700000000\n"d3d11"="native"\n')


def wine_override():
    added = patcher.set_wine_dll_override(REG)
    check(added.startswith(REG), 'adding the override keeps everything that was there')
    tail = added[len(REG):]
    check(tail.startswith('\n' + KEY + ' ') and tail.endswith('\n' + patcher.WINE_OVERRIDE_VALUE + '\n'),
          'the override is a new key of its own at the end')
    check(patcher.set_wine_dll_override(added) == added, 'adding it twice changes nothing')
    check(patcher.set_wine_dll_override(added, remove=True) == REG, 'removing it gives the original back')
    check(patcher.set_wine_dll_override(REG, remove=True) == REG, 'removing what is not there changes nothing')

    # the key already exists with another value in it
    shared = REG + '\n' + KEY + ' 1700000000\n"dinput8"="native,builtin"\n'
    added = patcher.set_wine_dll_override(shared)
    check(added == shared + patcher.WINE_OVERRIDE_VALUE + '\n', 'the value joins an existing key')
    check(patcher.set_wine_dll_override(added, remove=True) == shared,
          'removing it leaves the other value and the key')
    # a stale value of ours is replaced, not duplicated
    stale = REG + '\n' + KEY + ' 1700000000\n"winhttp"="builtin"\n'
    check(patcher.set_wine_dll_override(stale) == REG + '\n' + KEY + ' 1700000000\n'
          + patcher.WINE_OVERRIDE_VALUE + '\n', 'a different value for version is replaced')
    # a key that is not the last block in the file
    middle = REG.replace('[Software\\\\Wine\\\\DllOverrides]',
                         KEY + ' 1700000000\n"winhttp"="native,builtin"\n\n[Software\\\\Wine\\\\DllOverrides]')
    check(patcher.set_wine_dll_override(middle) == middle, 'present in the middle: recognised')
    check(patcher.set_wine_dll_override(middle, remove=True) == REG, 'removed from the middle cleanly')


def loader_status():
    tmp = tempfile.mkdtemp()
    win64 = os.path.join(tmp, 'Chronos', 'Binaries', 'Win64')
    os.makedirs(win64)
    exe = os.path.join(win64, patcher.EXE_NAME)
    check(patcher.check_camera(exe)[0] == 'missing', 'no executable: missing')
    with open(exe, 'wb') as f:
        f.write(b'MZ')
    check(patcher.check_camera(os.path.join(win64, 'other.exe'))[0] == 'missing',
          'a path that does not exist: missing')
    other = os.path.join(win64, 'Chronos.exe')
    with open(other, 'wb') as f:
        f.write(b'MZ')
    check(patcher.check_camera(other)[0] == 'notgame', 'another executable: notgame')
    check(patcher.check_camera(exe)[0] == 'none', 'no loader: none')

    dll, ini, log = patcher.camera_paths(exe)
    with open(dll, 'wb') as f:
        f.write(b'MZ some other mod')
    check(not patcher.is_our_dll(dll), 'a foreign winhttp.dll is not ours')
    check(patcher.check_camera(exe)[0] == 'foreign', 'a foreign winhttp.dll: foreign')
    with open(dll, 'wb') as f:
        f.write(b'MZ' + patcher.DLL_MARKER + b'!')
    check(patcher.is_our_dll(dll), 'the marker identifies our loader')
    status, detail = patcher.check_camera(exe)
    check(status in ('installed', 'outdated') and 'not been started' in detail,
          'our loader without a log: installed, not launched yet (%s)' % detail)
    with open(log, 'w') as f:
        f.write('LiS Ultrawide Fix camera loader dev - now\n  note\napplied 6 writes - the fix is active\n')
    check(patcher.last_launch(log) == 'applied 6 writes - the fix is active', 'the verdict is read from the log')
    check('last launch: applied 6 writes' in patcher.check_camera(exe)[1], 'the status carries the verdict')

    # the old in-place patch: a matching backup is restored and dropped
    header = bytearray(0x200)
    header[:2] = b'MZ'
    header[0x3C:0x40] = (0x80).to_bytes(4, 'little')
    header[0x80:0x84] = b'PE\0\0'
    header[0x88:0x8C] = (12345).to_bytes(4, 'little')          # timestamp
    header[0x80 + 24 + 56:0x80 + 24 + 60] = (0x1000).to_bytes(4, 'little')  # image size
    stock = bytes(header) + b'stock'
    with open(exe + '.original', 'wb') as f:
        f.write(stock)
    with open(exe, 'wb') as f:
        f.write(bytes(header) + b'patch')
    check(patcher.build_identity(exe) == patcher.build_identity(exe + '.original'),
          'same size and header: same build')
    patcher.retire_exe_patch(exe)
    with open(exe, 'rb') as f:
        check(f.read() == stock, 'the stock executable came back from the backup')
    check(not os.path.exists(exe + '.original'), 'the backup is gone')
    # a backup of another build is left alone
    with open(exe + '.original', 'wb') as f:
        f.write(stock + b'longer')
    patcher.retire_exe_patch(exe)
    with open(exe, 'rb') as f:
        check(f.read() == stock and os.path.exists(exe + '.original'),
              'a backup of a different build is neither restored nor removed')


wine_override()
loader_status()
if failures:
    print('\n%d failure(s)' % len(failures))
    sys.exit(1)
print('\nall good')
