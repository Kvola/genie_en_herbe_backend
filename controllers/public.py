# -*- coding: utf-8 -*-
"""Lien d'invitation partagé (WhatsApp, QR code) : /genie-en-herbe/rejoindre/<code>.

Le QR code se lit avec l'appareil photo du téléphone ; le lien ouvre cette
page, qui propose d'ouvrir l'application (Android), de jouer dans le
navigateur, ou d'installer l'application.
"""
import re
from html import escape
from urllib.parse import quote

from odoo.http import Controller, request, route

SLUG = 'genie-en-herbe'
PACKAGE = 'ci.ivorycocoa.genie_en_herbe'


class GeniePublic(Controller):

    @route(['/genie-en-herbe/rejoindre/<string:code>', '/genie-en-herbe/join/<string:code>'],
           type='http', auth='public', methods=['GET'], sitemap=False)
    def join_page(self, code, **kw):
        code = re.sub(r'[^A-Z0-9]', '', (code or '').upper())[:8]
        en = '/join/' in request.httprequest.path  # « rejoindre » contient aussi « join »
        base = request.httprequest.host_url.rstrip('/')
        install = '%s/apps/%s' % (base, SLUG)
        web = '%s/apps/%s/web/?join=%s' % (base, SLUG, code)
        intent = 'intent://join/%s#Intent;scheme=genieenherbe;package=%s;S.browser_fallback_url=%s;end' % (
            code, PACKAGE, quote(install, safe=''))
        is_doc = len(code) == 8  # code de partage d'un document (D + 7 caractères)
        t = {
            'title': ("Add a shared document" if en else "Ajouter un document partagé") if is_doc else
                     ("Join a Génie en herbe game" if en else "Rejoindre une partie de Génie en herbe"),
            'lead': ("Share code:" if en else "Code de partage :") if is_doc else
                    ("Game code:" if en else "Code de la partie :"),
            'app': "Open in the app" if en else "Ouvrir dans l'application",
            'web': "Open in the browser" if en else "Ouvrir dans le navigateur",
            'install': "Install the app (Android)" if en else "Installer l'application (Android)",
            'hint': ("No app? Use the browser, or install it, then type the code under Documents → Add a shared "
                     "document." if en else "Pas encore l'application ? Utilisez le navigateur, ou installez-la "
                     "puis saisissez le code dans Documents → Ajouter un document partagé.") if is_doc else
                    ("No app? Play in the browser, or install it, then type the code under Play → Join."
                     if en else "Pas encore l'application ? Jouez dans le navigateur, ou installez-la puis "
                                "saisissez le code dans Jouer → Rejoindre."),
        }
        html = """<!doctype html><html lang="%(lang)s"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>%(title)s</title>
<style>
body{font-family:system-ui,sans-serif;background:#F4F7F1;color:#1E2A38;margin:0;padding:24px 16px}
main{max-width:460px;margin:auto;text-align:center}
.code{font-size:44px;letter-spacing:6px;font-weight:700;color:#2E6B30;margin:8px 0 24px}
a.b{display:block;padding:14px;border-radius:12px;margin:10px 0;text-decoration:none;font-weight:600}
.p{background:#2E6B30;color:#fff}.s{background:#fff;color:#2E6B30;border:2px solid #2E6B30}
p.h{color:#5b6573;font-size:14px;margin-top:20px}
@media(prefers-color-scheme:dark){body{background:#111A12;color:#E8E3D6}.code{color:#9CD48A}
.s{background:#111A12;color:#9CD48A;border-color:#9CD48A}.p{background:#9CD48A;color:#111A12}p.h{color:#a9b2bf}}
</style></head><body><main>
<h1>%(title)s</h1><div>%(lead)s</div><div class="code">%(code)s</div>
<a class="b p" href="%(intent)s">%(app)s</a>
<a class="b s" href="%(web)s">%(web_label)s</a>
<a class="b s" href="%(install)s">%(install_label)s</a>
<p class="h">%(hint)s</p></main></body></html>""" % {
            'lang': 'en' if en else 'fr', 'title': escape(t['title']), 'lead': escape(t['lead']),
            'code': escape(code), 'intent': escape(intent), 'app': escape(t['app']), 'web': escape(web),
            'web_label': escape(t['web']), 'install': escape(install), 'install_label': escape(t['install']),
            'hint': escape(t['hint']),
        }
        return request.make_response(html, headers=[('Content-Type', 'text/html; charset=utf-8'),
                                                    ('Cache-Control', 'no-store')])
