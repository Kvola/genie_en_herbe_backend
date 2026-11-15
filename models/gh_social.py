# -*- coding: utf-8 -*-
"""Inviter des joueurs : « Mes joueurs », groupes, invitations à un match.

* **Mes joueurs** (``gh.contact``) : se remplit seul à la fin de chaque match
  (tous ceux qui y étaient, organisateur compris) ; on peut marquer des favoris.
* **Groupes** (``gh.group``) : une classe, un club. Le créateur partage le code
  du groupe (``G`` + 7 caractères) ; les membres le saisissent une fois. On
  invite ensuite tout le groupe d'un geste, et un tournoi peut être réservé à
  ses membres.
* **Invitations à un match** (``gh.match.invitation``) : l'invité est prévenu
  (temps réel, et notification push quand elle est configurée) ; il accepte
  (il entre dans la salle d'attente) ou refuse. Une invitation encore en
  attente tombe quand le match commence ou est annulé.

Garde-fous : un nombre d'invitations par heure borné ; chacun choisit de qui
il accepte les invitations (tout le monde, ses joueurs et ses groupes, ou
personne) et peut bloquer un joueur. Le numéro de téléphone n'est jamais
montré, et rien ne lit le répertoire du téléphone.
"""
import random
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .gh_config import gh_param

CODE_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'

#: Motifs de refus d'une invitation (lisibles par l'application).
SKIP_SELF = 'self'
SKIP_BLOCKED = 'blocked'
SKIP_POLICY = 'policy'
SKIP_BUSY = 'busy'
SKIP_ALREADY = 'already'
SKIP_LIMIT = 'limit'


class GhPlayerSocial(models.Model):
    _inherit = 'gh.player'

    invite_policy = fields.Selection([
        ('all', "De tout le monde"),
        ('known', "De mes joueurs et de mes groupes"),
        ('none', "De personne"),
    ], "Recevoir les invitations", default='all', required=True)
    blocked_ids = fields.Many2many('gh.player', 'gh_player_block_rel', 'player_id', 'blocked_id',
                                   "Joueurs bloqués")

    def _knows(self, other):
        """``other`` est-il dans mes joueurs, ou dans un de mes groupes ?"""
        self.ensure_one()
        if self.env['gh.contact'].sudo().search_count([('owner_id', '=', self.id), ('player_id', '=', other.id)]):
            return True
        mine = self.env['gh.group.member'].sudo().search([('player_id', '=', self.id)]).group_id
        return bool(mine) and bool(self.env['gh.group.member'].sudo().search_count(
            [('group_id', 'in', mine.ids), ('player_id', '=', other.id)]))

    def _invitation_refusal(self, inviter):
        """Pourquoi ``self`` ne peut pas recevoir d'invitation de ``inviter`` ('' si possible)."""
        self.ensure_one()
        if self == inviter:
            return SKIP_SELF
        if not self.active or self.banned or inviter in self.blocked_ids:
            return SKIP_BLOCKED
        if self.invite_policy == 'none' or (self.invite_policy == 'known' and not self._knows(inviter)):
            return SKIP_POLICY
        return ''


class GhContact(models.Model):
    _name = 'gh.contact'
    _description = "Joueur déjà rencontré (Mes joueurs)"
    _order = 'favorite desc, last_played desc'

    owner_id = fields.Many2one('gh.player', required=True, ondelete='cascade', index=True)
    player_id = fields.Many2one('gh.player', "Joueur", required=True, ondelete='cascade', index=True)
    favorite = fields.Boolean("Favori")
    matches_together = fields.Integer("Matchs ensemble")
    last_played = fields.Datetime("Dernier match ensemble")

    _uniq = models.Constraint('UNIQUE (owner_id, player_id)', "Joueur déjà dans la liste.")

    @api.model
    def _met(self, players):
        """Fin d'un match : chacun ajoute les autres à « Mes joueurs » (une requête, sans doublon)."""
        ids = sorted(set(players.ids))
        pairs = [(a, b) for a in ids for b in ids if a != b]
        if not pairs:
            return
        self.env.flush_all()
        self.env.cr.execute("""
            INSERT INTO gh_contact (owner_id, player_id, matches_together, last_played, favorite,
                                    create_uid, write_uid, create_date, write_date)
            SELECT a, b, 1, now() at time zone 'utc', false, %s, %s, now() at time zone 'utc', now() at time zone 'utc'
              FROM unnest(%s::int[], %s::int[]) AS t(a, b)
            ON CONFLICT (owner_id, player_id) DO UPDATE
               SET matches_together = gh_contact.matches_together + 1,
                   last_played = EXCLUDED.last_played, write_date = EXCLUDED.write_date
        """, [self.env.uid, self.env.uid, [a for a, _b in pairs], [b for _a, b in pairs]])
        self.invalidate_model()

    def _public(self):
        self.ensure_one()
        p = self.player_id
        return {'player_id': p.id, 'pseudo': p.pseudo, 'favorite': self.favorite,
                'matches_together': self.matches_together,
                'last_played': fields.Datetime.to_string(self.last_played) if self.last_played else None,
                'busy': bool(p.match_id or p.tournament_id)}


