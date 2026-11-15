# -*- coding: utf-8 -*-
"""Réglages de Génie en herbe (Génie en herbe → Configuration → Réglages).

Tous lus par :func:`gh_param` : une valeur absente ou illisible retombe sur la
valeur par défaut, bornée. Une faute de frappe dans le back-office ne doit
jamais casser un match.
"""
from odoo import api, fields, models

#: clé → (défaut, minimum, maximum)
PARAMS = {
    'max_players': (10, 2, 50),             # joueurs par match libre (individuel)
    'team_max_size': (5, 1, 20),            # joueurs par équipe
    'team_min_size': (1, 1, 20),            # 1 : une équipe peut jouer sur un seul téléphone
    'max_live_matches': (50, 1, 10000),     # matchs libres ouverts ou en cours, en même temps
    'max_live_tournaments': (10, 1, 1000),  # tournois ouverts ou en cours, en même temps
    'tournament_max_entries': (32, 2, 256),
    'question_seconds_default': (30, 5, 300),
    'hand_seconds_default': (5, 2, 60),
    'question_count_default': (10, 1, 100),
    'reveal_seconds': (8, 2, 30),           # affichage de la réponse et de son explication
    'forfeit_minutes_default': (10, 1, 1440),
    'lobby_idle_minutes': (60, 5, 1440),    # salle d'attente abandonnée → annulée
    'running_idle_minutes': (10, 2, 1440),  # match sans aucune activité → clos
    # Invitations et groupes
    'max_invites_per_hour': (60, 1, 10000),  # invitations à un match envoyées par joueur et par heure
    'max_groups': (30, 1, 1000),             # groupes créés par joueur
    'max_group_members': (300, 2, 100000),
    # Documents
    'max_documents': (60, 1, 10000),        # documents par joueur
    'max_pages': (400, 1, 5000),            # pages par document
    'max_page_chars': (12000, 500, 100000),
    'max_document_chars': (1500000, 10000, 20000000),
    # IA
    'ai_daily_quota': (20, 0, 10000),       # lots de pages générés par l'IA, par joueur et par jour (gratuit)
    'ai_chunk_chars': (9000, 1000, 40000),  # taille d'un lot de pages envoyé à l'IA
    'ai_daily_rejected_cap': (5, 1, 1000),  # appels facturés sans résultat, par joueur et par jour
    'ai_jobs_per_run': (6, 1, 200),         # lots traités par passage de la tâche planifiée
}

PREFIX = 'genie_en_herbe.'


def gh_param(env, key):
    default, low, high = PARAMS[key]
    raw = env['ir.config_parameter'].sudo().get_param(PREFIX + key)
    try:
        value = int(raw) if raw not in (None, False, '') else default
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    gh_max_players = fields.Integer(
        "Joueurs par match (au plus)", config_parameter=PREFIX + 'max_players', default=10,
        help="Match individuel : de 2 joueurs à ce nombre.")
    gh_team_max_size = fields.Integer(
        "Joueurs par équipe (au plus)", config_parameter=PREFIX + 'team_max_size', default=5)
    gh_team_min_size = fields.Integer(
        "Joueurs par équipe (au moins)", config_parameter=PREFIX + 'team_min_size', default=1,
        help="1 : une équipe peut jouer sur un seul téléphone (une classe derrière son capitaine).")
    gh_max_live_matches = fields.Integer(
        "Matchs simultanés (au plus)", config_parameter=PREFIX + 'max_live_matches', default=50,
        help="Matchs libres en salle d'attente ou en cours en même temps, sur tout le serveur. "
             "Les duels de tournoi n'y sont pas comptés : ils sont bornés par le nombre de tournois.")
    gh_max_live_tournaments = fields.Integer(
        "Tournois simultanés (au plus)", config_parameter=PREFIX + 'max_live_tournaments', default=10)
    gh_tournament_max_entries = fields.Integer(
        "Participants par tournoi (au plus)", config_parameter=PREFIX + 'tournament_max_entries', default=32)
    gh_question_seconds_default = fields.Integer(
        "Temps par question (s, par défaut)", config_parameter=PREFIX + 'question_seconds_default', default=30)
    gh_hand_seconds_default = fields.Integer(
        "Durée de la main (s, par défaut)", config_parameter=PREFIX + 'hand_seconds_default', default=5)
    gh_question_count_default = fields.Integer(
        "Questions par match (par défaut)", config_parameter=PREFIX + 'question_count_default', default=10)
    gh_reveal_seconds = fields.Integer(
        "Affichage de la réponse (s)", config_parameter=PREFIX + 'reveal_seconds', default=8)
    gh_forfeit_minutes_default = fields.Integer(
        "Forfait d'un duel de tournoi (min, par défaut)", config_parameter=PREFIX + 'forfeit_minutes_default',
        default=10)
    gh_banned_words = fields.Char(
        "Mots interdits dans les pseudos", config_parameter=PREFIX + 'banned_words',
        help="Séparés par des virgules ; s'ajoutent à la liste livrée avec le module.")
    gh_phone_countries = fields.Char(
        "Pays proposés à la connexion", config_parameter=PREFIX + 'phone_countries', default='CI',
        help="Codes ISO séparés par des virgules (ex. CI,SN,BF,FR).")

    gh_max_documents = fields.Integer("Documents par joueur (au plus)", config_parameter=PREFIX + 'max_documents',
                                      default=60)
    gh_max_pages = fields.Integer("Pages par document (au plus)", config_parameter=PREFIX + 'max_pages', default=400)
    gh_ai_enabled = fields.Boolean(
        "Questions rédigées par l'IA", config_parameter=PREFIX + 'ai_enabled', default=True,
        help="Désactivé : seules les questions tirées du texte sans IA sont proposées.")
    gh_ai_daily_quota = fields.Integer(
        "Lots IA gratuits par joueur et par jour", config_parameter=PREFIX + 'ai_daily_quota', default=20,
        help="Un lot = quelques pages (environ 9 000 caractères) envoyées ensemble à l'IA. "
             "Au-delà, les crédits IA du joueur (codes IA) sont utilisés, puis le générateur sans IA.")
    gh_ai_model = fields.Char("Modèle d'IA", config_parameter=PREFIX + 'ai_model', default='claude-opus-5-5')

    @api.model
    def _gh_limits(self):
        return {key: gh_param(self.env, key) for key in PARAMS}
