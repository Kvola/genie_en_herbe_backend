# -*- coding: utf-8 -*-
{
    'name': "Génie en herbe — quiz en ligne sur vos documents",
    'version': '19.0.1.3.0',
    'category': 'Productivity',
    'summary': "Serveur de l'application Génie en herbe : documents découpés en pages, questions "
               "(IA ou sans IA), matchs à la main, équipes et tournois",
    'description': """
Génie en herbe — serveur
========================

Un organisateur charge un document (PDF, Word, PowerPoint, Excel…) depuis
l'application, choisit des pages, et lance un match ou un tournoi dont les
questions portent sur ces pages : le premier qui prend la main répond.

* **Documents** : le texte est extrait sur le téléphone et envoyé page par
  page (le fichier ne quitte pas le téléphone). Privés, partagés par code ou
  publics.
* **Questions** par page, réutilisées d'un match à l'autre : tirées du texte
  sans IA (tout de suite, français et anglais), ou rédigées par Claude en
  arrière-plan (citation vérifiée mot pour mot dans la page). Quota gratuit
  par joueur et par jour, puis crédits IA (codes IA). Le propriétaire relit,
  corrige, valide, supprime, régénère.
* **Matchs** de 2 à 10 joueurs ou entre deux équipes (une équipe peut jouer
  sur un seul téléphone), **tournois** à élimination directe ; l'organisateur
  peut diriger sans jouer. Main de 5 s (chrono de la question en pause), +10,
  −5, 0. Serveur seul arbitre, temps réel par le bus Odoo (WebSocket).
* Les joueurs relisent les pages du match avant (révision) et après
  (correction, avec le passage qui justifie chaque réponse).

Réglages : Génie en herbe → Configuration → Réglages. Clé de l'IA : paramètre
système ``genie_en_herbe.anthropic_api_key`` (à défaut celle de Comprendre,
``comprendre.anthropic_api_key``, ou la variable ``ANTHROPIC_API_KEY``).

Le guide (``/apps/genie-en-herbe/guide``) est produit depuis
``genie_en_herbe/docs/GUIDE_UTILISATEUR.md`` par ``docs/md_to_guide_qweb.py`` :
ne pas modifier ``reports/user_guide.xml`` à la main.
    """,
    'author': 'ICP',
    'website': 'https://ivorycocoa.ci',
    'depends': ['bus', 'mobile_app_release', 'otp_auth', 'push_notification_hub'],
    'external_dependencies': {'python': ['anthropic']},
    'data': [
        'security/gh_security.xml',
        'security/ir.model.access.csv',
        'reports/user_guide.xml',
        'data/mobile_app.xml',
        'data/gh_data.xml',
        'views/gh_views.xml',
    ],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
