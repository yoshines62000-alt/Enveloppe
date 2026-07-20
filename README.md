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
  solde de départ, son solde actuel et son solde **pointé** (rapprochement
  bancaire, voir plus bas) calculés automatiquement. Un compte peut être
  archivé sans perdre l'argent qu'il contient.
- **Catégories groupées** : organisez vos enveloppes par groupe (ex :
  "Obligations", "Loisirs") pour une vue plus claire, avec un **objectif
  d'épargne** optionnel par catégorie (suivi visuel de la progression dans
  l'onglet Budget).
- **Budget mensuel avec navigation** : parcourez les mois passés et futurs,
  assignez un montant à chaque catégorie, et voyez immédiatement l'activité
  et le disponible (report inclus) pour le mois affiché. Les lignes en
  dépassement sont surlignées en rouge, et une **bannière** signale dès
  l'ouverture de l'application le nombre d'enveloppes en dépassement, sans
  avoir à ouvrir l'onglet Budget.
- **"Reste à assigner"** : indicateur toujours visible de l'argent que vous
  avez mais n'avez pas encore donné a une enveloppe.
- **Déplacer de l'argent entre enveloppes** : rééquilibrez votre budget d'un
  mois sans changer le total assigné ni le reste à assigner.
- **Copier le budget du mois précédent** : reproduisez en un clic les
  montants assignés le mois passé, sans écraser une saisie déjà faite.
- **Transactions** : ajoutez vos dépenses/revenus avec compte, catégorie,
  bénéficiaire, mémo et montant (virgule ou point décimal acceptés) ;
  filtrez par compte, modifiez ou supprimez une transaction existante.
- **Fractionnement d'une transaction** : répartissez un seul achat sur
  plusieurs enveloppes (ex : un plein de courses à la fois "Épicerie" et
  "Entretien maison").
- **Virements entre comptes** : déplacez de l'argent d'un compte à un autre
  sans jamais affecter le budget à enveloppes (un virement entre vos propres
  comptes n'est ni une dépense ni un revenu).
- **Rapprochement bancaire (pointage)** : marquez les transactions comme
  "pointées" au fur et à mesure que vous les retrouvez sur votre relevé
  bancaire, pour vérifier que le solde pointé de l'application correspond à
  la réalité.
- **Transactions récurrentes** : définissez un modèle (loyer, abonnement...)
  avec sa fréquence (hebdomadaire, mensuelle, annuelle) ; les échéances dues
  sont générées automatiquement à l'ouverture de l'application, en
  rattrapant les occurrences manquées si besoin.
- **Import et export CSV des transactions** : exportez votre historique vers
  un tableur, ou importez des transactions depuis un autre outil (les
  doublons exacts sont détectés et ignorés automatiquement).
- **Rapports de dépenses** : tendances de dépenses réelles par catégorie sur
  3, 6 ou 12 derniers mois.
- **Vue annuelle du budget** : le montant assigné à chaque catégorie, mois
  par mois, sur toute une année, pour repérer d'un coup d'œil les mois sans
  aucune assignation.
- **Sauvegarde et restauration** : copie complète de vos données vers
  l'emplacement de votre choix, en un clic, sans fermer l'application (voir
  la section dédiée plus bas).
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
   Loisirs), avec un groupe et un objectif d'épargne optionnels.
3. Onglet **Budget** : assignez un montant à chaque catégorie pour le mois
   affiché (double-clic sur une ligne). Le "Reste à assigner" en haut à
   droite indique combien d'argent n'est pas encore affecté ; une bannière
   rouge signale, dès l'ouverture de l'application, les enveloppes en
   dépassement pour le mois affiché.
4. Onglet **Transactions** : enregistrez vos dépenses et revenus (double-
   clic sur une ligne pour la modifier). Une dépense catégorisée réduit
   automatiquement le disponible de son enveloppe ; un revenu sans catégorie
   augmente le "Reste à assigner". Depuis cet onglet, vous pouvez aussi
   fractionner une transaction sur plusieurs catégories, effectuer un
   virement entre deux comptes, pointer une ou plusieurs transactions
   (rapprochement bancaire), et importer/exporter vos transactions en CSV.
5. Onglet **Récurrentes** : définissez un modèle de transaction répétitive
   (loyer, abonnement...) avec sa fréquence ; les échéances dues sont
   générées automatiquement à chaque ouverture de l'application.
6. Onglets **Rapports** et **Vue annuelle** : consultez vos tendances de
   dépenses par catégorie, et le plan budgétaire de toute une année.
7. Onglet **Paramètres** : sauvegardez vos données en un clic (voir la
   section **Sauvegarde et restauration** ci-dessous).

## Confidentialité

- Aucune donnée ne quitte votre machine : pas de compte, pas de serveur, pas
  de télémétrie, aucune connexion à votre banque.
- Les données sont stockées dans `%APPDATA%\Enveloppe\enveloppe.sqlite`.

## Sauvegarde et restauration

- Onglet **Paramètres > Sauvegarde** : le bouton « Sauvegarder les
  données... » enregistre une copie complète du fichier de données à
  l'emplacement de votre choix, sans fermer l'application ni verrouiller la
  base active.
- Pour restaurer une sauvegarde : fermez Enveloppe, puis remplacez le
  fichier `%APPDATA%\Enveloppe\enveloppe.sqlite` par la copie de sauvegarde
  (le bouton « Ouvrir le dossier de données » y accède directement).

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
sur une vraie base SQLite temporaire, ainsi qu'un test de bout en bout qui
pilote la vraie interface Tkinter (dialogues, boutons, Treeview) plutôt que
d'appeler directement les fonctions internes.

```bash
python -m unittest discover tests -v
```

## Structure du projet

```
db.py                  # couche donnees SQLite : comptes, categories, budget, transactions, sauvegarde
budget.py              # logique pure du budget a enveloppes (report, reste a assigner)
csv_transactions.py    # export/import CSV des transactions
gui.py                 # interface graphique Tkinter
tests/                 # tests automatises (dont un smoke test bout en bout de la GUI)
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
