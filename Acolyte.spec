# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

root=Path(SPECPATH)
datas=[(str(root/'VERSION'),'.')]
binaries=[]
hiddenimports=[]

for package in (
    'customtkinter','piper','faster_whisper','ctranslate2','tokenizers',
    'onnxruntime','sounddevice','av','huggingface_hub','pynput','psutil'
):
    try:
        d,b,h=collect_all(package)
        datas+=d
        binaries+=b
        hiddenimports+=h
    except Exception:
        pass

for package in ('piper','faster_whisper','pynput'):
    try:hiddenimports+=collect_submodules(package)
    except Exception:pass

hiddenimports+=['piper.download_voices','piper.voice','tkinter','tkinter.ttk']

icon_path=root/'build'/'Acolyte.ico'
icon=str(icon_path) if icon_path.exists() else None

a=Analysis(
    ['coach.py'],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=list(dict.fromkeys(hiddenimports)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz=PYZ(a.pure)

exe=EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Acolyte',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
    uac_admin=True,
    version=None,
)
