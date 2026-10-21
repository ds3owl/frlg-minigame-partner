# PyInstaller: one windowed FRLG-Minigame-Partner.exe. Build: .venv\Scripts\pyinstaller frlg-minigame-partner.spec
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = [
    ("frlg_minigames/gui/web", "frlg_minigames/gui/web"),
    ("firmware/esp32-radio.bin", "firmware"),
    ("firmware/NOTICE.md", "firmware"),
    ("third_party/LDN/LICENSE", "third_party/LDN"),
    ("third_party/LDN/UPSTREAM.md", "third_party/LDN"),
    ("LICENSE", "."),
    ("THIRD_PARTY_NOTICES.md", "."),
] + collect_data_files("esptool")

a = Analysis(
    ["frlg_minigames/__main__.py"],
    pathex=[".", "third_party/LDN"],
    datas=datas,
    hiddenimports=collect_submodules("frlg_minigames") + collect_submodules("ldn") + collect_submodules("esptool"),
    excludes=["tkinter", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="FRLG-Minigame-Partner",
    console=False,
    upx=False,
)
