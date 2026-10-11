# Build:  pyinstaller app.spec   (lean demo-mode binary; AI stacks excluded)
a = Analysis(
    ["main.py"],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    excludes=["torch", "diffusers", "transformers", "accelerate", "rembg",
              "tkinter", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, exclude_binaries=True, name="ai-image-studio",
          console=False, upx=False)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name="ai-image-studio")