class GhGroup(models.Model):
    _name = 'gh.group'
    _description = "Groupe de joueurs (classe, club)"
    _order = 'name'

    name = fields.Char("Nom", required=True)
    code = fields.Char("Code", required=True, readonly=True, copy=False, index=True)
    owner_id = fields.Many2one('gh.player', "Créé par", required=True, ondelete='cascade', index=True)
    member_ids = fields.One2many('gh.group.member', 'group_id', "Membres")
    member_count = fields.Integer("Membres", compute='_compute_member_count')
    active = fields.Boolean(default=True)

    _code_uniq = models.Constraint('UNIQUE (code)', "Ce code existe déjà.")

    def _compute_member_count(self):
        for g in self:
            g.member_count = len(g.member_ids)

    @api.model
    def _new_code(self):
        rnd = random.SystemRandom()
        for _attempt in range(50):
            code = 'G' + ''.join(rnd.choice(CODE_ALPHABET) for _i in range(7))
            if not self.sudo().with_context(active_test=False).search_count([('code', '=', code)]):
                return code
        raise UserError(_("Impossible de créer un code. Réessayez."))

    @api.model
    def create_group(self, player, name):
        player._ensure_can_play()
        name = (name or '').strip()[:60]
        if len(name) < 2:
            raise UserError(_("Donnez un nom au groupe."))
        if self.sudo().search_count([('owner_id', '=', player.id)]) >= gh_param(self.env, 'max_groups'):
            raise UserError(_("Vous avez déjà %s groupes.", gh_param(self.env, 'max_groups')))
        group = self.sudo().create({'name': name, 'owner_id': player.id, 'code': self._new_code()})
        self.env['gh.group.member'].sudo().create({'group_id': group.id, 'player_id': player.id})
        return group

    @api.model
    def _for_member(self, player, group_id, owner=False):
        group = self.sudo().browse(int(group_id or 0)).exists()
        if not group or not group.active or player not in group.member_ids.player_id:
            raise AccessError(_("Groupe introuvable."))
        if owner and group.owner_id != player:
            raise AccessError(_("Seul le créateur du groupe peut faire cela."))
        return group

    @api.model
    def join_by_code(self, player, code):
        player._ensure_can_play()
        code = ''.join(ch for ch in (code or '').upper() if ch.isalnum())
        group = self.sudo().search([('code', '=', code)], limit=1)
        if not group:
            raise UserError(_("Aucun groupe avec ce code."))
        if player not in group.member_ids.player_id:
            if len(group.member_ids) >= gh_param(self.env, 'max_group_members'):
                raise UserError(_("Ce groupe est complet."))
            self.env['gh.group.member'].sudo().create({'group_id': group.id, 'player_id': player.id})
            self.env['gh.notify']._players_event(group.owner_id, 'group_joined', {
                'group_id': group.id, 'group_name': group.name, 'pseudo': player.pseudo})
        return group

    def leave(self, player):
        self.ensure_one()
        if self.owner_id == player:
            raise UserError(_("Vous avez créé ce groupe : supprimez-le, ou confiez-le à un autre membre."))
        self.member_ids.filtered(lambda m: m.player_id == player).sudo().unlink()

    def remove_member(self, owner, player_id):
        self.ensure_one()
        if self.owner_id != owner:
            raise AccessError(_("Seul le créateur du groupe peut faire cela."))
        if int(player_id) == owner.id:
            raise UserError(_("Vous ne pouvez pas vous retirer de votre propre groupe."))
        self.member_ids.filtered(lambda m: m.player_id.id == int(player_id)).sudo().unlink()

    def transfer(self, owner, player_id):
        self.ensure_one()
        if self.owner_id != owner:
            raise AccessError(_("Seul le créateur du groupe peut faire cela."))
        new = self.member_ids.filtered(lambda m: m.player_id.id == int(player_id)).player_id
        if not new:
            raise UserError(_("Ce joueur n'est pas membre du groupe."))
        self.sudo().owner_id = new

    def _public(self, player, members=False):
        self.ensure_one()
        data = {'id': self.id, 'name': self.name, 'member_count': self.member_count,
                'owner': self.owner_id.pseudo, 'mine': self.owner_id == player,
                'code': self.code}
        if members:
            data['members'] = [{'player_id': m.player_id.id, 'pseudo': m.player_id.pseudo,
                                'owner': m.player_id == self.owner_id,
                                'busy': bool(m.player_id.match_id or m.player_id.tournament_id)}
                               for m in self.member_ids.sorted(lambda m: m.player_id.pseudo.lower())]
        return data


