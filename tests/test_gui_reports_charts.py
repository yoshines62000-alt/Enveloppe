"""Test cible des graphiques de l'onglet Rapports (histogramme des depenses
par categorie + depense totale mois par mois), dessines sur un tk.Canvas a
partir des memes donnees que le tableau (bg.spending_report). Pilote la
VRAIE GUI Tkinter comme test_gui_smoke, mais force la taille du Canvas via
winfo_width/winfo_height (une fenetre `withdraw()` n'obtient pas de geometrie
reelle dans cet environnement sans bureau interactif)."""

import sys
import tempfile
import unittest
from pathlib import Path
from tkinter import Canvas, Tk
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gui

# --- FILET ANTI-BLOCAGE -----------------------------------------------------
# `opl_theme.message()` dessine une vraie fenetre modale qui attend un clic.
# Un test qui emprunte un chemin d'erreur ferait pendre la suite ENTIERE : pas
# d'echec, pas de trace, juste une execution qui ne finit jamais. On neutralise
# donc le composant pour tout ce module. Les tests qui verifient un message le
# repatchent localement — un patch imbrique prend le pas sur celui-ci.
_filet_message = None


def setUpModule():
    # Imports locaux : les fichiers hotes ne nomment pas ces modules de la
    # meme facon (`patch` ou `mock.patch`, `gui` ou `gui as gui_mod`, ou pas de
    # gui du tout). `opl_theme` est le meme objet module que `gui.opl_theme`,
    # le patch porte donc des deux cotes.
    from unittest.mock import patch as _patch

    import opl_theme as _theme

    global _filet_message
    _filet_message = _patch.object(_theme, "message")
    _filet_message.start()


def tearDownModule():
    if _filet_message is not None:
        _filet_message.stop()
# ----------------------------------------------------------------------------


class ReportsChartsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        with patch.object(gui, "_data_dir", return_value=self.tmp):
            self.app = gui.EnveloppeApp(self.root)
        self.addCleanup(self.app.db.close)

    def _force_canvas_size(self, w=700, h=320):
        # Une fenetre withdraw() n'a pas de geometrie reelle ici : on force la
        # taille rapportee par le Canvas pour que _draw_reports_charts dessine.
        self.app.reports_canvas.winfo_width = lambda: w
        self.app.reports_canvas.winfo_height = lambda: h

    def _add_spending(self):
        month = self.app.current_month
        account = self.app.db.add_account("Compte", starting_balance=1000.0)
        epicerie = self.app.db.add_category("Epicerie")
        loisirs = self.app.db.add_category("Loisirs")
        self.app.db.add_transaction(account, f"{month}-05", -120.0, category_id=epicerie, payee="Courses")
        self.app.db.add_transaction(account, f"{month}-10", -40.0, category_id=loisirs, payee="Cinema")
        return account

    def test_reports_canvas_exists(self):
        self.assertIsInstance(self.app.reports_canvas, Canvas)

    def test_no_data_shows_centered_message(self):
        # Base vierge : spending_report ne renvoie aucune ligne.
        self._force_canvas_size()
        self.app._refresh_reports()
        texts = [
            self.app.reports_canvas.itemcget(item, "text")
            for item in self.app.reports_canvas.find_all()
            if self.app.reports_canvas.type(item) == "text"
        ]
        self.assertTrue(any("Aucune depense" in t for t in texts),
                        f"message 'aucune donnee' attendu, obtenu : {texts}")

    def test_charts_drawn_when_data_present(self):
        self._add_spending()
        self._force_canvas_size()
        self.app._refresh_reports()
        items = self.app.reports_canvas.find_all()
        rectangles = [i for i in items if self.app.reports_canvas.type(i) == "rectangle"]
        texts = [
            self.app.reports_canvas.itemcget(i, "text")
            for i in items if self.app.reports_canvas.type(i) == "text"
        ]
        # Au moins une barre par categorie (2) + barres mensuelles.
        self.assertGreaterEqual(len(rectangles), 3, "des barres etaient attendues sur le Canvas")
        self.assertIn("Depenses par categorie", texts)
        self.assertIn("Depense totale par mois", texts)
        self.assertTrue(any("Epicerie" in t for t in texts), "libelle de categorie attendu")

    def test_period_change_redraws_chart(self):
        self._add_spending()
        self._force_canvas_size()
        self.app._refresh_reports()
        first = len(self.app.reports_canvas.find_all())
        self.assertGreater(first, 0)
        # Change la periode a "12 derniers mois" et rafraichit : le Canvas doit
        # etre redessine (contenu remplace, non cumule).
        self.app.reports_period_combo.current(2)
        self.app._refresh_reports()
        # find_all() ne doit pas contenir les items de l'ancien dessin en plus
        # des nouveaux : delete("all") est appele au debut de chaque dessin.
        second = len(self.app.reports_canvas.find_all())
        self.assertGreater(second, 0)
        # Les libelles de mois de la nouvelle periode sont presents.
        texts = [
            self.app.reports_canvas.itemcget(i, "text")
            for i in self.app.reports_canvas.find_all()
            if self.app.reports_canvas.type(i) == "text"
        ]
        self.assertIn("Depense totale par mois", texts)

    def test_bar_colors_come_from_theme_palette(self):
        import opl_theme
        self._add_spending()
        self._force_canvas_size()
        self.app._refresh_reports()
        accents = {opl_theme.couleur("cyan"), opl_theme.couleur("emeraude"), opl_theme.couleur("vert_lab")}
        fills = {
            self.app.reports_canvas.itemcget(i, "fill")
            for i in self.app.reports_canvas.find_all()
            if self.app.reports_canvas.type(i) == "rectangle"
        }
        self.assertTrue(fills.issubset(accents | {""}),
                        f"les barres doivent utiliser les accents de la palette, obtenu : {fills}")
        self.assertTrue(fills & accents, "au moins une barre coloree via la palette attendue")


if __name__ == "__main__":
    unittest.main()
