"""Regenere icon.ico en multi-resolution (16, 32, 48, 256) a partir d'une
image source, via Pillow (audit D35) : le fichier livre jusqu'ici ne
contenait qu'une SEULE resolution (16x16, verifie par inspection directe de
`PIL.Image.open("icon.ico").info["sizes"]`), forcant Windows a l'agrandir
lui-meme (lissage bitmap brut, sans anti-aliasing prepare a l'avance) pour la
barre des taches (32x32 typique), le switcher Alt-Tab (48x48) et les grandes
icones de l'Explorateur (jusqu'a 256x256) - un rendu flou/pixelise partout
sauf dans le coin superieur gauche de la fenetre.

Usage :
    python build_icon.py [source] [destination]

Sans argument, relit et reecrit icon.ico sur lui-meme a partir de sa propre
image existante - au mieux du possible en l'absence d'une source haute
resolution separee dans le depot (la 16x16 existante est agrandie par
rechantillonnage LANCZOS avant l'ecriture des tailles demandees). Fournir un
PNG source de meilleure qualite en premier argument (ex: `python
build_icon.py source_512.png icon.ico`) donnera un resultat plus net a
grande taille que ce plafond.

Ce script n'est PAS execute au lancement de l'application (voir
requirements.txt : Pillow est une dependance de BUILD uniquement, jamais
embarquee dans l'executable final au-dela du fichier .ico statique qu'il
produit ici, en amont, une fois pour toutes)."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

# Tailles standard Windows pour une icone d'executable : 16x16 (barre de
# titre/menu), 32x32 (barre des taches), 48x48 (Alt-Tab, Explorateur en
# icones moyennes), 256x256 (Explorateur en grandes icones/apercu).
_SIZES = [(16, 16), (32, 32), (48, 48), (256, 256)]


def generate_icon(source: Path, destination: Path) -> None:
    with Image.open(source) as original:
        base = original.convert("RGBA")
        largest = max(_SIZES)
        if base.size[0] < largest[0] or base.size[1] < largest[1]:
            # Le plugin ICO de Pillow ignore silencieusement toute taille
            # demandee plus grande que l'image source fournie a save()
            # (verifie empiriquement) - sans ce pre-agrandissement explicite,
            # partir d'une source 16x16 (le cas type ici, faute de source
            # haute resolution separee dans le depot) produirait un fichier
            # .ico strictement identique au mono-resolution original, malgre
            # le parametre `sizes` demande ci-dessous.
            base = base.resize(largest, Image.LANCZOS)
        base.save(destination, format="ICO", sizes=_SIZES)


def main() -> None:
    root = Path(__file__).resolve().parent
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "icon.ico"
    destination = Path(sys.argv[2]) if len(sys.argv) > 2 else root / "icon.ico"
    generate_icon(source, destination)
    with Image.open(destination) as result:
        sizes = sorted(result.info.get("sizes", []))
    print(f"{destination} regenere : tailles = {sizes}")


if __name__ == "__main__":
    main()
