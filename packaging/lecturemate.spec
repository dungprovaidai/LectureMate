# PyInstaller spec — bản portable thư mục (onedir) cho Windows x64.
# Build: powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
datas, binaries, hidden = [], [], []

# dữ liệu của chính ứng dụng
datas += [(os.path.join(ROOT, "src", "lecturemate", "persistence", "migrations"), "lecturemate/persistence/migrations"),
          (os.path.join(ROOT, "src", "lecturemate", "ai", "prompts"), "lecturemate/ai/prompts")]

# ASR: ctranslate2 (DLL), faster_whisper (silero VAD .onnx), onnxruntime, tokenizers, av
for pkg in ("ctranslate2", "faster_whisper", "onnxruntime", "tokenizers", "av"):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hidden += h
    except Exception as exc:
        print("WARN collect", pkg, exc)

# SoundCard dùng cffi + file header .h đi kèm package
datas += collect_data_files("soundcard")
hidden += ["soundcard.mediafoundation", "_cffi_backend"]
# keyring: backend Windows Credential Manager
hidden += collect_submodules("keyring.backends")

a = Analysis(
    [os.path.join(ROOT, "packaging", "launcher.py")],
    pathex=[os.path.join(ROOT, "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden + ["lecturemate"],
    excludes=["tkinter", "matplotlib", "pytest", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="LectureMate", console=False,
          icon=None, version=None, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="LectureMate")
