# Génie en herbe — serveur (module Odoo 19)

Serveur de l'application mobile **Génie en herbe** (dépôt `genie_en_herbe`) :
des concours de questions sur les pages de vos documents.

## Ce que fait le module

* **Documents** (`gh.document`, `gh.document.page`) : le texte est extrait sur
  le téléphone (PDF, Word, PowerPoint, Excel, OpenDocument, texte ; OCR des
  pages scannées) et envoyé page par page, par lots
  (`/document/begin` → `/document/pages` → `/document/finish`). Privés,
  partagés par code (`D` + 7 caractères) ou publics. Un même texte n'est pas
  chargé deux fois par le même joueur.
* **Questions** (`gh.question`) par page, réutilisées d'un match à l'autre :
  * sans IA (`game/offline.py`, Python pur, français et anglais), tout de suite ;
  * par Claude (`gh.ai.request`) : lots de pages (≈ 9 000 caractères) traités
    en arrière-plan par la tâche « Génie en herbe : questions de l'IA » ; la
    citation de chaque question doit figurer mot pour mot dans sa page ;
  * écrites ou corrigées par le propriétaire (relecture : valider, corriger,
    supprimer, régénérer).
* **Coût de l'IA** : quota gratuit de lots par joueur et par jour, puis crédits
  IA apportés par des **codes IA** (`gh.ai.code`, back-office → IA → Créer des
  codes IA). Un lot n'est payé que s'il aboutit.
* **Jeu** (repris de Quiz Biblique) : matchs de 2 à 10 joueurs ou entre deux
  équipes (une équipe peut jouer sur un seul téléphone), tournois à
  élimination directe, main de 5 s (chrono de la question en pause), +10 / −5 /
  0, questions décisives en tournoi. L'organisateur peut **diriger sans jouer**
  (`host_only`). Serveur seul arbitre ; temps réel par le bus Odoo.
* Les joueurs d'un match relisent ses pages (`/document/page` avec `match_id`),
  même si le document est privé.

## Installation

Dépendances : `bus`, `mobile_app_release`, `otp_auth`, `push_notification_hub`
(modules partagés d'innovation), bibliothèque Python `anthropic`.

Clé de l'IA : paramètre système `genie_en_herbe.anthropic_api_key` ; à défaut
`comprendre.anthropic_api_key`, puis la variable d'environnement
`ANTHROPIC_API_KEY`. Réglages : Génie en herbe → Configuration → Réglages.

## Tests

```
python3 -m game.tests_standalone          # générateur sans IA, sans Odoo
../odoo_dev/test.sh genie_en_herbe_backend # tests Odoo (base neuve)
```

Le guide (`reports/user_guide.xml`) est produit depuis
`genie_en_herbe/docs/GUIDE_UTILISATEUR.md` par `docs/md_to_guide_qweb.py`
(dépôt de travail `sunday_jobs`) : ne pas le modifier à la main.
