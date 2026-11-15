# -*- coding: utf-8 -*-
"""Documents, pages, questions (sans IA, IA simulée), relecture, codes IA, organisateur sans jouer."""
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from ..game.tests_standalone import EN_PAGES, FR_PAGES
from ..game.text_tools import split_sentences
from ..models.gh_document import DuplicateDocument
from ..models.gh_match import COUNTDOWN_SECONDS, GameRefusal
from ..models.gh_question import AiRefusal
from .test_game import T0, make_document


def fake_claude(pages, lang):
    """Réponse simulée de l'IA : une bonne question par phrase longue de chaque page, plus une citation
    inventée (à écarter) et une question mal formée (à écarter)."""
    questions = []
    for p in pages:
        for s in split_sentences(p.text or '')[:3]:
            questions.append({'page': p.number, 'question': 'Que dit la page %s ? (%s)' % (p.number, s.index),
                              'choices': ['Réponse A %s' % s.index, 'Réponse B', 'Réponse C', 'Réponse D'],
                              'answer': 0, 'explanation': 'Voir la page.', 'quote': s.text, 'skill': 'detail'})
    questions.append({'page': pages[0].number, 'question': 'Inventée ?', 'choices': ['a', 'b', 'c', 'd'],
                      'answer': 1, 'explanation': '…', 'quote': 'Cette phrase ne figure nulle part dans le texte.',
                      'skill': 'detail'})
    questions.append({'page': pages[0].number, 'question': 'Deux choix ?', 'choices': ['a', 'b'], 'answer': 0,
                      'explanation': '…', 'quote': split_sentences(pages[0].text)[0].text, 'skill': 'detail'})
    return {'questions': questions}, 1000, 2000, 'claude-opus-5-5'


class DocCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Player = cls.env['gh.player'].sudo()
        cls.Doc = cls.env['gh.document'].sudo()
        cls.Question = cls.env['gh.question'].sudo()
        cls.Request = cls.env['gh.ai.request'].sudo()
        cls.ICP = cls.env['ir.config_parameter'].sudo()
        cls.ICP.set_param('genie_en_herbe.anthropic_api_key', 'test-key')
        cls.ICP.set_param('genie_en_herbe.ai_enabled', 'True')
        cls.prof = cls.player('Professeur')
        cls.eleve = cls.player('Eleve')

    @classmethod
    def player(cls, pseudo):
        partner = cls.env['res.partner'].create({'name': pseudo, 'phone': '+225070%07d' % (abs(hash(pseudo)) % 10 ** 7)})
        return cls.env['gh.player'].sudo().create({'partner_id': partner.id, 'pseudo': pseudo})

    def run_ai(self):
        with patch.object(type(self.Request), '_call_claude', side_effect=lambda pages, lang: fake_claude(pages, lang)):
            self.Request._cron_process()


