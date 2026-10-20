# -*- mode: python ; coding: utf-8 -*-
# Сборка: pyinstaller chessium.spec  (или build.ps1)
import re

version = re.search(r'__version__ = "(.+?)"', open("version.py", encoding="utf-8").read()).group(1)
nums = [int(x) for x in version.split(".")] + [0] * (4 - len(version.split(".")))

version_info = f"""
VSVersionInfo(
  ffi=FixedFileInfo(filevers={tuple(nums)}, prodvers={tuple(nums)}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('041904B0', [
      StringStruct('CompanyName', 'Chessium'),
      StringStruct('FileDescription', 'Chessium — анализ шахматных партий с экрана'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'Chessium'),
      StringStruct('OriginalFilename', 'Chessium.exe'),
      StringStruct('ProductName', 'Chessium'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1049, 1200])])
  ]
)
"""
with open("build/version_info.txt", "w", encoding="utf-8") as f:
    f.write(version_info)

a = Analysis(
    ["app.py"],
    pathex=[],
    datas=[("assets/icon.ico", "assets"), ("assets/icon.png", "assets"), ("openings.tsv", ".")],
    hiddenimports=["websocket", "PyQt6.QtSvg"],
    excludes=["tkinter", "PyQt6.QtWebEngineCore", "PyQt6.QtQml", "PyQt6.QtQuick", "PyQt6.QtMultimedia",
              "PyQt6.QtNetwork", "PyQt6.QtPdf", "PyQt6.Qt3DCore", "matplotlib", "scipy", "pandas"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Chessium",
    icon="assets/icon.ico",
    version="build/version_info.txt",
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Chessium", upx=False)
