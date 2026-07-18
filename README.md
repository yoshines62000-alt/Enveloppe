# Enveloppe

[![Dernière version](https://img.shields.io/github/v/release/yoshines62000-alt/Enveloppe?label=derni%C3%A8re%20version)](https://github.com/yoshines62000-alt/Enveloppe/releases/latest)
[![Téléchargements](https://img.shields.io/github/downloads/yoshines62000-alt/Enveloppe/total?label=t%C3%A9l%C3%A9chargements)](https://github.com/yoshines62000-alt/Enveloppe/releases/latest)

**[⬇️ Télécharger l'exécutable (.exe) — aucune installation requise](https://github.com/yoshines62000-alt/Enveloppe/releases/latest)**

Budget personnel par la méthode des enveloppes (zero-based budgeting) —
gratuit, open source, et 100 % local. Alternative libre à
[YNAB](https://www.ynab.com/pricing) (109 $/an), sans lier vos comptes
bancaires à un service tiers : vous saisissez vos comptes et vos
transactions vous-même, tout reste sur votre ordinateur.

## Le principe (methode des enveloppes)

Chaque euro que vous avez reçoit une mission : vous répartissez votre
argent disponible dans des catégories ("enveloppes" — Loyer, Courses,
Loisirs...). Ce que vous ne dépensez pas dans une enveloppe se reporte
automatiquement au mois suivant ; si vous dépensez plus que ce qui y était
prévu, le dépassement se reporte aussi et réduit d'autant le mois suivant,
tant que vous n'y réassignez pas d'argent. Vous savez ainsi en permanence
combien vous pouvez encore dépenser dans chaque catégorie, sans vous baser
sur un simple solde de compte.

## Fonctionnalités

- **Comptes multiples** : courant, épargne, espèces... chacun avec son
  solde de départ et son solde actuel calculé automatiquement.
- **Catégories groupées** : organisez vos enveloppes par groupe (ex :
  "Obligations", "Loisirs") pour une vue plus claire.
- **Budget mensuel avec navigation** : parcourez les mois passés et futurs,
  assignez un montant à chaque catégorie, et voyez immédiatement l'activité
  et le disponible (report inclus) pour le mois affiché.
- **"Reste à assigner"** : indicateur toujours visible de l'argent que vous
  avez mais n'avez pas encore donné a une enveloppe.
- **Transactions** : ajoutez vos dépenses/revenus avec compte, catégorie,
  bénéficiaire et montant ; filtrez par compte.
- **100 % local, zéro cloud** : aucune connexion bancaire, aucun compte,
  aucune télémétrie. Vos données financières ne quittent jamais votre
  machine.
- **Gratuit et open source, pour toujours** : pas de version payante, pas
  de fonctionnalité verrouillée derrière un abonnement.

## Démarrage rapide

1. [**Téléchargez `Enveloppe.exe`**](https://github.com/yoshines62000-alt/Enveloppe/releases/latest)
   depuis la dernière release.
2. Double-cliquez dessus : la fenêtre de l'application s'ouvre directement,
   sans installation, sans Python.

L'exécutable n'étant pas signé numériquement, Windows SmartScreen peut
afficher un avertissement au premier lancement : cliquez sur **Informations
complémentaires** puis **Exécuter quand même**.

## Lancer depuis le code source

Alternative à l'exécutable, pour les développeurs ou par souci de
transparence : double-cliquez sur **[`Lancer.vbs`](Lancer.vbs)** — la
fenêtre s'ouvre directement, sans console. Aucune dépendance tierce n'est
nécessaire, seul Python avec Tkinter suffit (inclus dans les installations
standard de Python sous Windows).

## Utilisation

1. Onglet **Comptes** : ajoutez vos comptes avec leur solde de départ.
2. Onglet **Catégories** : créez vos enveloppes (ex : Loyer, Courses,
   Loisirs), avec un groupe optionnel pour les organiser.
3. Onglet **Budget** : assignez un montant à chaque catégorie pour le mois
   affiché (double-clic sur une ligne). Le "Reste à assigner" en haut à
   droite indique combien d'argent n'est pas encore affecté.
4. Onglet **Transactions** : enregistrez vos dépenses et revenus. Une
   dépense catégorisée réduit automatiquement le disponible de son
   enveloppe ; un revenu sans catégorie augmente le "Reste à assigner".

## Confidentialité

- Aucune donnée ne quitte votre machine : pas de compte, pas de serveur, pas
  de télémétrie, aucune connexion à votre banque.
- Les données sont stockées dans `%APPDATA%\Enveloppe\enveloppe.sqlite`.

## Créer un exécutable autonome (.exe)

Pour distribuer l'outil sans que le destinataire ait besoin d'installer
Python, un exécutable Windows autonome peut être généré avec
[PyInstaller](https://pyinstaller.org/) :

```bash
python -m pip install pyinstaller
python -m PyInstaller Enveloppe.spec
```

L'exécutable est produit dans `dist/Enveloppe.exe` (fichier unique, sans
console). Le fichier `.spec` du dépôt fixe la configuration de build pour un
résultat reproductible. Les dossiers `build/` et `dist/` ne sont pas suivis
par Git.

## Tests

Une suite de tests automatisés couvre toute la logique de calcul (report
d'enveloppe positif et négatif, "reste à assigner", navigation entre mois)
sur une vraie base SQLite temporaire.

```bash
python -m unittest discover tests -v
```

## Structure du projet

```
db.py                 # couche donnees SQLite : comptes, categories, budget, transactions
budget.py             # logique pure du budget a enveloppes (report, reste a assigner)
gui.py                 # interface graphique Tkinter
tests/                 # tests automatises
requirements.txt      # aucune dependance tierce a l'execution
Lancer.vbs            # raccourci de lancement double-clic (sans console)
Lancer.bat            # raccourci de lancement double-clic (avec console, pour debug)
Enveloppe.spec        # configuration de build PyInstaller (.exe autonome)
icon.ico              # icone de l'application et de l'executable
.gitignore
LICENSE               # licence MIT
README.md
```

## Licence

Ce projet est publié sous licence [MIT](LICENSE) : gratuit, open source, et
libre de réutilisation, modification et redistribution.

## Soutenir le projet

<div align="center">

**Cet outil est gratuit, open source, et le restera toujours.**
Pas de version payante, pas de fonctionnalité cachée derrière un paywall.

Si Enveloppe vous aide à garder un œil sur votre budget sans abonnement, un
petit café est toujours très apprécié. 🙌

[![Offrez-moi un café sur Ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/yoshines62000)

</div>