@tagged('post_install', '-at_install')
class TestDocuments(DocCase):

    def test_envoi_par_lots_et_langue(self):
        doc = self.Doc.begin(self.prof, {'name': 'Nile', 'file_kind': 'PDF', 'unit': 'slide'})
        self.assertEqual(doc.state, 'uploading')
        doc.add_pages(self.prof, [{'label': '', 'text': EN_PAGES[0][1]}])
        doc.add_pages(self.prof, [{'label': 'Résumé', 'text': 'The Nile is long. ' * 10}])
        doc.finish(self.prof)
        self.assertEqual(doc.state, 'ready')
        self.assertEqual(doc.lang, 'en')
        self.assertEqual(doc.file_kind, 'pdf')
        pages = doc.page_ids.sorted('number')
        self.assertEqual(pages.mapped('number'), [1, 2])
        self.assertEqual(pages.mapped('label'), ['Diapositive 1', 'Résumé'])
        self.assertIn('eleven countries before it reaches', pages[0].text)  # ligne coupée recollée (clean_text)

    def test_page_trop_longue_coupee(self):
        self.ICP.set_param('genie_en_herbe.max_page_chars', '1000')
        long_text = ' '.join(t for _k, t in FR_PAGES) * 2
        doc = self.Doc.begin(self.prof, {'name': 'Long'})
        doc.add_pages(self.prof, [{'label': 'Page 1', 'text': long_text}])
        labels = doc.page_ids.sorted('number').mapped('label')
        self.assertGreater(len(labels), 1)
        self.assertTrue(labels[0].startswith('Page 1 (1/'))
        self.assertTrue(all(p.chars <= 1000 for p in doc.page_ids))

    def test_limites(self):
        self.ICP.set_param('genie_en_herbe.max_pages', '2')
        doc = self.Doc.begin(self.prof, {'name': 'Court'})
        with self.assertRaises(UserError):
            doc.add_pages(self.prof, [{'text': 'abc'}] * 3)
        with self.assertRaises(AccessError):
            doc.add_pages(self.eleve, [{'text': 'abc'}])
        empty = self.Doc.begin(self.prof, {'name': 'Scan'})
        empty.add_pages(self.prof, [{'text': ''}, {'text': 'Sommaire'}])
        with self.assertRaises(UserError):
            empty.finish(self.prof)  # aucun texte lisible
        with self.assertRaises(UserError):
            self.Doc.begin(self.prof, {'name': ''})

    def test_doublon(self):
        make_document(self.env, self.prof, FR_PAGES)
        with self.assertRaises(DuplicateDocument):
            make_document(self.env, self.prof, FR_PAGES)
        # Un autre joueur peut charger le même texte.
        self.assertTrue(make_document(self.env, self.eleve, FR_PAGES))

    def test_partage_par_code_et_public(self):
        doc = make_document(self.env, self.prof, FR_PAGES)
        self.assertFalse(doc._can_read(self.eleve))
        doc.set_visibility(self.prof, 'shared')
        self.assertTrue(doc.share_code.startswith('D'))
        self.assertFalse(doc._can_read(self.eleve), "partagé : seulement après avoir saisi le code")
        self.Doc.add_shared(self.eleve, doc.share_code.lower())
        self.assertTrue(doc._can_read(self.eleve))
        with self.assertRaises(AccessError):
            doc.set_visibility(self.eleve, 'public')
        old = doc.share_code
        doc.renew_share_code(self.prof)
        with self.assertRaises(UserError):
            self.Doc.add_shared(self.player('Autre'), old)
        doc.set_visibility(self.prof, 'private')
        self.assertFalse(doc._can_read(self.eleve))
        doc.set_visibility(self.prof, 'public')
        self.assertTrue(doc._can_read(self.player('Passant')))

    def test_suppression_refusee_pendant_un_match(self):
        doc = make_document(self.env, self.prof, FR_PAGES)
        self.Question._offline_for_pages(doc.page_ids)
        match = self.env['gh.match'].sudo().create_free(self.prof, {'pages': doc.page_ids.ids, 'question_count': 5})
        with self.assertRaises(UserError):
            doc.remove(self.prof)
        match.leave(self.prof)
        self.assertEqual(match.state, 'cancelled')
        doc.remove(self.prof)
        self.assertFalse(doc.exists())

    def test_envoi_inacheve_supprime(self):
        doc = self.Doc.begin(self.prof, {'name': 'Abandon'})
        self.env.cr.execute("UPDATE gh_document SET create_date = now() - interval '2 days' WHERE id = %s", [doc.id])
        doc.invalidate_recordset()
        self.assertEqual(self.Doc._cron_gc(), 1)
        self.assertFalse(doc.exists())


