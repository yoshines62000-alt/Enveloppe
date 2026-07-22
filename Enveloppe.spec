# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['gui.py'],
    pathex=[],
    binaries=[],
    datas=[('icon.ico', '.')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Enveloppe',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icon.ico'],
    # Manifeste DPI-aware (audit D30) : voir Enveloppe.manifest pour le
    # detail et la justification complete. Sans lui, l'executable empaquete
    # n'est pas declare sensible au DPI et Windows applique un lissage
    # bitmap a tout le rendu des qu'un facteur d'echelle different de 100%
    # est actif (tres courant sur portables et ecrans modernes).
    # Complementaire de l'appel ctypes equivalent fait au demarrage de
    # gui.py (_configure_dpi_awareness), qui reste un filet de securite pour
    # une execution depuis le code source (non empaquetee).
    manifest='Enveloppe.manifest',
)
