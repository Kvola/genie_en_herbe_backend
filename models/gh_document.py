# -*- coding: utf-8 -*-
"""Documents chargés par les joueurs, découpés en pages.

L'application lit le fichier sur le téléphone (PDF, Word, PowerPoint, Excel,
OpenDocument, texte…, avec reconnaissance de texte pour les pages scannées) et
envoie ici le TEXTE, page par page. Le fichier lui-même ne quitte pas le
téléphone.

Une « page » dépend du format : page d'un PDF, diapositive d'une présentation,
feuille d'un classeur, et, pour un traitement de texte (qui n'a pas de pages
fixes), les sauts de page enregistrés par Word s'il y en a, sinon des parties
d'environ 3 000 caractères coupées entre deux paragraphes.

Envoi en plusieurs fois (réseau mobile) : ``begin`` crée le document en état
« envoi », ``add_pages`` ajoute des lots de pages, ``finish`` le rend
utilisable. Un envoi resté inachevé est supprimé au bout d'un jour.

Visibilité : privé (le propriétaire seul), partagé (ceux qui ont saisi son
code de partage) ou public (tous les joueurs). Les questions d'un document ne
sont lisibles que par son propriétaire ; les autres peuvent lire les pages et
organiser des matchs dessus.
"""
import hashlib
import logging
import random
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from ..game.text_tools import TEXT_LANGUAGES, clean_text, detect_language
from .gh_config import gh_param

_logger = logging.getLogger(__name__)

SHARE_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'
LANG_SELECTION = [('fr', "Français"), ('en', "English"), ('es', "Español"), ('pt', "Português"), ('de', "Deutsch"),
                  ('it', "Italiano")]
UNIT_SELECTION = [('page', "Page"), ('slide', "Diapositive"), ('sheet', "Feuille"), ('part', "Partie")]
MAX_PAGES_PER_CALL = 100
MAX_CHARS_PER_CALL = 600000


