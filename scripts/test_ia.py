# -*- coding: utf-8 -*-
"""Essai réel de l'IA (Claude) sur le serveur, SANS RIEN ENREGISTRER.

À lancer dans le shell Odoo du serveur, par exemple :
    docker exec -i odoo19-web-prod-iyf odoo shell -d iyf.ebng.world --no-http \
        < addons/genie_en_herbe_backend/scripts/test_ia.py

Crée un joueur et un document de 2 pages dans la transaction, appelle Claude
une fois (un lot, quelques centimes), vérifie les questions, affiche le
résultat, puis ANNULE tout (rollback) : la base reste inchangée.
"""
PAGES = [
    "L'empire du Mali fut fondé par Soundiata Keïta vers 1235, après la bataille de Kirina. Soundiata vainquit "
    "Soumaoro Kanté, le roi du Sosso. La charte du Manden, proclamée à Kouroukan Fouga, organisait la société en "
    "clans et proclamait le respect de la vie humaine. La capitale de l'empire était Niani.",
    "Kankou Moussa régna sur le Mali de 1312 à 1337. En 1324, il fit un célèbre pèlerinage à La Mecque. Il "
    "transportait tant d'or que le prix du métal baissa au Caire pendant plusieurs années. À son retour, "
    "l'architecte Es-Saheli construisit la mosquée de Djingareyber à Tombouctou.",
]
try:
    R = env['gh.ai.request'].sudo()
    print('IA activée :', R._enabled(), '| clé présente :', bool(R._api_key()),
          '| modèle :', R._param('ai_model', '') or 'claude-opus-5-5 (défaut)')
    partner = env['res.partner'].sudo().create({'name': 'Essai IA'})
    player = env['gh.player'].sudo().create({'partner_id': partner.id, 'pseudo': 'EssaiIA%s' % partner.id})
    doc = env['gh.document'].sudo().begin(player, {'name': 'Essai IA — Mali'})
    doc.add_pages(player, [{'label': '', 'text': t} for t in PAGES])
    doc.finish(player)
    pages = doc.page_ids.sorted('number')
    data, tokens_in, tokens_out, model = R._call_claude(pages, 'fr')
    kept, dropped = R._validate(data, pages)
    from odoo.addons.genie_en_herbe_backend.models.gh_question import price_of
    price = price_of(model) or (0, 0)
    print('Modèle utilisé :', model, '| jetons :', tokens_in, '+', tokens_out,
          '| coût ≈ %.4f $' % ((tokens_in * price[0] + tokens_out * price[1]) / 1e6))
    print('Questions gardées :', len(kept), '| écartées :', dropped)
    for q in kept:
        print('-', q['prompt'], '→', q['options'][q['answer']])
finally:
    env.cr.rollback()
    print('Annulé : rien n’a été enregistré.')
