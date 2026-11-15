# -*- coding: utf-8 -*-
"""Matchs programmés : inscription sans être retenu, engagement au lancement, rappels, annulation."""
import time
from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged

from ..game.tests_standalone import FR_PAGES
from ..models.gh_match import GameRefusal, ms_dt
from .test_game import T0, make_document


def in_ms(delta):
    """Instant (ms depuis l'époque) dans ``delta``."""
    return int((time.time() + delta.total_seconds()) * 1000)


@tagged('post_install', '-at_install')
class TestSchedule(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Match = cls.env['gh.match'].sudo()
        cls.prof = cls.player('Professeur')
        cls.doc = make_document(cls.env, cls.prof, FR_PAGES, visibility='public')
        cls.env['gh.question'].sudo()._offline_for_pages(cls.doc.page_ids)

    @classmethod
    def player(cls, pseudo):
        partner = cls.env['res.partner'].create({'name': pseudo})
        return cls.env['gh.player'].sudo().create({'partner_id': partner.id, 'pseudo': pseudo})

    def scheduled(self, creator, delta=timedelta(hours=2), **kw):
        return self.Match.create_free(creator, dict(
            {'pages': self.doc.page_ids.ids, 'question_count': 5, 'scheduled_at': in_ms(delta)}, **kw))

    def setUp(self):
        super().setUp()
        # Les messages temps réel ne s'écrivent qu'à la validation (jamais en test) : on les capte à la source.
        self.sent = []
        notify = type(self.env['gh.notify'])
        original = notify._players_event

        def record(model, players, event, payload, title=None, body=None):
            self.sent.extend({'event': event, 'payload': payload, 'player': p.pseudo} for p in players)
            return original(model, players, event, payload, title, body)
        self.patch(notify, '_players_event', record)

    def events(self, kind='match_reminder'):
        return [e for e in self.sent if e['event'] == kind]

    def test_s_inscrire_sans_etre_retenu_puis_engage_au_lancement(self):
        awa, ben, cyril = self.player('Awa'), self.player('Ben'), self.player('Cyril')
        m = self.scheduled(self.prof)
        self.assertTrue(m.scheduled_at)
        self.assertFalse(self.prof.match_id, "programmé : l'organisateur reste libre")
        m.join(awa)
        m.join(ben)
        m.join(cyril)
        self.assertFalse(awa.match_id)
        # En attendant, Cyril joue un autre match.
        other = self.Match.create_free(cyril, {'pages': self.doc.page_ids.ids, 'question_count': 5})
        other.join(self.player('Dora'))
        self.assertEqual(cyril.match_id, other)
        # Au lancement : chacun est retenu ; Cyril, pris ailleurs, est écarté et prévenu.
        m.start(self.prof, now=T0)
        self.assertEqual(m.state, 'running')
        self.assertEqual((self.prof.match_id, awa.match_id, ben.match_id, cyril.match_id), (m, m, m, other))
        self.assertNotIn(cyril, m._active().player_id)
        self.assertTrue(self.events('match_dropped'))

    def test_heure_hors_limites(self):
        with self.assertRaises(GameRefusal) as cm:
            self.scheduled(self.prof, timedelta(minutes=1))
        self.assertEqual(cm.exception.code, 'SCHEDULE')
        with self.assertRaises(GameRefusal):
            self.scheduled(self.prof, timedelta(days=31))

    def test_plafond_par_organisateur_et_matchs_simultanes(self):
        self.env['ir.config_parameter'].sudo().set_param('genie_en_herbe.max_scheduled_per_player', '2')
        self.scheduled(self.prof)
        self.scheduled(self.prof, timedelta(days=2))
        with self.assertRaises(GameRefusal):
            self.scheduled(self.prof, timedelta(days=3))
        # Un match programmé dans plus d'une heure n'occupe pas le serveur.
        self.assertEqual(self.Match._live_free_count(), 0)

    def test_rappels(self):
        awa, invitee = self.player('Awa'), self.player('Invitée')
        m = self.scheduled(self.prof, timedelta(days=2))
        m.join(awa)
        self.env['gh.match.invitation'].sudo().invite(m, self.prof, invitee)
        # La veille.
        m.scheduled_at = fields.Datetime.now() + timedelta(hours=21)
        self.Match._cron_schedule_reminders()
        self.assertTrue(m.reminded_day)
        self.assertEqual(len(self.events()), 3, "inscrite, organisateur, invitée")
        # 10 minutes avant, puis à l'heure (l'organisateur : « lancez »).
        m.scheduled_at = fields.Datetime.now() + timedelta(minutes=9)
        self.Match._cron_schedule_reminders()
        self.assertTrue(m.reminded_soon)
        m.scheduled_at = fields.Datetime.now() - timedelta(minutes=1)
        self.Match._cron_schedule_reminders()
        self.assertTrue(m.reminded_start)
        start = [e for e in self.events() if e['payload'].get('kind') == 'start']
        self.assertEqual(sum(1 for e in start if e['payload'].get('creator')), 1)
        # Plus rien ensuite.
        before = len(self.events())
        self.Match._cron_schedule_reminders()
        self.assertEqual(len(self.events()), before)

    def test_pas_de_rappel_de_la_veille_pour_un_match_programme_le_jour_meme(self):
        m = self.scheduled(self.prof, timedelta(hours=5))
        self.Match._cron_schedule_reminders()
        self.assertFalse(m.reminded_day)

    def test_deplacer_l_heure(self):
        awa = self.player('Awa')
        m = self.scheduled(self.prof)
        m.join(awa)
        m.reminded_day = True
        new = in_ms(timedelta(days=1))
        m.update_settings(self.prof, {'scheduled_at': new})
        self.assertEqual(ms_dt(m.scheduled_at) // 1000, new // 1000)
        self.assertFalse(m.reminded_day)
        self.assertTrue([e for e in self.events() if e['payload'].get('kind') == 'moved'])

    def test_annulation_si_personne_ne_lance(self):
        m = self.scheduled(self.prof)
        old = fields.Datetime.now() - timedelta(hours=3)
        self.env.cr.execute("UPDATE gh_match SET last_activity = %s WHERE id = %s", [old, m.id])
        m.invalidate_recordset()
        self.Match._cron_advance()
        self.assertEqual(m.state, 'lobby', "son heure n'est pas venue")
        self.env.cr.execute("UPDATE gh_match SET scheduled_at = %s WHERE id = %s", [old, m.id])
        m.invalidate_recordset()
        self.Match._cron_advance()
        self.assertEqual(m.state, 'cancelled')