class GhDocument(models.Model):
    _name = 'gh.document'
    _description = "Document (Génie en herbe)"
    _order = 'id desc'

    name = fields.Char("Titre", required=True)
    owner_id = fields.Many2one('gh.player', "Propriétaire", required=True, ondelete='cascade', index=True)
    filename = fields.Char("Fichier d'origine")
    file_kind = fields.Char("Format", help="Extension du fichier d'origine (pdf, docx, pptx…).")
    unit = fields.Selection(UNIT_SELECTION, "Découpage", required=True, default='page')
    lang = fields.Selection(LANG_SELECTION, "Langue", required=True, default='fr')
    state = fields.Selection([('uploading', "Envoi en cours"), ('ready', "Prêt")], "État", required=True,
                             default='uploading', index=True)
    visibility = fields.Selection([('private', "Privé"), ('shared', "Partagé par code"), ('public', "Public")],
                                  "Visibilité", required=True, default='private', index=True)
    share_code = fields.Char("Code de partage", readonly=True, copy=False, index=True)
    access_ids = fields.One2many('gh.document.access', 'document_id', "Ajouté par")
    page_ids = fields.One2many('gh.document.page', 'document_id', "Pages")
    page_count = fields.Integer("Pages", readonly=True)
    chars = fields.Integer("Caractères", readonly=True)
    content_hash = fields.Char("Empreinte du contenu", readonly=True, index=True)
    question_count = fields.Integer("Questions", compute='_compute_question_count')
    active = fields.Boolean(default=True)

    _share_code_uniq = models.Constraint('UNIQUE (share_code)', "Ce code de partage existe déjà.")

    def _compute_question_count(self):
        data = self.env['gh.question'].sudo()._read_group([('document_id', 'in', self.ids)], ['document_id'],
                                                          ['__count'])
        counts = {doc.id: n for doc, n in data}
        for doc in self:
            doc.question_count = counts.get(doc.id, 0)

    # ------------------------------------------------------------------ droits
    def _can_read(self, player):
        """Le joueur peut lire les pages et organiser des matchs dessus."""
        self.ensure_one()
        if not self.active or self.state != 'ready':
            return self.owner_id == player
        return (self.owner_id == player or self.visibility == 'public'
                or (self.visibility == 'shared' and player in self.access_ids.player_id))

    def _check_read(self, player):
        if not self._can_read(player):
            raise AccessError(_("Ce document n'est pas accessible."))

    def _check_owner(self, player):
        if self.owner_id != player:
            raise AccessError(_("Seul le propriétaire du document peut faire cela."))

    @api.model
    def _for_player(self, player, document_id, owner=False):
        doc = self.sudo().browse(int(document_id or 0)).exists()
        if not doc:
            raise AccessError(_("Document introuvable."))
        if owner:
            doc._check_owner(player)
        else:
            doc._check_read(player)
        return doc

    # ------------------------------------------------------------------ données pour l'application
    def _summary(self, player=None):
        self.ensure_one()
        return {
            'id': self.id, 'name': self.name, 'filename': self.filename or '', 'file_kind': self.file_kind or '',
            'unit': self.unit, 'lang': self.lang, 'state': self.state, 'visibility': self.visibility,
            'share_code': self.share_code if (player and self.owner_id == player) else None,
            'owner': self.owner_id.pseudo, 'mine': bool(player and self.owner_id == player),
            'page_count': self.page_count, 'chars': self.chars,
            'created': fields.Datetime.to_string(self.create_date) if self.create_date else None,
        }

    def _detail(self, player):
        """Résumé + liste des pages (sans leur texte) avec l'état de leurs questions."""
        self.ensure_one()
        data = self._summary(player)
        counts = self.page_ids._question_counts()
        data['pages'] = [p._summary(counts.get(p.id)) for p in self.page_ids.sorted('number')]
        return data

    # ------------------------------------------------------------------ envoi
    @api.model
    def begin(self, player, values):
        """Crée le document (état « envoi ») ; les pages suivent par lots."""
        player._ensure_can_play()
        count = self.sudo().search_count([('owner_id', '=', player.id)])
        limit = gh_param(self.env, 'max_documents')
        if count >= limit:
            raise UserError(_("Vous avez déjà %s documents : supprimez-en un pour en charger un autre.", limit))
        name = (values.get('name') or '').strip()[:120]
        if len(name) < 2:
            raise UserError(_("Donnez un titre au document."))
        unit = values.get('unit') if values.get('unit') in dict(UNIT_SELECTION) else 'page'
        lang = values.get('lang') if values.get('lang') in TEXT_LANGUAGES else None
        return self.sudo().create({
            'name': name, 'owner_id': player.id, 'filename': (values.get('filename') or '')[:200] or False,
            'file_kind': (values.get('file_kind') or '').lower()[:10] or False, 'unit': unit,
            'lang': lang or 'fr',
        })

    def add_pages(self, player, pages):
        """Ajoute un lot de pages ``[{'label', 'text'}]`` (à la suite des précédentes)."""
        self.ensure_one()
        self._check_owner(player)
        if self.state != 'uploading':
            raise UserError(_("Ce document est déjà complet."))
        self.env.cr.execute('SELECT id FROM gh_document WHERE id = %s FOR UPDATE', [self.id])
        if not isinstance(pages, list) or not pages:
            raise UserError(_("Aucune page reçue."))
        if len(pages) > MAX_PAGES_PER_CALL:
            raise UserError(_("Trop de pages en un seul envoi (%s au plus).", MAX_PAGES_PER_CALL))
        max_pages = gh_param(self.env, 'max_pages')
        max_page_chars = gh_param(self.env, 'max_page_chars')
        max_doc_chars = gh_param(self.env, 'max_document_chars')
        Page = self.env['gh.document.page'].sudo()
        number = Page.search_count([('document_id', '=', self.id)])
        total = sum(Page.search([('document_id', '=', self.id)]).mapped('chars'))
        received = 0
        vals_list = []
        for item in pages:
            if not isinstance(item, dict):
                raise UserError(_("Page illisible."))
            text = clean_text(str(item.get('text') or ''))
            received += len(text)
            if received > MAX_CHARS_PER_CALL:
                raise UserError(_("Envoi trop volumineux : envoyez moins de pages à la fois."))
            label = ' '.join(str(item.get('label') or '').split())[:80]
            # Une page démesurée (feuille de calcul, texte sans saut de page) est coupée en parties.
            parts = _split_long(text, max_page_chars)
            for k, part in enumerate(parts):
                number += 1
                if number > max_pages:
                    raise UserError(_("Ce document dépasse %s pages : choisissez les pages utiles (ou découpez "
                                      "le fichier).", max_pages))
                total += len(part)
                if total > max_doc_chars:
                    raise UserError(_("Ce document est trop long (%s caractères au plus).", max_doc_chars))
                part_label = label or self._default_label(number)
                if len(parts) > 1:
                    part_label = '%s (%s/%s)' % (part_label, k + 1, len(parts))
                vals_list.append({'document_id': self.id, 'number': number, 'label': part_label, 'text': part,
                                  'chars': len(part)})
        Page.create(vals_list)
        return self

    def _default_label(self, number):
        unit = dict(self._fields['unit']._description_selection(self.env)).get(self.unit) or _("Page")
        return '%s %s' % (unit, number)

    def finish(self, player):
        """Fin de l'envoi : langue (si non donnée), empreinte, document utilisable."""
        self.ensure_one()
        self._check_owner(player)
        if self.state == 'ready':
            return self
        pages = self.page_ids.sorted('number')
        if not pages or not any(p.chars >= 80 for p in pages):
            raise UserError(_("Ce document ne contient pas de texte lisible. Un document scanné doit être "
                              "lisible par la reconnaissance de texte de l'application."))
        text = '\n'.join(p.text or '' for p in pages)
        digest = hashlib.sha256(text.encode()).hexdigest()
        twin = self.sudo().search([('owner_id', '=', player.id), ('content_hash', '=', digest),
                                   ('state', '=', 'ready'), ('id', '!=', self.id)], limit=1)
        if twin:
            raise DuplicateDocument(twin)  # le contrôleur supprime l'envoi en double
        vals = {'state': 'ready', 'page_count': len(pages), 'chars': len(text), 'content_hash': digest}
        if not self.env.context.get('gh_lang_given'):
            vals['lang'] = detect_language(text[:20000])
        self.write(vals)
        return self

    # ------------------------------------------------------------------ partage
    def set_visibility(self, player, visibility):
        self.ensure_one()
        self._check_owner(player)
        if visibility not in ('private', 'shared', 'public'):
            raise UserError(_("Visibilité inconnue."))
        vals = {'visibility': visibility}
        if visibility == 'shared' and not self.share_code:
            vals['share_code'] = self._new_share_code()
        self.write(vals)
        return self

    def _new_share_code(self):
        rnd = random.SystemRandom()
        for _attempt in range(50):
            code = 'D' + ''.join(rnd.choice(SHARE_ALPHABET) for _i in range(7))
            if not self.sudo().with_context(active_test=False).search_count([('share_code', '=', code)]):
                return code
        raise UserError(_("Impossible de créer un code. Réessayez."))

    def renew_share_code(self, player):
        """Nouveau code : l'ancien ne permet plus d'ajouter le document (ceux qui l'ont déjà le gardent)."""
        self.ensure_one()
        self._check_owner(player)
        self.write({'share_code': self._new_share_code(), 'visibility': 'shared'
                    if self.visibility == 'private' else self.visibility})
        return self

    @api.model
    def add_shared(self, player, code):
        code = ''.join(ch for ch in (code or '').upper() if ch.isalnum())
        doc = self.sudo().search([('share_code', '=', code), ('state', '=', 'ready')], limit=1)
        if not doc or doc.visibility == 'private':
            raise UserError(_("Aucun document partagé avec ce code."))
        if doc.owner_id != player and player not in doc.access_ids.player_id:
            self.env['gh.document.access'].sudo().create({'document_id': doc.id, 'player_id': player.id})
        return doc

    def remove_shared(self, player):
        self.ensure_one()
        self.access_ids.filtered(lambda a: a.player_id == player).sudo().unlink()
        return True

    def remove(self, player):
        """Suppression par le propriétaire. Les matchs déjà joués gardent leurs questions."""
        self.ensure_one()
        self._check_owner(player)
        live = self.env['gh.match'].sudo().search_count([
            ('page_ids.document_id', '=', self.id), ('state', 'in', ('lobby', 'running'))])
        live += self.env['gh.tournament'].sudo().search_count([
            ('page_ids.document_id', '=', self.id), ('state', 'in', ('registration', 'running'))])
        if live:
            raise UserError(_("Un match ou un tournoi en cours utilise ce document : attendez qu'il soit terminé."))
        self.sudo().unlink()
        return True

    # ------------------------------------------------------------------ tâches planifiées
    @api.model
    def _cron_gc(self):
        """Envois inachevés depuis plus d'un jour : supprimés."""
        limit = fields.Datetime.now() - timedelta(days=1)
        stale = self.sudo().with_context(active_test=False).search([('state', '=', 'uploading'),
                                                                    ('create_date', '<', limit)])
        stale.unlink()
        return len(stale)


