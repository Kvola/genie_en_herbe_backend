# -*- coding: utf-8 -*-
"""API pour inviter des joueurs : invitations à un match, « Mes joueurs », groupes, réglages."""
from odoo import _
from odoo.exceptions import AccessError, UserError
from odoo.http import Controller, request, route

from ..models.gh_social import player_refs
from .api import _match_payload, _match_for, _tournament_for, _tournament_payload
from .common import API, arg_int, ok, player_route, rate_limited

#: Invitations à un tournoi (elles ne laissent pas de trace en base) : par session et par heure.
RL_TOURNAMENT_INVITE = (20, 3600, 3600)


def _skipped(items):
    return [{'player_id': p.id, 'pseudo': p.pseudo, 'reason': reason} for p, reason in items]


def _targets(player, kw):
    """Les joueurs visés : ``player_ids`` et/ou tous les membres du groupe ``group_id``."""
    players = player_refs(request.env, kw.get('player_ids') or [])
    if kw.get('group_id'):
        group = request.env['gh.group']._for_member(player, kw['group_id'])
        players |= group.member_ids.player_id.filtered('active')
    if not players:
        raise UserError(_("Choisissez au moins un joueur."))
    return players - player


class GenieSocialApi(Controller):

    # ================================================================== invitations
    @route(API + '/match/invite', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    @player_route
    def match_invite(self, player, **kw):
        match = _match_for(player, kw.get('match_id'))
        match._lock()
        invited, skipped, busy = request.env['gh.match.invitation'].sudo().invite(match, player, _targets(player, kw))
        return ok(invited=[p.pseudo for p in invited], busy=[p.pseudo for p in busy], skipped=_skipped(skipped),
                  **_match_payload(match, player))

    @route(API + '/match/invitation/answer', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    @player_route
    def invitation_answer(self, player, **kw):
        invitation = request.env['gh.match.invitation'].sudo().browse(arg_int(kw, 'invitation_id')).exists()
        if not invitation:
            raise AccessError(_("Invitation introuvable."))
        match = invitation.match_id
        match._lock()
        accept = kw.get('accept') is not False
        invitation.answer(player, accept)
        return ok(**_match_payload(match, player)) if accept else ok()

    @route(API + '/tournament/invite', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    @player_route
    def tournament_invite(self, player, **kw):
        refusal = rate_limited('tournament_invite', RL_TOURNAMENT_INVITE, subject='p%s' % player.id)
        if refusal:
            return refusal
        t = _tournament_for(kw.get('tournament_id'))
        sent, skipped = t.invite(player, _targets(player, kw))
        return ok(invited=[p.pseudo for p in sent], skipped=_skipped(skipped), **_tournament_payload(t, player))

    # ================================================================== mes joueurs
    @route(API + '/contacts', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    @player_route
    def contacts(self, player, **kw):
        lines = request.env['gh.contact'].sudo().search([('owner_id', '=', player.id), ('player_id.active', '=', True)],
                                                        limit=300)
        return ok(contacts=[c._public() for c in lines],
                  blocked=[p._public() for p in player.blocked_ids],
                  invite_policy=player.invite_policy)

    @route(API + '/contact/favorite', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    @player_route
    def contact_favorite(self, player, **kw):
        other = player_refs(request.env, [kw.get('player_id')])
        if not other or other == player:
            raise UserError(_("Joueur introuvable."))
        Contact = request.env['gh.contact'].sudo()
        line = Contact.search([('owner_id', '=', player.id), ('player_id', '=', other.id)], limit=1)
        if not line:
            line = Contact.create({'owner_id': player.id, 'player_id': other.id})
        line.favorite = kw.get('favorite') is not False
        return ok(contact=line._public())

    @route(API + '/player/block', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    @player_route
    def player_block(self, player, **kw):
        other = player_refs(request.env, [kw.get('player_id')])
        if not other or other == player:
            raise UserError(_("Joueur introuvable."))
        if kw.get('blocked') is False:
            player.sudo().blocked_ids = [(3, other.id)]
        else:
            player.sudo().blocked_ids = [(4, other.id)]
            # Ses invitations en attente disparaissent.
            request.env['gh.match.invitation'].sudo().search([
                ('player_id', '=', player.id), ('inviter_id', '=', other.id), ('state', '=', 'pending')]).write(
                {'state': 'declined'})
        return ok(blocked=[p._public() for p in player.blocked_ids])

    @route(API + '/player/settings', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    @player_route
    def player_settings(self, player, **kw):
        if kw.get('invite_policy') in ('all', 'known', 'none'):
            player.sudo().invite_policy = kw['invite_policy']
        return ok(invite_policy=player.invite_policy)

    # ================================================================== groupes
    @route(API + '/groups', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    @player_route
    def groups(self, player, **kw):
        groups = request.env['gh.group.member'].sudo().search([('player_id', '=', player.id)]).group_id.filtered('active')
        return ok(groups=[g._public(player) for g in groups.sorted(lambda g: g.name.lower())])

    @route(API + '/group/create', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    @player_route
    def group_create(self, player, **kw):
        group = request.env['gh.group'].sudo().create_group(player, kw.get('name'))
        return ok(group=group._public(player, members=True), link=self._link(group))

    @route(API + '/group/join', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    @player_route
    def group_join(self, player, **kw):
        refusal = rate_limited('group_join', (20, 600, 600), subject='p%s' % player.id)
        if refusal:
            return refusal
        group = request.env['gh.group'].sudo().join_by_code(player, kw.get('code'))
        return ok(group=group._public(player, members=True))

    @route(API + '/group/get', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    @player_route
    def group_get(self, player, **kw):
        group = request.env['gh.group']._for_member(player, kw.get('group_id'))
        return ok(group=group._public(player, members=True), link=self._link(group))

    @route(API + '/group/update', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    @player_route
    def group_update(self, player, **kw):
        group = request.env['gh.group']._for_member(player, kw.get('group_id'), owner=True)
        if kw.get('name'):
            name = str(kw['name']).strip()[:60]
            if len(name) < 2:
                raise UserError(_("Donnez un nom au groupe."))
            group.name = name
        if kw.get('renew_code'):
            group.code = group._new_code()
        if kw.get('remove_player_id'):
            group.remove_member(player, kw['remove_player_id'])
        if kw.get('transfer_to'):
            group.transfer(player, kw['transfer_to'])
        return ok(group=group._public(player, members=True), link=self._link(group))

    @route(API + '/group/leave', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    @player_route
    def group_leave(self, player, **kw):
        group = request.env['gh.group']._for_member(player, kw.get('group_id'))
        group.leave(player)
        return ok()

    @route(API + '/group/delete', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    @player_route
    def group_delete(self, player, **kw):
        group = request.env['gh.group']._for_member(player, kw.get('group_id'), owner=True)
        group.active = False
        return ok()

    @staticmethod
    def _link(group):
        base = request.httprequest.host_url.rstrip('/')
        return '%s/genie-en-herbe/rejoindre/%s' % (base, group.code)
