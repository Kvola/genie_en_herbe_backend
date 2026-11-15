# -*- coding: utf-8 -*-
"""Inviter des joueurs : invitations à un match, Mes joueurs, groupes, garde-fous."""
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from ..game.tests_standalone import FR_PAGES
from ..models.gh_match import COUNTDOWN_SECONDS, GameRefusal
from .test_game import T0, make_document


@tagged('post_install', '-at_install')
class TestSocial(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Player = cls.env['gh.player'].sudo()
        cls.Match = cls.env['gh.match'].sudo()
        cls.Inv = cls.env['gh.match.invitation'].sudo()
        cls.Group = cls.env['gh.group'].sudo()
        cls.prof = cls.player('Professeur')
        cls.doc = make_document(cls.env, cls.prof, FR_PAGES, visibility='public')
        cls.env['gh.question'].sudo()._offline_for_pages(cls.doc.page_ids)

    @classmethod
    def player(cls, pseudo):
        partner = cls.env['res.partner'].create({'name': pseudo})
        return cls.env['gh.player'].sudo().create({'partner_id': partner.id, 'pseudo': pseudo})

    def match(self, creator, **kw):
        return self.Match.create_free(creator, dict({'pages': self.doc.page_ids.ids, 'question_count': 5}, **kw))

    def test_invitation_acceptee_puis_expiree(self):
        awa, ben, cy = self.player('Awa'), self.player('Ben'), self.player('Cyril')
        m = self.match(self.prof)
        invited, skipped, busy = self.Inv.invite(m, self.prof, awa | ben | cy | self.prof)
        self.assertEqual(invited, awa | ben | cy)
        self.assertEqual(skipped, [(self.prof, 'already')])
        self.assertFalse(busy)
        state = m._state()
        self.assertEqual({i['pseudo']: i['state'] for i in state['invitations']},
                         {'Awa': 'pending', 'Ben': 'pending', 'Cyril': 'pending'})
        inv_awa = self.Inv.search([('player_id', '=', awa.id)])
        with self.assertRaises(AccessError):
            inv_awa.answer(ben, True)
        inv_awa.answer(awa, True)
        self.assertEqual(awa.match_id, m)
        self.Inv.search([('player_id', '=', ben.id)]).answer(ben, False)
        # Cyril rejoint par le code : son invitation est honorée.
        m.join(cy)
        self.assertEqual(self.Inv.search([('player_id', '=', cy.id)]).state, 'accepted')
        # Réinviter Ben après son refus : de nouveau en attente ; elle tombe au lancement.
        self.Inv.invite(m, awa, ben)
        m.start(self.prof, now=T0)
        self.assertEqual(self.Inv.search([('player_id', '=', ben.id)]).state, 'expired')
        with self.assertRaises(UserError):
            self.Inv.search([('player_id', '=', ben.id)]).answer(ben, True)

    def test_seuls_ceux_du_match_invitent_et_seulement_en_salle_d_attente(self):
        awa, intrus = self.player('Awa'), self.player('Intrus')
        m = self.match(self.prof)
        with self.assertRaises(AccessError):
            self.Inv.invite(m, intrus, awa)
        m.join(awa)
        m.start(self.prof, now=T0)
        with self.assertRaises(UserError):
            self.Inv.invite(m, self.prof, intrus)

    def test_organisateur_sans_jouer_invite(self):
        awa = self.player('Awa')
        m = self.match(self.prof, host_only=True)
        invited, _s, _b = self.Inv.invite(m, self.prof, awa)
        self.assertEqual(invited, awa)

    def test_garde_fous(self):
        awa, ben, cy, dan = (self.player(n) for n in ('Awa', 'Ben', 'Cyril', 'Daniel'))
        m = self.match(self.prof)
        awa.invite_policy = 'none'
        ben.invite_policy = 'known'
        cy.blocked_ids = [(4, self.prof.id)]
        invited, skipped, _busy = self.Inv.invite(m, self.prof, awa | ben | cy | dan)
        self.assertEqual(invited, dan)
        self.assertEqual(dict((p.pseudo, r) for p, r in skipped), {'Awa': 'policy', 'Ben': 'policy', 'Cyril': 'blocked'})
        # Ben accepte les invitations de « ses joueurs » : après un match ensemble, c'est bon.
        self.env['gh.contact']._met(self.prof | ben)
        invited, _s, _b = self.Inv.invite(m, self.prof, ben)
        self.assertEqual(invited, ben)
        # Limite horaire.
        self.env['ir.config_parameter'].sudo().set_param('genie_en_herbe.max_invites_per_hour', '2')
        invited, skipped, _b = self.Inv.invite(m, self.prof, self.player('Emma'))
        self.assertFalse(invited)
        self.assertEqual(skipped[0][1], 'limit')

    def test_invite_un_joueur_deja_en_partie(self):
        awa, ben = self.player('Awa'), self.player('Ben')
        other = self.match(ben)
        other.join(awa)
        m = self.match(self.prof)
        invited, _s, busy = self.Inv.invite(m, self.prof, awa)
        self.assertEqual((invited, busy), (awa, awa))

    def test_mes_joueurs_apres_un_match(self):
        awa, ben = self.player('Awa'), self.player('Ben')
        m = self.match(self.prof, host_only=True)
        m.join(awa)
        m.join(ben)
        m.start(self.prof, now=T0)
        m._lock()
        m._advance(T0 + COUNTDOWN_SECONDS)
        m.leave(ben, now=T0 + 5)  # abandon : le match se termine
        self.assertEqual(m.state, 'finished')
        Contact = self.env['gh.contact'].sudo()
        self.assertEqual(set(Contact.search([('owner_id', '=', self.prof.id)]).player_id.mapped('pseudo')), {'Awa', 'Ben'})
        line = Contact.search([('owner_id', '=', awa.id), ('player_id', '=', ben.id)])
        self.assertEqual(line.matches_together, 1)
        self.env['gh.contact']._met(awa | ben)
        line.invalidate_recordset()
        self.assertEqual(line.matches_together, 2)

    def test_groupes(self):
        awa, ben, intrus = self.player('Awa'), self.player('Ben'), self.player('Intrus')
        g = self.Group.create_group(self.prof, '3e B')
        self.assertTrue(g.code.startswith('G') and len(g.code) == 8)
        self.Group.join_by_code(awa, g.code.lower())
        self.Group.join_by_code(ben, g.code)
        self.assertEqual(len(g.member_ids), 3)
        with self.assertRaises(UserError):
            self.Group.join_by_code(intrus, 'GXXXXXXX')
        with self.assertRaises(AccessError):
            self.Group._for_member(intrus, g.id)
        with self.assertRaises(UserError):
            g.leave(self.prof)  # le créateur supprime ou transmet
        g.transfer(self.prof, awa.id)
        self.assertEqual(g.owner_id, awa)
        g.leave(self.prof)
        # « known » : un membre du même groupe peut inviter.
        ben.invite_policy = 'known'
        self.assertEqual(ben._invitation_refusal(awa), '')
        self.assertEqual(ben._invitation_refusal(intrus), 'policy')

    def test_tournoi_reserve_au_groupe(self):
        awa, intrus = self.player('Awa'), self.player('Intrus')
        g = self.Group.create_group(self.prof, 'Club')
        self.Group.join_by_code(awa, g.code)
        t = self.env['gh.tournament'].sudo().create_tournament(self.prof, {
            'name': 'Coupe du club', 'pages': self.doc.page_ids.ids, 'question_count': 5, 'group_id': g.id})
        t.register(awa)
        with self.assertRaises(GameRefusal) as cm:
            t.register(intrus)
        self.assertEqual(cm.exception.code, 'GROUP_ONLY')
        sent, skipped = t.invite(self.prof, awa | intrus | self.player('Zoé'))
        self.assertFalse(sent)  # Awa déjà inscrite, les autres hors du groupe
        self.assertEqual(sorted(r for _p, r in skipped), ['already', 'group', 'group'])
        with self.assertRaises(AccessError):
            self.env['gh.tournament'].sudo().create_tournament(intrus, {
                'name': 'Pirate', 'pages': self.doc.page_ids.ids, 'question_count': 5, 'group_id': g.id})