@tagged('post_install', '-at_install')
class TestQuestions(DocCase):

    def setUp(self):
        super().setUp()
        self.doc = make_document(self.env, self.prof, FR_PAGES)
        self.pages = self.doc.page_ids.sorted('number')

    def test_sans_ia_tout_de_suite(self):
        info = self.Question.prepare(self.prof, self.pages, use_ai=False)
        self.assertEqual(info['queued'], 0)
        self.assertTrue(all(self.pages.mapped('offline_done')))
        qs = self.Question.search([('document_id', '=', self.doc.id)])
        self.assertGreaterEqual(len(qs), 6)
        self.assertEqual(set(qs.mapped('source')), {'offline'})
        for q in qs:
            self.assertIn(' '.join(q.quote.split())[:30], ' '.join(q.page_id.text.split()))
        # Déjà faites : pas de doublon.
        self.Question.prepare(self.prof, self.pages, use_ai=False)
        self.assertEqual(self.Question.search_count([('document_id', '=', self.doc.id)]), len(qs))

    def test_ia_en_file_puis_traitee(self):
        info = self.Question.prepare(self.prof, self.pages)
        self.assertEqual(info['queued'], 1, "trois petites pages : un seul lot")
        self.assertEqual(set(self.pages.mapped('ai_state')), {'queued'})
        self.run_ai()
        req = self.Request.search([('player_id', '=', self.prof.id)])
        self.assertEqual(req.state, 'done')
        self.assertEqual(req.dropped, 2, "citation inventée et question à deux choix écartées")
        self.assertEqual(set(self.pages.mapped('ai_state')), {'done'})
        ai = self.Question.search([('document_id', '=', self.doc.id), ('source', '=', 'ai')])
        self.assertEqual(len(ai), 9)
        self.assertGreater(req.cost, 0)
        # Chaque question IA est rangée sur la page de sa citation.
        for q in ai:
            self.assertIn('page %s' % q.page_id.number, q.prompt)

    def test_quota_gratuit_puis_credits_puis_refus(self):
        self.ICP.set_param('genie_en_herbe.ai_daily_quota', '1')
        self.ICP.set_param('genie_en_herbe.ai_chunk_chars', '1000')  # le minimum : pages 1 | 2 et 3
        info = self.Question.prepare(self.prof, self.pages)
        self.assertEqual(info['queued'], 1)
        self.assertEqual(info['ai'], 'quota')
        self.assertEqual(self.pages.mapped('ai_state'), ['queued', 'none', 'none'])
        code = self.env['gh.ai.code'].sudo().create({'credits': 1})
        self.env['gh.ai.code'].sudo().redeem(self.prof, code.name.replace('-', '').lower())
        self.assertEqual(self.prof.ai_credits, 1)
        with self.assertRaises(UserError):
            self.env['gh.ai.code'].sudo().redeem(self.eleve, code.name)  # déjà utilisé
        info = self.Question.prepare(self.prof, self.pages)
        self.assertEqual((info['queued'], info['ai']), (1, ''))
        self.assertEqual(self.prof.ai_credits, 0)
        self.assertEqual(set(self.pages.mapped('ai_state')), {'queued'})
        # Plus de quota ni de crédit : refusé (et compté, pour mesurer le besoin).
        other = make_document(self.env, self.prof, EN_PAGES, name='Nile')
        info = self.Question.prepare(self.prof, other.page_ids)
        self.assertEqual((info['queued'], info['ai']), (0, 'quota'))
        self.assertEqual(self.Request.search_count([('state', '=', 'refused')]), 2)

    def test_echec_rend_le_quota_et_le_credit(self):
        self.ICP.set_param('genie_en_herbe.ai_daily_quota', '0')
        self.prof.ai_credits = 1
        self.Question.prepare(self.prof, self.pages)
        self.assertEqual(self.prof.ai_credits, 0)

        def boom(pages, lang):
            raise AiRefusal("indisponible", 'unavailable')
        with patch.object(type(self.Request), '_call_claude', side_effect=boom):
            self.Request._cron_process()
        self.assertEqual(self.prof.ai_credits, 1, "crédit rendu")
        self.assertEqual(set(self.pages.mapped('ai_state')), {'failed'})
        # Les questions sans IA restent disponibles.
        self.assertTrue(self.Question.search_count([('document_id', '=', self.doc.id)]))

    def test_ia_indisponible(self):
        self.ICP.set_param('genie_en_herbe.ai_enabled', 'False')
        info = self.Question.prepare(self.prof, self.pages)
        self.assertEqual(info, {'queued': 0, 'ai': 'disabled'})
        self.assertTrue(all(self.pages.mapped('offline_done')))

    def test_relecture(self):
        self.Question.prepare(self.prof, self.pages, use_ai=False)
        page = self.pages[0]
        q = self.Question.search([('page_id', '=', page.id)], limit=1)
        with self.assertRaises(AccessError):
            self.Question._owned(self.eleve, q.id)
        q = self.Question._owned(self.prof, q.id)
        q.edit({'prompt': 'Où le Niger prend-il sa source ?', 'options': ['En Guinée', 'Au Mali', 'Au Niger'],
                'answer': 0})
        self.assertEqual((q.source, q.state), ('manual', 'validated'))
        with self.assertRaises(UserError):
            q.edit({'options': ['Oui', 'oui']})
        manual = self.Question.create_manual(self.prof, page, {
            'prompt': 'Quelle ville est un centre de savoir ?', 'options': ['Tombouctou', 'Gao'], 'answer': 0})
        self.assertEqual(manual.state, 'validated')
        others = self.Question.search([('page_id', '=', page.id), ('state', '!=', 'validated')])
        self.Question.regenerate(self.prof, page, use_ai=False)
        self.assertFalse(any(o.active for o in others.with_context(active_test=False)))
        self.assertTrue(q.active and manual.active, "les validées sont gardées")

    def test_tirage_prend_les_validees_d_abord(self):
        self.Question.prepare(self.prof, self.pages, use_ai=False)
        bank = self.Question.search([('document_id', '=', self.doc.id)])
        chosen = bank[:5]
        chosen.write({'state': 'validated'})
        match = self.env['gh.match'].sudo().create_free(self.prof, {'pages': self.pages.ids, 'question_count': 5})
        match.join(self.eleve)
        match.start(self.prof, now=T0)
        self.assertEqual(set(match.question_ids.mapped('qid')), {str(i) for i in chosen.ids})
        self.assertTrue(all(q.times_played == 1 for q in chosen))
        for mq in match.question_ids:
            bq = self.Question.browse(int(mq.qid))
            self.assertEqual(mq.options[mq.answer], bq.options[bq.answer], "propositions mélangées, réponse suivie")
            self.assertEqual(mq.quote, bq.quote)

    def test_questions_en_preparation(self):
        self.Question.prepare(self.prof, self.pages[:1], use_ai=True)
        self.pages[:1].offline_done = True
        self.Question.search([('page_id', '=', self.pages[0].id)]).write({'active': False})
        with self.assertRaises(GameRefusal) as cm:
            self.env['gh.match'].sudo().create_free(self.prof, {'pages': self.pages[:1].ids, 'question_count': 5})
        self.assertEqual(cm.exception.code, 'QUESTIONS_PENDING')