class DuplicateDocument(UserError):
    """Le même texte existe déjà chez ce joueur : l'application ouvre le document existant."""

    def __init__(self, document):
        super().__init__(_("Vous avez déjà chargé ce document (« %s »).", document.name))
        self.code = 'DUPLICATE'
        self.document = document


def _split_long(text, limit):
    """Coupe un texte trop long en parties de ``limit`` caractères au plus, entre deux paragraphes
    (ou deux phrases, ou deux mots) autant que possible."""
    if len(text) <= limit:
        return [text]
    parts = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        cut = max(window.rfind('\n\n'), -1)
        if cut < limit * 0.5:
            cut = max(window.rfind('. '), window.rfind('\n'))
            cut = cut + 1 if cut >= 0 else -1
        if cut < limit * 0.5:
            cut = window.rfind(' ')
        if cut < limit * 0.3:
            cut = limit
        parts.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        parts.append(rest)
    return [p for p in parts if p]


class GhDocumentPage(models.Model):
    _name = 'gh.document.page'
    _description = "Page d'un document"
    _order = 'document_id, number'

    document_id = fields.Many2one('gh.document', required=True, ondelete='cascade', index=True)
    number = fields.Integer("Numéro", required=True)
    label = fields.Char("Libellé", required=True)
    text = fields.Text("Texte")
    chars = fields.Integer("Caractères")
    offline_done = fields.Boolean("Questions sans IA faites", readonly=True)
    ai_state = fields.Selection([
        ('none', "Pas demandé"),
        ('queued', "En file d'attente"),
        ('running', "En cours"),
        ('done', "Fait"),
        ('failed', "Échec"),
    ], "Questions de l'IA", default='none', required=True, readonly=True)
    ai_error = fields.Char("Motif de l'échec", readonly=True)
    question_ids = fields.One2many('gh.question', 'page_id', "Questions")

    _number_uniq = models.Constraint('UNIQUE (document_id, number)', "Numéro de page en double.")

    @api.depends('document_id.name', 'label')
    def _compute_display_name(self):
        for page in self:
            page.display_name = '%s — %s' % (page.document_id.name or '', page.label or '')

    def _question_counts(self):
        """{page_id: {'total', 'validated', 'ai', 'offline', 'manual'}} (questions actives)."""
        out = {}
        if not self.ids:
            return out
        data = self.env['gh.question'].sudo()._read_group(
            [('page_id', 'in', self.ids)], ['page_id', 'source', 'state'], ['__count'])
        for page, source, state, n in data:
            c = out.setdefault(page.id, {'total': 0, 'validated': 0, 'ai': 0, 'offline': 0, 'manual': 0})
            c['total'] += n
            c[source] = c.get(source, 0) + n
            if state == 'validated':
                c['validated'] += n
        return out

    def _summary(self, counts=None):
        """Sans le texte ; le nombre de questions (pas leur contenu) est visible de tous."""
        self.ensure_one()
        return {'id': self.id, 'number': self.number, 'label': self.label, 'chars': self.chars,
                'ai_state': self.ai_state, 'offline_done': self.offline_done,
                'questions': counts or {'total': 0, 'validated': 0, 'ai': 0, 'offline': 0, 'manual': 0}}

    def _ref_label(self):
        """« Titre — Page 3 » (affiché avec chaque question)."""
        self.ensure_one()
        return '%s — %s' % (self.document_id.name, self.label)


