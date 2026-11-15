# -*- coding: utf-8 -*-
"""L'API JSON-RPC, de bout en bout (jeton porteur, enveloppe success)."""
import json

from odoo.tests import HttpCase, tagged

from ..game.tests_standalone import FR_PAGES

API = '/genie-en-herbe/api/v1'


@tagged('post_install', '-at_install')
class TestApi(HttpCase):

    def call(self, path, token=None, **params):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer %s' % token
        r = self.url_open(API + path, data=json.dumps({'jsonrpc': '2.0', 'method': 'call', 'params': params}),
                          headers=headers)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertNotIn('error', body, body)
        return body['result']

    def account(self, phone, pseudo):
        partner = self.env['res.partner'].create({'name': phone, 'phone': phone})
        token = self.env['gh.api.session'].sudo()._issue(partner)
        self.assertTrue(self.call('/player/register', token, pseudo=pseudo, lang='fr')['success'])
        return token

    def upload(self, token, name='Le fleuve Niger'):
        begun = self.call('/document/begin', token, name=name, filename='niger.pdf', file_kind='pdf', unit='page')
        doc_id = begun['document']['id']
        self.assertEqual(begun['document']['state'], 'uploading')
        for i, (_k, text) in enumerate(FR_PAGES):  # un lot par page, comme sur un réseau lent
            sent = self.call('/document/pages', token, document_id=doc_id, pages=[{'label': 'Page %s' % (i + 1),
                                                                                    'text': text}])
            self.assertEqual(sent['page_count'], i + 1)
        return self.call('/document/finish', token, document_id=doc_id)

    def test_document_puis_match(self):
        prof = self.account('+2250700000011', 'Professeur')
        eleve = self.account('+2250700000012', 'Eleve')
        intrus = self.account('+2250700000013', 'Intrus')
        done = self.upload(prof)
        self.assertTrue(done['success'], done)
        doc = done['document']
        self.assertEqual((doc['state'], doc['lang'], doc['page_count']), ('ready', 'fr', 3))
        page_ids = [p['id'] for p in doc['pages']]
        # Le même texte une seconde fois : on retrouve le premier.
        dup = self.upload(prof)
        self.assertEqual((dup['error_code'], dup['error']['details']['document_id']), ('DUPLICATE', doc['id']))
        self.assertEqual(len(self.call('/documents', prof)['documents']), 1)
        # Privé : l'élève ne le voit pas.
        self.assertEqual(self.call('/document/get', eleve, document_id=doc['id'])['error_code'], 'FORBIDDEN')
        # Questions sans IA (l'IA n'est pas configurée dans les tests).
        prep = self.call('/questions/prepare', prof, page_ids=page_ids)
        self.assertEqual(prep['ai_reason'], 'not_configured')
        self.assertTrue(all(p['questions']['total'] >= 2 for p in prep['pages']))
        listed = self.call('/questions/list', prof, page_id=page_ids[0])
        self.assertTrue(listed['questions'][0]['options'])
        self.assertEqual(self.call('/questions/list', eleve, page_id=page_ids[0])['error_code'], 'FORBIDDEN')
        qid = listed['questions'][0]['id']
        saved = self.call('/question/save', prof, question_id=qid, prompt='Où le Niger prend-il sa source ?',
                          options=['En Guinée', 'Au Mali', 'Au Bénin'], answer=0, explain='Page 1.')
        self.assertEqual((saved['question']['source'], saved['question']['state']), ('manual', 'validated'))
        self.assertEqual(self.call('/question/delete', eleve, question_id=qid)['error_code'], 'FORBIDDEN')
        # Match organisé sans jouer, sur deux pages ; l'élève rejoint par le code.
        created = self.call('/match/create', prof, pages=page_ids[:2], question_count=5, host_only=True)
        self.assertTrue(created['success'], created)
        match = created['match']
        self.assertTrue(match['host_only'])
        self.assertIsNone(created['me'])
        self.assertIn('Le fleuve Niger : p. 1–2', match['pages_label'])
        joined = self.call('/join', eleve, code=match['code'])
        self.assertEqual(joined['kind'], 'match')
        # Les joueurs du match relisent ses pages, même si le document est privé ; pas les autres.
        page = self.call('/document/page', eleve, page_id=page_ids[0], match_id=match['id'])
        self.assertIn('Niger', page['page']['text'])
        self.assertEqual(self.call('/document/page', eleve, page_id=page_ids[2], match_id=match['id'])['error_code'],
                         'FORBIDDEN', "page hors du match")
        self.assertEqual(self.call('/document/page', intrus, page_id=page_ids[0], match_id=match['id'])['error_code'],
                         'FORBIDDEN')
        # L'organisateur lit l'état de son match sans y jouer.
        self.assertEqual(self.call('/match/state', prof, match_id=match['id'])['match']['id'], match['id'])
        # Partage par code : l'élève ajoute le document à sa bibliothèque.
        shared = self.call('/document/share', prof, document_id=doc['id'])
        self.assertIn('/genie-en-herbe/rejoindre/%s' % shared['share_code'], shared['link'])
        added = self.call('/document/add', eleve, code=shared['share_code'])
        self.assertEqual(added['document']['id'], doc['id'])
        self.assertEqual([d['id'] for d in self.call('/documents', eleve)['shared']], [doc['id']])
        self.assertEqual(self.call('/document/delete', eleve, document_id=doc['id'])['error_code'], 'FORBIDDEN')
        # Quota IA et code inconnu.
        self.assertIn('free_remaining', self.call('/ai/quota', prof)['quota'])
        self.assertEqual(self.call('/ai/redeem', prof, code='GH-XXXX-XXXX')['error_code'], 'REFUSED')

    def test_documents_publics(self):
        prof = self.account('+2250700000021', 'Prof2')
        doc = self.upload(prof, name='Histoire du Mali')['document']
        other = self.account('+2250700000022', 'Lecteur')
        self.assertEqual(self.call('/documents/public', other, q='mali')['documents'], [])
        self.call('/document/update', prof, document_id=doc['id'], visibility='public', name='Histoire du Mali (3e)')
        found = self.call('/documents/public', other, q='mali')['documents']
        self.assertEqual([d['name'] for d in found], ['Histoire du Mali (3e)'])
        self.assertIsNone(found[0]['share_code'])
        self.assertEqual(self.call('/document/get', other, document_id=doc['id'])['document']['page_count'], 3)

    def test_page_d_invitation(self):
        page = self.url_open('/genie-en-herbe/rejoindre/ab12cd')
        self.assertEqual(page.status_code, 200)
        self.assertIn('AB12CD', page.text)
        self.assertIn('scheme=genieenherbe', page.text)
        self.assertIn('/apps/genie-en-herbe/web/?join=AB12CD', page.text)
        doc = self.url_open('/genie-en-herbe/rejoindre/dabc2345')
        self.assertIn('DABC2345', doc.text)
        self.assertIn('document partagé', doc.text)

    def test_inviter_par_pseudo_et_par_groupe(self):
        prof = self.account('+2250700000031', 'Prof3')
        awa = self.account('+2250700000032', 'Awa3')
        ben = self.account('+2250700000033', 'Ben3')
        doc = self.upload(prof)['document']
        page_ids = [p['id'] for p in doc['pages']]
        self.call('/questions/prepare', prof, page_ids=page_ids)
        match = self.call('/match/create', prof, pages=page_ids, question_count=5, host_only=True)['match']
        # Groupe : Ben y entre par le code saisi dans « Rejoindre ».
        group = self.call('/group/create', prof, name='3e B')
        self.assertIn('/genie-en-herbe/rejoindre/%s' % group['group']['code'], group['link'])
        joined = self.call('/join', ben, code=group['group']['code'])
        self.assertEqual((joined['kind'], joined['group']['member_count']), ('group', 2))
        # Recherche du pseudo d'Awa, puis invitation d'Awa et de tout le groupe.
        awa_id = self.call('/player/search', prof, q='awa3')['players'][0]['id']
        sent = self.call('/match/invite', prof, match_id=match['id'], player_ids=[awa_id],
                         group_id=group['group']['id'])
        self.assertEqual(sorted(sent['invited']), ['Awa3', 'Ben3'])
        self.assertEqual(len(sent['match']['invitations']), 2)
        # Awa voit l'invitation et l'accepte : elle est dans la salle d'attente.
        inv = self.call('/invitations', awa)['match_invitations']
        self.assertEqual((inv[0]['code'], inv[0]['inviter']), (match['code'], 'Prof3'))
        accepted = self.call('/match/invitation/answer', awa, invitation_id=inv[0]['id'])
        self.assertEqual([p['pseudo'] for p in accepted['match']['participants']], ['Awa3'])
        # Ben refuse, puis bloque Prof3 : ses futures invitations sont écartées.
        inv_ben = self.call('/invitations', ben)['match_invitations'][0]
        self.assertTrue(self.call('/match/invitation/answer', ben, invitation_id=inv_ben['id'], accept=False)['success'])
        self.call('/player/block', ben, player_id=self.call('/me', prof)['player']['id'])
        again = self.call('/match/invite', prof, match_id=match['id'], player_ids=[], group_id=group['group']['id'])
        self.assertEqual([s['reason'] for s in again['skipped']], ['blocked'])
        # Réglage, mes joueurs.
        self.assertEqual(self.call('/player/settings', awa, invite_policy='known')['invite_policy'], 'known')
        self.assertEqual(self.call('/contacts', awa)['invite_policy'], 'known')
        self.assertEqual(self.call('/groups', ben)['groups'][0]['name'], '3e B')
        page = self.url_open('/genie-en-herbe/rejoindre/%s' % group['group']['code'].lower())
        self.assertIn('Rejoindre un groupe', page.text)
