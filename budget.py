"""Logique de calcul du budget a enveloppes (zero-based budgeting), pure et
independante de la base de donnees et de l'interface : etant donne les
sommes lues via Database, ces fonctions ne font que des additions/
soustractions simples, entierement deterministes et testables.

Principe (identique a la methode YNAB) : chaque categorie est une
"enveloppe". Le solde disponible d'une enveloppe pour un mois donne est la
somme cumulee de tout ce qui lui a ete assigne moins tout ce qui a ete
depense, depuis le debut - un solde positif non depense se reporte
automatiquement au mois suivant (rollover), un solde negatif (depense au-
dela du budget) se reporte aussi, reduisant d'autant le mois suivant tant
qu'il n'est pas comble par une nouvelle assignation.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Optional


_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


def month_key(iso_date: str) -> str:
    """Extrait 'YYYY-MM' du debut d'une chaine de date ISO."""
    return iso_date[:7]


def current_month() -> str:
    today = date.today()
    return f"{today.year:04d}-{today.month:02d}"


def shift_month(month: str, delta: int) -> str:
    """Renvoie le mois 'YYYY-MM' decale de `delta` mois (positif ou negatif)."""
    match = _MONTH_RE.match(month)
    if not match:
        raise ValueError(f"Format de mois invalide : {month!r} (attendu YYYY-MM)")
    year, mon = int(match.group(1)), int(match.group(2))
    total = year * 12 + (mon - 1) + delta
    new_year, new_month = divmod(total, 12)
    return f"{new_year:04d}-{new_month + 1:02d}"


def month_label(month: str) -> str:
    """Libelle lisible en francais pour un mois 'YYYY-MM'."""
    match = _MONTH_RE.match(month)
    if not match:
        return month
    names = [
        "Janvier", "Fevrier", "Mars", "Avril", "Mai", "Juin",
        "Juillet", "Aout", "Septembre", "Octobre", "Novembre", "Decembre",
    ]
    year, mon = int(match.group(1)), int(match.group(2))
    return f"{names[mon - 1]} {year}"


def category_available(db, category_id: int, month: str) -> float:
    """Solde disponible de l'enveloppe a la fin du mois donne (cumul depuis
    toujours : assignations - depenses, report inclus)."""
    assigned = db.sum_assigned_up_to(category_id, month)
    activity = db.sum_transactions_up_to(category_id, month)
    return round(assigned + activity, 2)


def category_activity_for_month(db, category_id: int, month: str) -> float:
    """Depense/revenu de cette seule categorie pour ce mois precis (pas
    cumule) - toujours <= 0 pour une depense normale."""
    return db.sum_transactions_for_month(category_id, month)


def ready_to_assign(db, month: Optional[str] = None) -> float:
    """Argent disponible mais pas encore assigne a une enveloppe.

    C'est le total reel sur les comptes moins ce qui est actuellement "range"
    dans une enveloppe (son solde disponible, report inclus) - PAS moins le
    total assigne depuis toujours. La difference compte : depenser de
    l'argent deja assigne reduit a la fois le solde du compte ET le solde de
    l'enveloppe du meme montant, donc ne doit jamais faire bouger le reste a
    assigner. (sum(solde des enveloppes) + reste a assigner == solde total)."""
    month = month or current_month()
    categories = db.list_categories(include_archived=False)
    total_in_envelopes = sum(category_available(db, cat["id"], month) for cat in categories)
    return round(db.total_on_budget_balance() - total_in_envelopes, 2)


def format_amount(amount: float, currency: str = "EUR") -> str:
    return f"{amount:,.2f} {currency}".replace(",", " ")
