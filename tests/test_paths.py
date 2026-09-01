"""
Checks patcher.py's game search and Engine.ini lookup against a fake Steam
layout built in a temporary home directory. Plain Python, no test framework:

    python tests/test_paths.py

This is the only place the Linux paths are ever exercised: the fix is
developed on Windows, and CI's Ubuntu runner is the one Linux it sees. The
fake layout has the Steam install in ~/.local/share/Steam, a second library
registered in its libraryfolders.vdf with the older SteamApps spelling, and
the game inside that second library - which is where a Steam Deck's SD card
or a separate drive on any distribution puts it. The test then checks that
the Proton prefix is found from the game's own path, that it is not claimed
before the game's first launch, that --engine-ini overrides the search, and
that the managed block round-trips inside the prefix.
"""
import io
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

failures = []


def check(cond, what):
    if not cond:
        failures.append(what)
        print('FAIL ' + what)


def same(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def main():
    tmp = tempfile.mkdtemp(prefix='lisde-paths-')
    try:
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('%d checks failed' % len(failures) if failures else 'all path checks passed')
    sys.exit(1 if failures else 0)


def run(tmp):
    home = os.path.join(tmp, 'home')
    library = os.path.join(tmp, 'Library2')
    for var in ('LOCALAPPDATA', 'PROGRAMDATA'):
        os.environ.pop(var, None)
    os.environ['HOME'] = home
    os.environ['USERPROFILE'] = home            # what expanduser reads on Windows

    steam = os.path.join(home, '.local', 'share', 'Steam')
    os.makedirs(os.path.join(steam, 'steamapps'))
    with io.open(os.path.join(steam, 'steamapps', 'libraryfolders.vdf'), 'w', encoding='utf-8') as f:
        f.write('"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n\t"1"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n'
                % (steam.replace('\\', '\\\\'), library.replace('\\', '\\\\')))
    flatpak = os.path.join(home, '.var', 'app', 'com.valvesoftware.Steam', 'data', 'Steam')
    snap = os.path.join(home, 'snap', 'steam', 'common', '.local', 'share', 'Steam')
    os.makedirs(flatpak)
    os.makedirs(snap)

    import patcher
    exe = os.path.join(library, 'SteamApps', 'common', patcher.GAME_DIR_NAME, patcher.EXE_RELATIVE)
    os.makedirs(os.path.dirname(exe))
    with open(exe, 'wb') as f:
        f.write(b'MZ')

    installs = patcher._steam_installs()
    for root in (steam, flatpak, snap):
        check(any(same(root, p) for p in installs), 'Steam root not searched: ' + root)
    check(any(same(library, p) for p in patcher._steam_libraries()),
          'library from libraryfolders.vdf not searched: ' + library)
    found = [(p, s) for p, s in patcher._exe_candidates() if same(p, exe)]
    check(found, 'the game in the second library was not found')
    check(found and found[0][1].startswith('Steam library'),
          'the game was found, but not through Steam: %r' % (found[:1],))

    # the prefix does not exist until the game has been started once
    check(patcher.engine_ini_path(exe) is None, 'an Engine.ini path was returned with no prefix')
    prefix = os.path.join(library, 'SteamApps', 'compatdata', patcher.STEAM_APPID, 'pfx')
    os.makedirs(os.path.join(prefix, 'drive_c'))
    want = os.path.join(prefix, 'drive_c', 'users', 'steamuser', 'AppData', 'Local',
                        'Chronos', 'Saved', 'Config', 'Windows', 'Engine.ini')
    got = patcher.engine_ini_path(exe)
    check(got and same(got, want), 'Engine.ini not found from the game path: %r' % got)
    # ...and through the libraries alone, when the game path gives no lead
    got = patcher.engine_ini_path(os.path.join(tmp, 'elsewhere', patcher.EXE_NAME))
    check(got and same(got, want), 'Engine.ini not found through the libraries: %r' % got)
    override = os.path.join(tmp, 'heroic', 'Engine.ini')
    check(same(patcher.engine_ini_path(exe, override), override), '--engine-ini was not honoured')

    # the managed block goes in and comes out again, creating the folders
    patcher.apply_engine_ini(exe, 5120, 2160, True, True)
    check(os.path.isfile(want), 'Engine.ini was not written into the prefix')
    with io.open(want, encoding='utf-8') as f:
        check(patcher.INI_BEGIN.strip() in f.read(), 'the managed block is missing')
    patcher.apply_engine_ini(exe, 5120, 2160, False, False, remove=True)
    with io.open(want, encoding='utf-8') as f:
        check(f.read() == '', 'the managed block was not removed')

    # a permission error reads the same on every platform
    ex = OSError(13, 'Permission denied')
    check('Windows' not in patcher.write_failure(exe, ex), 'permission wording names Windows')
    detected = patcher.detect_resolution()
    check(detected is None or (len(detected) == 2 and min(detected) > 0),
          'detect_resolution returned %r' % (detected,))


if __name__ == '__main__':
    main()
