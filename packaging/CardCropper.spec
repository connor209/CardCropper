# PyInstaller build for CardCropper.  Requires PyInstaller 6.x.
#
# Run from anywhere:
#     pyinstaller packaging/CardCropper.spec --noconfirm
#
# Two things this file has to get right, both of which cost a red build:
#
# **Script paths in a spec resolve against the SPEC's directory, not the
# working directory.** A bare "entry.py" here means packaging/entry.py, which
# does not exist. SPECPATH is injected by PyInstaller and is the only reliable
# way to name the repository root.
#
# **PyInstaller 6 removed the arguments 5.x specs carry.** Bytecode encryption
# went, taking `cipher` and `block_cipher` with it, along with `a.zipfiles`,
# `win_no_prefer_redirects` and `win_private_assemblies`. A spec copied from an
# older template raises a TypeError before it builds anything.

import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # noqa: F821  (injected)

a = Analysis(
    [os.path.join(ROOT, "entry.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[],
    # Pillow finds Tk through this shim, and PyInstaller cannot see the import
    # because it happens inside a try/except at runtime.
    hiddenimports=["PIL._tkinter_finder"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # None of this is reachable from here, and together it roughly doubles the
    # binary. numpy.testing is deliberately NOT excluded: numpy reaches it
    # through a lazy __getattr__, and excluding it breaks the import.
    excludes=["matplotlib", "scipy", "pandas", "pytest", "PIL.ImageQt",
              "PyQt5", "PyQt6", "PySide2", "PySide6", "IPython", "tornado"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="CardCropper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    # No console. Without this Windows opens a command prompt behind the app,
    # and the first thing a person does with an unexpected black window is
    # close it — which takes the app with it.
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
