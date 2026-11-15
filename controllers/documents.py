# -*- coding: utf-8 -*-
"""API des documents, de leurs pages et de leurs questions, et des crédits IA.

Envoi d'un document (le texte est extrait sur le téléphone) :
``/document/begin`` → ``/document/pages`` (par lots) → ``/document/finish``.

Les questions d'une page ne sont lisibles que par le propriétaire du document
(relecture). Les autres joueurs voient seulement leur nombre et leur état.
"""
from odoo import _
from odoo.exceptions import AccessError, UserError
from odoo.http import Controller, request, route

from ..models.gh_document import DuplicateDocument
from .common import API, RL_REDEEM, RL_SEARCH, RL_UPLOAD, arg_int, fail, ok, player_route, rate_limited


def _pages_for(player, page_ids, owner=False):
    """Pages lisibles par le joueur (toutes, sinon refus)."""
    if not isinstance(page_ids, list) or not page_ids:
        raise UserError(_("Choisissez au moins une page."))
    try:
        ids = list({int(i) for i in page_ids})
    except (TypeError, ValueError) as e:
        raise UserError(_("Sélection de pages illisible.")) from e
    if len(ids) > 400:
        raise UserError(_("Trop de pages à la fois."))
    pages = request.env['gh.document.page'].sudo().browse(ids).exists()
    if len(pages) != len(ids):
        raise AccessError(_("Une des pages n'existe plus."))
    for doc in pages.document_id:
        if owner:
            doc._check_owner(player)
        else:
            doc._check_read(player)
    return pages


def _page_readable(player, page, match_id=None, tournament_id=None):
    """Un joueur lit une page de son document, d'un document qui lui est accessible, ou d'un match /
    tournoi auquel il participe (révision avant, correction après), même si le document est privé."""
    if page.document_id._can_read(player):
        return True
    if match_id:
        match = request.env['gh.match'].sudo().browse(int(match_id)).exists()
        if match and page in match.page_ids and (
                match.creator_id == player or player in match.participant_ids.player_id):
            return True
    if tournament_id:
        t = request.env['gh.tournament'].sudo().browse(int(tournament_id)).exists()
        if t and (t.organizer_id == player or t._entry_of(player)):
            pages = t.page_ids
            for ids in (t.round_pages or {}).values():
                pages |= request.env['gh.document.page'].sudo().browse(ids).exists()
            return page in pages
    return False


def _status(pages):
    counts = pages._question_counts()
    return [p._summary(counts.get(p.id)) for p in pages.sorted(lambda p: (p.document_id.id, p.number))]


