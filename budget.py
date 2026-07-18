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


_MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


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
        raise ValueError(f"Format de mois invalide : {month!r} (attendu YYYY-MM)")
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
    assigner. (sum(solde des enveloppes) + reste a assigner == solde total).

    Comptes ET categories archives sont INCLUS dans ce calcul (via
    include_archived=True) : "archiver" ne signifie que "masquer des listes
    deroulantes pour les nouvelles saisies", jamais "faire disparaitre de
    l'equation" - sinon archiver un compte qui contient encore de l'argent
    reel, ou une categorie qui contient encore un solde, fausserait le
    reste a assigner sans qu'aucun argent n'ait reellement bouge."""
    month = month or current_month()
    categories = db.list_categories(include_archived=True)
    total_in_envelopes = sum(category_available(db, cat["id"], month) for cat in categories)
    return round(db.total_on_budget_balance() - total_in_envelopes, 2)


def spending_report(db, end_month: Optional[str] = None, num_months: int = 6) -> dict:
    """Rapport de depenses par categorie sur les `num_months` mois se
    terminant a `end_month` inclus (par defaut le mois courant). Pour
    chaque categorie, ne retient que les depenses reelles du mois (activite
    negative) - un remboursement ou un revenu range dans une categorie ne
    doit pas faire "baisser" ses depenses affichees. Les categories
    archivees sont incluses (leur historique de depenses reste pertinent),
    mais une categorie sans aucune depense sur la periode est omise."""
    end_month = end_month or current_month()
    months = []
    month = end_month
    for _ in range(num_months):
        months.append(month)
        month = shift_month(month, -1)
    months.reverse()

    rows = []
    for category in db.list_categories(include_archived=True):
        amounts = {}
        total = 0.0
        for month in months:
            activity = category_activity_for_month(db, category["id"], month)
            spent = round(-activity, 2) if activity < 0 else 0.0
            amounts[month] = spent
            total += spent
        if total > 0:
            rows.append({
                "category_id": category["id"],
                "name": category["name"],
                "group_name": category["group_name"],
                "amounts": amounts,
                "total": round(total, 2),
            })
    rows.sort(key=lambda row: row["total"], reverse=True)
    return {"months": months, "rows": rows}


def annual_budget_overview(db, year: int) -> dict:
    """Vue annuelle : montant assigne a chaque categorie pour chacun des 12
    mois de `year`. Contrairement a spending_report (qui ne montre que les
    depenses reelles), ceci montre le PLAN budgetaire tel que saisi dans
    l'onglet Budget - utile pour reperer d'un coup d'oeil les mois sans
    aucune assignation avant qu'ils n'arrivent. Une categorie jamais
    assignee de toute l'annee est omise."""
    months = [f"{year:04d}-{m:02d}" for m in range(1, 13)]
    rows = []
    for category in db.list_categories(include_archived=True):
        amounts = {month: db.get_budget_entry(category["id"], month) for month in months}
        if not any(amounts.values()):
            continue
        rows.append({
            "category_id": category["id"], "name": category["name"], "group_name": category["group_name"],
            "amounts": amounts, "total": round(sum(amounts.values()), 2),
        })
    rows.sort(key=lambda row: (row["group_name"], row["name"]))
    return {"year": year, "months": months, "rows": rows}


def savings_goal_progress(available: float, goal: Optional[float]) -> Optional[dict]:
    """Progres vers l'objectif d'epargne d'une categorie, ou None si aucun
    objectif n'est defini. Un solde negatif (categorie en depassement) donne
    un pourcentage de 0, jamais negatif - un depassement n'est pas un
    "progres negatif" vers l'objectif, juste une absence de progres. Le
    pourcentage est plafonne a 100 : depasser l'objectif reste "atteint",
    pas "150% atteint"."""
    if not goal or goal <= 0:
        return None
    percent = max(0.0, min(100.0, round(available / goal * 100)))
    return {"available": available, "goal": goal, "percent": percent, "reached": available >= goal}


def format_amount(amount: float, currency: str = "EUR") -> str:
    return f"{amount:,.2f} {currency}".replace(",", " ")