@tagged('post_install', '-at_install')
class TestHost(DocCase):

    def setUp(self):
        super().setUp()
        self.doc = make_document(self.env, self.prof, FR_PAGES, visibility='public')
        self.Question.prepare(self.prof, self.doc.page_ids, use_ai=False)
        self.Match = self.env['gh.match'].sudo()

    def test_organiser_sans_jouer(self):
        a, b = self.player('Lions'), self.player('Aigles')
        m = self.Match.create_free(self.prof, {'pages': self.doc.page_ids.ids, 'question_count': 5,
                                               'mode': 'team', 'host_only': True})
        self.assertFalse(m.participant_ids)
        self.assertEqual(self.prof.match_id, m, "l'organisateur ne joue pas ailleurs pendant ce temps")
        with self.assertRaises(GameRefusal):
            m.join(self.prof)
        m.join(a)
        m.create_team(a, 'CM2 A')
        m.join(b)
        m.create_team(b, 'CM2 B')
        with self.assertRaises(AccessError):
            m.start(a, now=T0)
        m.start(self.prof, now=T0)
        m._lock()
        m._advance(T0 + COUNTDOWN_SECONDS)
        with self.assertRaises(AccessError):
            m.buzz(self.prof, now=T0 + 5)
        state = m._state()
        self.assertTrue(state['host_only'])
        self.assertEqual(len(state['pages']), 3)
        # L'organisateur s'en va en cours de match : le match continue, il est libéré.
        m.leave(self.prof, now=T0 + 6)
        self.assertEqual(m.state, 'running')
        self.assertFalse(self.prof.match_id)

    def test_organisateur_part_avant_le_debut(self):
        a = self.player('Awa')
        m = self.Match.create_free(self.prof, {'pages': self.doc.page_ids.ids, 'question_count': 5, 'host_only': True})
        m.join(a)
        m.leave(self.prof)
        self.assertEqual((m.creator_id, m.host_only), (a, False))
        m2 = self.Match.create_free(self.prof, {'pages': self.doc.page_ids.ids, 'question_count': 5, 'host_only': True})
        m2.leave(self.prof)
        self.assertEqual(m2.state, 'cancelled')
        self.assertFalse(self.prof.match_id)

    def test_fin_de_match_libere_l_organisateur(self):
        a, b = self.player('Ali'), self.player('Bea')
        m = self.Match.create_free(self.prof, {'pages': self.doc.page_ids.ids, 'question_count': 5, 'host_only': True})
        m.join(a)
        m.join(b)
        m.start(self.prof, now=T0)
        m.leave(b, now=T0 + 1)
        self.assertEqual(m.state, 'finished')
        self.assertFalse(self.prof.match_id)
