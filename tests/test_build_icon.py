"""Tests pour build_icon.py (audit D35) : icon.ico ne doit plus contenir
qu'une seule resolution (16x16, verifie a l'audit par inspection directe de
PIL.Image.open("icon.ico").info["sizes"]) - Windows agrandissait sinon lui-
meme cette unique image (lissage bitmap brut) pour la barre des taches
(32x32), le switcher Alt-Tab (48x48) et les grandes icones de l'Explorateur
(jusqu'a 256x256), produisant un rendu flou/pixelise partout sauf dans le
coin superieur gauche de la fenetre.

Pillow est une dependance de BUILD uniquement (voir requirements.txt),
jamais garantie a l'execution de la suite de tests dans tous les
environnements - ce module est donc entierement ignore (skip propre, pas un
echec) si PIL n'est pas installe."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from PIL import Image
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False


@unittest.skipUnless(_PIL_AVAILABLE, "Pillow n'est pas installe (dependance de build uniquement)")
class BuildIconTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _make_source_icon(self, size=(16, 16)) -> Path:
        source = self.tmp / "source.png"
        Image.new("RGBA", size, (10, 20, 30, 255)).save(source, format="PNG")
        return source

    def test_generate_icon_produces_all_four_standard_sizes_from_a_small_source(self):
        import build_icon

        source = self._make_source_icon((16, 16))
        destination = self.tmp / "icon.ico"
        build_icon.generate_icon(source, destination)

        with Image.open(destination) as result:
            sizes = sorted(result.info.get("sizes", []))
        self.assertEqual(sizes, [(16, 16), (32, 32), (48, 48), (256, 256)])

    def test_generate_icon_works_from_a_source_already_larger_than_every_target_size(self):
        import build_icon

        source = self._make_source_icon((512, 512))
        destination = self.tmp / "icon.ico"
        build_icon.generate_icon(source, destination)

        with Image.open(destination) as result:
            sizes = sorted(result.info.get("sizes", []))
        self.assertEqual(sizes, [(16, 16), (32, 32), (48, 48), (256, 256)])

    def test_generate_icon_can_overwrite_its_own_source_file_in_place(self):
        # Usage par defaut de build_icon.py (sans argument) : relit et
        # reecrit icon.ico sur lui-meme.
        import build_icon

        icon_path = self.tmp / "icon.ico"
        Image.new("RGBA", (16, 16), (1, 2, 3, 255)).save(icon_path, format="ICO", sizes=[(16, 16)])
        with Image.open(icon_path) as before:
            self.assertEqual(sorted(before.info.get("sizes", [])), [(16, 16)])

        build_icon.generate_icon(icon_path, icon_path)

        with Image.open(icon_path) as after:
            sizes = sorted(after.info.get("sizes", []))
        self.assertEqual(sizes, [(16, 16), (32, 32), (48, 48), (256, 256)])

    def test_the_repository_icon_ico_is_actually_multi_resolution(self):
        # Verrouille le resultat concret du correctif D35 sur le fichier
        # reellement livre avec l'application (pas seulement la fonction de
        # generation en isolation) - regression lock : si icon.ico est un
        # jour remplace par une version mono-resolution par erreur, ce test
        # doit echouer.
        repo_icon = Path(__file__).resolve().parent.parent / "icon.ico"
        with Image.open(repo_icon) as img:
            sizes = img.info.get("sizes", set())
        self.assertGreaterEqual(
            len(sizes), 2, "icon.ico du depot ne contient qu'une seule resolution (mono-resolution)",
        )
        self.assertIn((256, 256), sizes, "icon.ico du depot devrait inclure une resolution 256x256")


if __name__ == "__main__":
    unittest.main()
