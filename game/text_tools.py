"""Outils de texte : normalisation, phrases, mots, langue.

Portage de comprendre/lib/engine/text_tools.dart (application Comprendre), en
Python pur (bibliothèque standard seulement, aucun import d'Odoo) : les mêmes
règles des deux côtés, pour que la citation d'une question se retrouve dans le
texte affiché par l'application.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_FOLD = {
    'à': 'a', 'á': 'a', 'â': 'a', 'ä': 'a', 'ã': 'a', 'å': 'a', 'ç': 'c', 'è': 'e', 'é': 'e', 'ê': 'e', 'ë': 'e',
    'ì': 'i', 'í': 'i', 'î': 'i', 'ï': 'i', 'ñ': 'n', 'ò': 'o', 'ó': 'o', 'ô': 'o', 'ö': 'o', 'õ': 'o',
    'ù': 'u', 'ú': 'u', 'û': 'u', 'ü': 'u', 'ý': 'y', 'ÿ': 'y', 'œ': 'oe', 'æ': 'ae', 'ß': 'ss',
}


def _word_char(f):
    return len(f) > 1 or ('a' <= f <= 'z') or ('0' <= f <= '9')


def normalize(s):
    """Forme de comparaison : minuscules, sans accents ni ponctuation, espaces simples."""
    out = []
    for ch in (s or '').lower():
        f = _FOLD.get(ch, ch)
        out.append(f if _word_char(f) else ' ')
    return re.sub(r'\s+', ' ', ''.join(out)).strip()


def clean_text(raw):
    """Nettoie un texte extrait (PDF, OCR…) : césures de fin de ligne, espaces, lignes vides."""
    t = (raw or '').replace('\r\n', '\n').replace('\r', '\n').replace(' ', ' ').replace('­', '')
    t = t.replace('\x00', '')
    # Césure : « compré-\nhension » → « compréhension » (seulement devant une minuscule).
    t = re.sub(r'([^\W\d_])-\n([^\W\d_])',
               lambda m: m.group(1) + m.group(2) if m.group(2).islower() else m.group(0), t)
    t = re.sub(r'[ \t]+', ' ', t)
    # Une ligne coupée au milieu d'une phrase est recollée ; un vrai paragraphe (ligne vide) reste.
    t = re.sub(r'([^\n.!?:;»"”])\n(?!\n)([^\W\d_])',
               lambda m: m.group(1) + ' ' + m.group(2) if m.group(2).islower() else m.group(0), t)
    t = re.sub(r'\n{3,}', '\n\n', t)
    return '\n'.join(line.strip() for line in t.split('\n')).strip()


_ABBREV = frozenset({
    'm', 'mm', 'mme', 'mlle', 'dr', 'pr', 'st', 'ste', 'etc', 'cf', 'ex', 'p', 'pp', 'vol', 'chap', 'fig', 'n', 'no',
    'mr', 'mrs', 'ms', 'prof', 'jr', 'sr', 'vs', 'inc', 'ltd', 'av', 'apr', 'env', 'janv', 'févr', 'sept', 'oct',
    'nov', 'déc',
})

_WORD_RE = re.compile(r'[^\W_]+(?:-[^\W_]+)*')
_LETTER_RE = re.compile(r'[^\W\d_]')
_LAST_WORD_DOT_RE = re.compile(r'([^\W\d_]+)\.$')


def words_of(s):
    """Mots d'une phrase (lettres, chiffres, traits d'union internes), apostrophes séparées."""
    return _WORD_RE.findall(s or '')


@dataclass(frozen=True)
class Sentence:
    text: str
    start: int
    index: int

    @property
    def words(self):
        return words_of(self.text)


def _starts_sentence(ch):
    return ch.isupper() or ch.isdigit() or ch in '«"“(—–-'


def split_sentences(text):
    """Phrases d'un texte, avec leur position de début."""
    out = []
    start = 0
    n = len(text)

    def push(end):
        nonlocal start
        raw = text[start:end]
        left = len(raw) - len(raw.lstrip())
        s = raw.strip()
        if s and _LETTER_RE.search(s):
            out.append(Sentence(s, start + left, len(out)))
        start = end

    i = 0
    while i < n:
        ch = text[i]
        if ch == '\n' and i + 1 < n and text[i + 1] == '\n':
            push(i)
            i += 1
            continue
        if ch not in '.!?…':
            i += 1
            continue
        j = i + 1
        while j < n and text[j] in '.!?…»"”’)':
            j += 1
        if j < n and text[j] not in ' \n':
            i += 1
            continue
        if ch == '.':
            m = _LAST_WORD_DOT_RE.search(text[start:i + 1])
            if m:
                w = m.group(1)
                if w.lower() in _ABBREV or (len(w) == 1 and w.isupper()):
                    i += 1
                    continue
        k = j
        while k < n and text[k] in ' \n':
            k += 1
        if k < n and not _starts_sentence(text[k]):
            i += 1
            continue
        push(j)
        i = j
    push(n)
    return out