class GenieDocumentApi(Controller):

    # ================================================================== bibliothèque
    @route(API + '/documents', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    @player_route
    def documents(self, player, **kw):
        Doc = request.env['gh.document'].sudo()
        mine = Doc.search([('owner_id', '=', player.id), ('state', '=', 'ready')])
        shared = Doc.search([('access_ids.player_id', '=', player.id), ('state', '=', 'ready'),
                             ('visibility', '!=', 'private')])
        return ok(documents=[d._summary(player) for d in mine],
                  shared=[d._summary(player) for d in shared - mine])

    @route(API + '/documents/public', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    @player_route
    def documents_public(self, player, **kw):
        refusal = rate_limited('search', RL_SEARCH, subject='p%s' % player.id)
        if refusal:
            return refusal
        domain = [('visibility', '=', 'public'), ('state', '=', 'ready')]
        q = (kw.get('q') or '').strip()[:60]
        if q:
            domain.append(('name', 'ilike', q))
        docs = request.env['gh.document'].sudo().search(domain, limit=50)
        return ok(documents=[d._summary(player) for d in docs])

    @route(API + '/document/get', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    @player_route
    def document_get(self, player, **kw):
        doc = request.env['gh.document']._for_player(player, kw.get('document_id'))
        return ok(document=doc._detail(player))

    @route(API + '/document/page', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    @player_route
    def document_page(self, player, **kw):
        """Texte d'une page (lecture, révision avant un match, correction après)."""
        page = request.env['gh.document.page'].sudo().browse(arg_int(kw, 'page_id')).exists()
        if not page or not _page_readable(player, page, kw.get('match_id'), kw.get('tournament_id')):
            raise AccessError(_("Cette page n'est pas accessible."))
        doc = page.document_id
        return ok(page={'id': page.id, 'number': page.number, 'label': page.label, 'text': page.text or '',
                        'document_id': doc.id, 'document': doc.name, 'unit': doc.unit, 'lang': doc.lang,
                        'page_count': doc.page_count})

    # ================================================================== envoi
    @route(API + '/document/begin', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    @player_route
    def document_begin(self, player, **kw):
        refusal = rate_limited('upload', RL_UPLOAD, subject='p%s' % player.id)
        if refusal:
            return refusal
        doc = request.env['gh.document'].sudo().begin(player, kw)
        return ok(document=doc._summary(player))

    @route(API + '/document/pages', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    @player_route
    def document_pages(self, player, **kw):
        refusal = rate_limited('upload', RL_UPLOAD, subject='p%s' % player.id)
        if refusal:
            return refusal
        doc = request.env['gh.document'].sudo().browse(arg_int(kw, 'document_id')).exists()
        if not doc:
            raise AccessError(_("Document introuvable."))
        doc.add_pages(player, kw.get('pages'))
        return ok(page_count=len(doc.page_ids))

    @route(API + '/document/finish', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    @player_route
    def document_finish(self, player, **kw):
        doc = request.env['gh.document'].sudo().browse(arg_int(kw, 'document_id')).exists()
        if not doc:
            raise AccessError(_("Document introuvable."))
        try:
            with request.env.cr.savepoint():
                doc.with_context(gh_lang_given=bool(kw.get('lang_given'))).finish(player)
        except DuplicateDocument as e:
            doc.unlink()  # l'envoi en double est abandonné ; l'application ouvre l'existant
            return fail('DUPLICATE', str(e.args[0]), document_id=e.document.id)
        return ok(document=doc._detail(player))

    # ================================================================== gestion
    @route(API + '/document/update', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    @player_route
    def document_update(self, player, **kw):
        doc = request.env['gh.document']._for_player(player, kw.get('document_id'), owner=True)
        vals = {}
        if kw.get('name'):
            name = str(kw['name']).strip()[:120]
            if len(name) < 2:
                raise UserError(_("Donnez un titre au document."))
            vals['name'] = name
        if kw.get('lang') in dict(doc._fields['lang'].selection):
            vals['lang'] = kw['lang']
        if vals:
            doc.write(vals)
        if kw.get('visibility'):
            doc.set_visibility(player, kw['visibility'])
        return ok(document=doc._detail(player))

    @route(API + '/document/share', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    @player_route
    def document_share(self, player, **kw):
        doc = request.env['gh.document']._for_player(player, kw.get('document_id'), owner=True)
        if kw.get('renew'):
            doc.renew_share_code(player)
        elif doc.visibility == 'private' or not doc.share_code:
            doc.set_visibility(player, 'shared' if doc.visibility == 'private' else doc.visibility)
            if not doc.share_code:
                doc.renew_share_code(player)
        base = request.httprequest.host_url.rstrip('/')
        return ok(document=doc._summary(player), share_code=doc.share_code,
                  link='%s/genie-en-herbe/rejoindre/%s' % (base, doc.share_code))

    @route(API + '/document/add', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    @player_route
    def document_add(self, player, **kw):
        refusal = rate_limited('search', RL_SEARCH, subject='p%s' % player.id)
        if refusal:
            return refusal
        doc = request.env['gh.document'].sudo().add_shared(player, kw.get('code'))
        return ok(document=doc._detail(player))

    @route(API + '/document/forget', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    @player_route
    def document_forget(self, player, **kw):
        doc = request.env['gh.document'].sudo().browse(arg_int(kw, 'document_id')).exists()
        if doc:
            doc.remove_shared(player)
        return ok()

    @route(API + '/document/delete', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    @player_route
    def document_delete(self, player, **kw):
        doc = request.env['gh.document'].sudo().with_context(active_test=False).browse(
            arg_int(kw, 'document_id')).exists()
        if doc:
            doc.remove(player)
        return ok()

    # ================================================================== questions
    @route(API + '/questions/prepare', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    @player_route
    def questions_prepare(self, player, **kw):
        """Prépare les questions des pages choisies (sans IA tout de suite, IA en arrière-plan)."""
        pages = _pages_for(player, kw.get('page_ids'))
        info = request.env['gh.question'].sudo().prepare(player, pages, use_ai=kw.get('ai', True) is not False)
        return ok(ai_queued=info['queued'], ai_reason=info['ai'], pages=_status(pages),
                  quota=request.env['gh.ai.request'].sudo()._quota_info(player))

    @route(API + '/questions/status', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    @player_route
    def questions_status(self, player, **kw):
        pages = _pages_for(player, kw.get('page_ids'))
        return ok(pages=_status(pages))

    @route(API + '/questions/list', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    @player_route
    def questions_list(self, player, **kw):
        page = _pages_for(player, [kw.get('page_id')], owner=True)
        questions = request.env['gh.question'].sudo().search([('page_id', '=', page.id)])
        return ok(page=_status(page)[0], questions=[q._owner_data() for q in questions])

    @route(API + '/question/save', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    @player_route
    def question_save(self, player, **kw):
        """Créer (``page_id``) ou corriger (``question_id``) une question ; elle devient validée."""
        Question = request.env['gh.question'].sudo()
        if kw.get('question_id'):
            q = Question._owned(player, kw['question_id'])
            q.edit(kw)
        else:
            page = _pages_for(player, [kw.get('page_id')], owner=True)
            q = Question.create_manual(player, page, kw)
        return ok(question=q._owner_data())

    @route(API + '/question/validate', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    @player_route
    def question_validate(self, player, **kw):
        Question = request.env['gh.question'].sudo()
        if kw.get('page_id'):
            page = _pages_for(player, [kw['page_id']], owner=True)
            Question.search([('page_id', '=', page.id)]).write({'state': 'validated'})
            return ok(page=_status(page)[0])
        q = Question._owned(player, kw.get('question_id'))
        q.write({'state': 'proposed' if kw.get('validated') is False else 'validated'})
        return ok(question=q._owner_data())

    @route(API + '/question/delete', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    @player_route
    def question_delete(self, player, **kw):
        q = request.env['gh.question'].sudo()._owned(player, kw.get('question_id'))
        q.write({'active': False})
        return ok()

    @route(API + '/questions/regenerate', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    @player_route
    def questions_regenerate(self, player, **kw):
        page = _pages_for(player, [kw.get('page_id')], owner=True)
        info = request.env['gh.question'].sudo().regenerate(player, page, use_ai=kw.get('ai', True) is not False)
        return ok(ai_queued=info['queued'], ai_reason=info['ai'], page=_status(page)[0],
                  quota=request.env['gh.ai.request'].sudo()._quota_info(player))

    # ================================================================== crédits IA
    @route(API + '/ai/quota', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    @player_route
    def ai_quota(self, player, **kw):
        return ok(quota=request.env['gh.ai.request'].sudo()._quota_info(player))

    @route(API + '/ai/redeem', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    @player_route
    def ai_redeem(self, player, **kw):
        refusal = rate_limited('redeem', RL_REDEEM, subject='p%s' % player.id)
        if refusal:
            return refusal
        code = request.env['gh.ai.code'].sudo().redeem(player, kw.get('code'))
        return ok(credits_added=code.credits, quota=request.env['gh.ai.request'].sudo()._quota_info(player))