class GhDocumentAccess(models.Model):
    _name = 'gh.document.access'
    _description = "Document partagé ajouté par un joueur"
    _order = 'id desc'

    document_id = fields.Many2one('gh.document', required=True, ondelete='cascade', index=True)
    player_id = fields.Many2one('gh.player', required=True, ondelete='cascade', index=True)

    _uniq = models.Constraint('UNIQUE (document_id, player_id)', "Document déjà ajouté.")


def pages_label(pages):
    """Libellé court d'une sélection de pages : « Titre : p. 3–5, 8 ; Autre : diapo 2 »."""
    out = []
    for doc in pages.document_id:
        numbers = sorted(pages.filtered(lambda p, d=doc: p.document_id == d).mapped('number'))
        ranges = []
        start = prev = None
        for n in numbers:
            if start is None:
                start = prev = n
            elif n == prev + 1:
                prev = n
            else:
                ranges.append((start, prev))
                start = prev = n
        if start is not None:
            ranges.append((start, prev))
        text = ', '.join(str(a) if a == b else '%s–%s' % (a, b) for a, b in ranges)
        prefix = {'page': 'p.', 'slide': 'diapo', 'sheet': 'feuille', 'part': 'partie'}.get(doc.unit, 'p.')
        if doc.lang == 'en':
            prefix = {'page': 'p.', 'slide': 'slide', 'sheet': 'sheet', 'part': 'part'}.get(doc.unit, 'p.')
        out.append('%s : %s %s' % (doc.name, prefix, text))
    return ' ; '.join(out)