STOP_FR = frozenset("""
le la les un une des du de d l au aux et ou mais donc or ni car que qui quoi dont où ce cet cette ces se sa son ses
leur leurs mon ma mes ton ta tes notre nos votre vos il elle ils elles on nous vous je tu me te lui y en ne pas plus
moins très trop peu est sont était étaient être été a ont avait avaient avoir eu fait faire peut peuvent pour par avec
sans sur sous dans entre vers chez depuis pendant avant après comme si aussi ainsi alors tout tous toute toutes autre
autres même mêmes bien encore déjà toujours jamais cela ça ceci celui celle ceux celles quand lorsque puis enfin c s n
j m t qu sera seront serait fut avons avez suis es sommes êtes chaque plusieurs certains
""".split())

STOP_EN = frozenset("""
the a an and or but so nor for yet of to in on at by with from as is are was were be been being am has have had do
does did will would can could should may might must shall it its this that these those he she they we you i him her
them us me his their our your my not no more most less very too also than then there here when where which who whom
whose what why how if into over under about after before between during since until while all any each some such
other only own same just s t up out many much one like even still
""".split())

TEXT_LANGUAGES = ('fr', 'en', 'es', 'pt', 'de', 'it')
OFFLINE_LANGUAGES = frozenset({'fr', 'en'})

_STOP_OTHER = {
    'es': frozenset("""el la los las de del que y en un una por para con no su sus al lo es se como más pero este esta
porque muy sin sobre también son está fue ha han entre cuando hay desde todo ya ser era eso hasta""".split()),
    'pt': frozenset("""o os a as de do da dos das que e em no na nos nas um uma com não mais ao pelo pela é foi são
está mas como também isso seu sua já muito quando entre para por se ser tem até ou pelos pelas""".split()),
    'de': frozenset("""der die das und ist nicht ein eine zu den von mit sich des auf für im dem auch es an werden aus
er hat dass sie nach wird bei einer um noch wie einem über so zum war haben nur oder aber vor zur bis durch""".split()),
    'it': frozenset("""il di che e la per un una del della non sono con gli le si da nel nella è al alla anche come più
ma dei delle questo questa ha hanno essere molto tra quando ancora lo degli nei sul sulla era perché loro""".split()),
}


def detect_language(text):
    """Langue probable parmi TEXT_LANGUAGES (le français l'emporte en cas de doute)."""
    scores = {lang: 0 for lang in TEXT_LANGUAGES}
    for w in words_of((text or '').lower())[:3000]:
        if w in STOP_FR:
            scores['fr'] += 1
        if w in STOP_EN:
            scores['en'] += 1
        for lang, words in _STOP_OTHER.items():
            if w in words:
                scores[lang] += 1
    best = 'fr'
    for lang in TEXT_LANGUAGES:
        threshold = 0 if lang == 'fr' else scores['fr'] * 1.2
        if scores[lang] > threshold and scores[lang] > scores[best] * (1.2 if best == 'fr' else 1):
            best = lang
    return best


def stable_seed(s):
    """Graine stable tirée du texte (FNV-1a), identique à celle de l'application."""
    h = 0x811c9dc5
    # Unités UTF-16, comme String.codeUnits en Dart.
    data = (s or '').encode('utf-16-le')
    for i in range(0, len(data), 2):
        h ^= data[i] | (data[i + 1] << 8)
        h = (h * 0x01000193) & 0x7fffffff
    return h


def contains_quote(text, quote):
    """La citation figure-t-elle dans le texte (casse, accents et ponctuation ignorés) ?"""
    q = normalize(quote)
    return bool(q) and f' {q} ' in f' {normalize(text)} '