class GhGroupMember(models.Model):
    _name = 'gh.group.member'
    _description = "Membre d'un groupe"
    _order = 'id'

    group_id = fields.Many2one('gh.group', required=True, ondelete='cascade', index=True)
    player_id = fields.Many2one('gh.player', required=True, ondelete='cascade', index=True)

    _uniq = models.Constraint('UNIQUE (group_id, player_id)', "Déjà membre.")


class GhMatchInvitation(models.Model):
    _name = 'gh.match.invitation'
    _description = "Invitation à un match"
    _order = 'id desc'

    match_id = fields.Many2one('gh.match', required=True, ondelete='cascade', index=True)
    player_id = fields.Many2one('gh.player', "Invité", required=True, ondelete='cascade', index=True)
    inviter_id = fields.Many2one('gh.player', "Invité par", required=True, ondelete='cascade', index=True)
    state = fields.Selection([('pending', "En attente"), ('accepted', "Acceptée"), ('declined', "Refusée"),
                              ('expired', "Expirée")], default='pending', required=True, index=True)

    _uniq = models.Constraint('UNIQUE (match_id, player_id)', "Joueur déjà invité à ce match.")

    @api.model
    def _sent_last_hour(self, inviter):
        since = fields.Datetime.now() - timedelta(hours=1)
        return self.sudo().search_count([('inviter_id', '=', inviter.id), ('create_date', '>=', since)])

    @api.model
    def invite(self, match, inviter, players):
        """Invite ``players`` ; rend (invités, [(joueur, motif)] écartés, invités déjà pris par une autre partie)."""
        if match.kind != 'free' or match.state != 'lobby':
            raise UserError(_("On n'invite que dans la salle d'attente d'un match."))
        is_member = inviter == match.creator_id or match.participant_ids.filtered(
            lambda p: p.player_id == inviter and not p.left)
        if not is_member:
            raise AccessError(_("Vous ne participez pas à ce match."))
        limit = gh_param(self.env, 'max_invites_per_hour')
        budget = limit - self._sent_last_hour(inviter)
        invited, skipped, busy = self.env['gh.player'], [], self.env['gh.player']
        present = match.participant_ids.filtered(lambda p: not p.left).player_id
        for player in players:
            if player in present:
                skipped.append((player, SKIP_ALREADY))
                continue
            reason = player._invitation_refusal(inviter)
            if reason:
                skipped.append((player, reason))
                continue
            if budget <= 0:
                skipped.append((player, SKIP_LIMIT))
                continue
            line = self.sudo().search([('match_id', '=', match.id), ('player_id', '=', player.id)], limit=1)
            if line and line.state == 'pending':
                skipped.append((player, SKIP_ALREADY))
                continue
            if line:
                line.write({'state': 'pending', 'inviter_id': inviter.id})
            else:
                self.sudo().create({'match_id': match.id, 'player_id': player.id, 'inviter_id': inviter.id})
            budget -= 1
            invited |= player
            if player.match_id or player.tournament_id:
                busy |= player  # invité quand même (il peut finir sa partie) ; l'inviteur le sait
        if invited:
            title = _("Invitation à un match")
            body = _("%(who)s vous invite à un match sur « %(pages)s ». Code : %(code)s",
                     who=inviter.pseudo, pages=match.pages_label or '', code=match.code)
            self.env['gh.notify']._players_event(invited, 'match_invitation', {
                'match_id': match.id, 'code': match.code, 'inviter': inviter.pseudo,
                'pages_label': match.pages_label or ''}, title, body)
            match._bump()
        return invited, skipped, busy

    def answer(self, player, accept):
        """L'invité accepte (il entre dans le match) ou refuse."""
        self.ensure_one()
        if self.player_id != player:
            raise AccessError(_("Cette invitation ne vous est pas adressée."))
        match = self.match_id
        if self.state != 'pending' or match.state != 'lobby':
            self.sudo().state = 'expired' if self.state == 'pending' else self.state
            raise UserError(_("Ce match a déjà commencé ou est terminé."))
        if accept:
            match.join(player)
            self.sudo().state = 'accepted'
        else:
            self.sudo().state = 'declined'
            match._bump()
        return match

    @api.model
    def _expire(self, matches):
        self.sudo().search([('match_id', 'in', matches.ids), ('state', '=', 'pending')]).write({'state': 'expired'})


def player_refs(env, ids):
    """Joueurs (actifs) d'après une liste d'identifiants envoyée par l'application."""
    if not isinstance(ids, list):
        raise UserError(_("Liste de joueurs illisible."))
    try:
        clean = list({int(i) for i in ids})[:200]
    except (TypeError, ValueError) as e:
        raise UserError(_("Liste de joueurs illisible.")) from e
    return env['gh.player'].sudo().browse(clean).exists().filtered('active')
