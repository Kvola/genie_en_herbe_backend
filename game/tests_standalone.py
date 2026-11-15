"""Tests du générateur hors ligne, sans Odoo :  python3 -m game.tests_standalone  (depuis la racine du module)."""
import random
import unittest

from .offline import OfflineError, OfflineGenerator
from .text_tools import clean_text, contains_quote, detect_language, normalize, split_sentences

FR_PAGES = [
    ('p1', """Le fleuve Niger prend sa source en Guinée, dans les monts du Fouta-Djalon. Le fleuve traverse ensuite
le Mali, où il forme un vaste delta intérieur. Ce delta est une zone de pêche et d'élevage très importante.
La ville de Tombouctou se trouve près du fleuve. Au Moyen Âge, Tombouctou était un grand centre de commerce
et de savoir. Les caravanes y apportaient le sel du désert. Les marchands échangeaient le sel contre l'or
et les tissus. La mosquée de Djingareyber fut construite en 1327 par l'architecte Es-Saheli."""),
    ('p2', """Le fleuve entre ensuite au Niger, puis au Nigeria. Il se jette dans l'océan Atlantique par un large
delta. Ce delta abrite de nombreuses mangroves et une grande richesse en pétrole. Le fleuve mesure environ
4 200 kilomètres. Il est le troisième fleuve d'Afrique par sa longueur, après le Nil et le Congo.
Les barrages de Kainji et de Jebba produisent de l'électricité pour le Nigeria. Les pêcheurs bozo vivent
le long du fleuve depuis des siècles. La crue annuelle fertilise les champs de riz."""),
    ('p3', """L'empire du Mali fut fondé par Soundiata Keïta vers 1235. Son petit-neveu Kankou Moussa fit un
célèbre pèlerinage à La Mecque en 1324. La caravane de Kankou Moussa transportait tant d'or que le prix du
métal baissa au Caire. Le souverain rapporta des savants et des architectes. L'empire contrôlait les mines
d'or du Bambouk. Les griots conservent la mémoire de cette histoire. Ils racontent l'épopée de Soundiata
dans les villages. La charte du Manden proclamait le respect de la vie humaine."""),
]

EN_PAGES = [
    ('a', """The Nile is the longest river in Africa. The river flows north through eleven countries before it
reaches the Mediterranean Sea. The Aswan High Dam was completed in 1970 in Egypt. The dam controls the annual
flood and produces electricity. Before the dam, the flood brought fertile mud to the farms of the valley.
The ancient Egyptians built their temples and their cities along the banks of the river. The city of Cairo
is the largest city on the river. Farmers grow wheat, cotton and rice in the delta."""),
]


class TextTools(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize("L'Éléphant  d’Afrique !"), 'l elephant d afrique')

    def test_sentences(self):
        s = split_sentences("M. Kouassi est venu. Il a vu 3.5 km de route. Fin")
        self.assertEqual([x.text for x in s], ['M. Kouassi est venu.', 'Il a vu 3.5 km de route.', 'Fin'])

    def test_clean(self):
        self.assertEqual(clean_text("la compré-\nhension du\ntexte\n\n\n\nSuite"), 'la compréhension du texte\n\nSuite')

    def test_language(self):
        self.assertEqual(detect_language(FR_PAGES[0][1]), 'fr')
        self.assertEqual(detect_language(EN_PAGES[0][1]), 'en')

    def test_quote(self):
        self.assertTrue(contains_quote("Le fleuve Niger prend sa source.", "fleuve niger PREND"))
        self.assertFalse(contains_quote("Le fleuve Niger prend sa source.", "fleuve Congo"))


class Generator(unittest.TestCase):
    def _check(self, pages, lang):
        gen = OfflineGenerator(pages, lang, random.Random(7))
        total = 0
        for key, text in pages:
            qs = gen.generate_page(key, 8)
            self.assertGreaterEqual(len(qs), 3, key)
            for q in qs:
                total += 1
                self.assertEqual(q['page'], key)
                self.assertGreaterEqual(len(q['options']), 3)
                self.assertLessEqual(len(q['options']), 4)
                self.assertEqual(len({normalize(o) for o in q['options']}), len(q['options']))
                self.assertTrue(0 <= q['answer'] < len(q['options']))
                # La citation est dans la page de la question (juste par construction).
                self.assertTrue(contains_quote(text, q['quote']), q)
                # Pour un texte à trous, la bonne réponse remise à sa place redonne la phrase.
                if '_____' in q['prompt']:
                    blanked = q['prompt'].split('\n', 1)[1].strip('«»“” ')
                    filled = blanked.replace('_____', q['options'][q['answer']], 1)
                    self.assertEqual(normalize(filled), normalize(q['quote']), q)
        return total

    def test_french(self):
        self.assertGreater(self._check(FR_PAGES, 'fr'), 10)

    def test_english(self):
        self.assertGreaterEqual(self._check(EN_PAGES, 'en'), 3)

    def test_language_refused(self):
        with self.assertRaises(OfflineError):
            OfflineGenerator(FR_PAGES, 'es')

    def test_empty_page(self):
        gen = OfflineGenerator(FR_PAGES + [('vide', 'Sommaire')], 'fr')
        self.assertEqual(gen.generate_page('vide', 5), [])

    def test_deterministic(self):
        a = OfflineGenerator(FR_PAGES, 'fr').generate_page('p1', 5)
        b = OfflineGenerator(FR_PAGES, 'fr').generate_page('p1', 5)
        self.assertEqual(a, b)


if __name__ == '__main__':
    unittest.main()
