# Changelog

Historique des changements notables d'Enveloppe, par version. Format
inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.0.0/) ;
versionnage inspiré de [SemVer](https://semver.org/lang/fr/).

## [Non publié]

Correctifs issus de la dernière passe de l'audit expert (backlog priorisé
qualité/effort), en attente de publication :

### Ajouté
- Ligne de total agrégé (solde actuel + solde pointé, comptes archivés
  inclus) dans l'onglet Comptes.
- Bouton « Restaurer une sauvegarde... » dédié dans l'onglet Paramètres,
  sans avoir à fermer l'application ni à remplacer le fichier de données à
  la main.
- Notification au démarrage si un import CSV précédent a été interrompu
  brutalement (coupure de courant, plantage) avant de se terminer
  normalement.
- Icône `icon.ico` multi-résolution (16, 32, 48, 256 px) au lieu d'une
  unique résolution 16x16 ; script `build_icon.py` (Pillow) pour la
  régénérer.
- Suivi explicite de la version de schéma SQLite via `PRAGMA user_version`
  (`Database.schema_version()`).
- Détail (compte/date/montant/bénéficiaire) des doublons ignorés à l'import
  CSV, pour permettre une vérification manuelle rapide au lieu d'un simple
  compteur.
- `.python-version` et documentation de la version de Python utilisée pour
  les builds officiels.
- Ce fichier `CHANGELOG.md`.

### Modifié
- Colonne « Catégorie » de l'onglet Budget élargie (180 → 220 px) pour
  éviter la troncature du suffixe « (archivée) » sur les noms de catégorie
  déjà longs ; répartition proportionnelle de l'espace explicitement
  activée sur toutes les colonnes des tableaux.
- Calcul de `category_available` factorisé à un seul appel par catégorie
  et par rafraîchissement du Budget (au lieu de jusqu'à trois), via
  `budget.ready_to_assign_from_available`.
- Basculer le pointage ou supprimer une transaction ne reconstruit plus
  tout le tableau Transactions : seules les lignes concernées sont mises à
  jour ou retirées.
- Validation de formulaire (date ISO, montant non nul) factorisée dans des
  méthodes communes (`_parse_iso_date`, `_parse_nonzero_amount`) au lieu
  d'être dupliquée dans chaque formulaire d'ajout/édition.

## [1.0.14] - 2026-07-22

### Ajouté
- Raccourcis clavier Entrée (valider) et Échap (annuler) sur tous les
  formulaires et dialogues.
- Mise en évidence en rouge des soldes de comptes négatifs (même
  convention que les enveloppes en dépassement).
- Index SQLite sur `transactions.account_id`, par symétrie avec l'index
  existant sur `category_id`.

## [1.0.13] - 2026-07-22

### Corrigé
- Les 4 constats de gravité Modérée / priorité court terme relevés par
  l'audit expert, avec la couverture de tests associée.

## [1.0.12] - 2026-07-22

### Corrigé
- Les 7 constats de gravité Majeure / priorité court terme relevés par
  l'audit expert.

## [1.0.11] - 2026-07-22

### Corrigé
- Taille minimale de fenêtre (`minsize`) et troncatures d'interface qui en
  découlaient (indicateur « Reste à assigner » et boutons d'action pouvant
  disparaître ou se tronquer à certaines tailles de fenêtre).

## [1.0.10] - 2026-07-21

### Modifié
- Import CSV accéléré (commits par lots plutôt qu'un par ligne) et exécuté
  sur un thread séparé pour ne plus geler l'interface pendant l'opération.

## [1.0.9] - 2026-07-21

### Sécurité
- Neutralisation de l'injection de formule CSV (OWASP CSV Injection) à
  l'export des transactions.

## [1.0.8] - 2026-07-21

### Corrigé
- Rejet des montants infinis ou NaN à la saisie, à l'import CSV et en
  base, pour éviter la contamination silencieuse des soldes.

## [1.0.7] - 2026-07-20

### Ajouté
- Indicateur de version et de mise à jour disponible (vérification GitHub
  en arrière-plan, non bloquante).
- Déplacement d'argent entre enveloppes, sans changer le total assigné ni
  le reste à assigner.
- Rapprochement bancaire (pointage des transactions, solde pointé par
  compte).

### Corrigé
- Non-atomicité du déplacement entre enveloppes (risque de solde fantôme
  en cas d'interruption) et validation d'un montant infini.
- Validation des dates, export CSV des transactions fractionnées et
  virgule décimale ; ajout de la sauvegarde de la base et d'une alerte de
  dépassement d'enveloppe.

## Avant la première version taguée (juillet 2026)

Fonctionnalités ajoutées au fil du développement initial, avant le premier
tag de version (v1.0.7) :

- Version initiale : budget personnel à enveloppes (zero-based budgeting).
- Invariant du reste à assigner et validation des dates/mois.
- Édition de transaction, visibilité des catégories archivées, copie du
  budget du mois précédent, indicateur de dépassement.
- Onglet Rapports (tendances de dépenses réelles par catégorie).
- Export et import CSV des transactions.
- Onglet Vue annuelle du budget.
- Virements entre comptes liés (deux transactions couplées).
- Fractionnement d'une transaction sur plusieurs catégories.
- Protection de l'édition standard contre la casse silencieuse d'un
  virement ou d'un fractionnement.
- Détection et ignorance des doublons exacts à l'import CSV.
- Transactions récurrentes (hebdomadaire, mensuelle, annuelle).
- Objectif d'épargne optionnel par catégorie.
- Correction de la dérive du jour d'échéance sur les mois courts et de la
  confusion heure locale/UTC pour les échéances récurrentes.

[Non publié]: https://github.com/yoshines62000-alt/Enveloppe/compare/v1.0.14...HEAD
[1.0.14]: https://github.com/yoshines62000-alt/Enveloppe/releases/tag/v1.0.14
[1.0.13]: https://github.com/yoshines62000-alt/Enveloppe/releases/tag/v1.0.13
[1.0.12]: https://github.com/yoshines62000-alt/Enveloppe/releases/tag/v1.0.12
[1.0.11]: https://github.com/yoshines62000-alt/Enveloppe/releases/tag/v1.0.11
[1.0.10]: https://github.com/yoshines62000-alt/Enveloppe/releases/tag/v1.0.10
[1.0.9]: https://github.com/yoshines62000-alt/Enveloppe/releases/tag/v1.0.9
[1.0.8]: https://github.com/yoshines62000-alt/Enveloppe/releases/tag/v1.0.8
[1.0.7]: https://github.com/yoshines62000-alt/Enveloppe/releases/tag/v1.0.7
