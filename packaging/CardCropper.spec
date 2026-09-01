# PyInstaller build for CardCropper.
#
# One file, no console. `--windowed` matters more than it looks: without it
# Windows opens a command prompt behind the app, and the first thing a person
# does with an unexpected black window is close it, which takes the app with it.
#
# Run from the repository root:
#     pyinstaller packaging/CardCropper.spec --noconfirm

import os

block_cipher = None

a = Analysis(
    ["entry.py"],
    pathex=[os.path.abspath(".")],
    binaries=[],
    datas=[],
    hiddenimports=["PIL._tkinter_finder"],
    hookspath=[],
    runtime_hooks=[],
    # numpy pulls in its test suite and every plotting backend it can find;
    # none of it is reachable from here and it doubles the binary.
    excludes=["matplotlib", "scipy", "pandas", "pytest", "numpy.testing",
              "PIL.ImageQt", "PyQt5", "PySide2", "IPython"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="CardCropper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
