"""Générateur de questions HORS LIGNE (sans IA) : tout est tiré du texte lui-même,
donc juste par construction.

Portage de comprendre/lib/engine/offline_quiz.dart, adapté au jeu :

* **uniquement des questions à choix multiples** (un vrai/faux se joue à pile
  ou face quand on a la main) ;
* le texte est découpé en **pages** : chaque question appartient à une page
  (sa citation en est tirée), mais les mauvaises propositions (distracteurs)
  viennent de tout le document, pour être plausibles même sur une page courte.

Types de questions :

- **thème** : de quoi parle principalement la page (nom dominant) ;
- **texte à trous** : un nom ou un nom propre retiré d'une phrase ;
- **chiffres et dates** : un nombre retiré, avec des nombres voisins ;
- **« selon le texte »** : laquelle de ces affirmations figure dans le texte
  (les autres ont un nom échangé) ;
- **ordre** : laquelle de ces idées vient en premier dans la page.

Pour rester grammatical sans analyseur, on n'échange que des **noms** (mot
précédé d'un déterminant), de même nombre, de même genre et commençant par
une voyelle ou une consonne comme l'original, ou des noms propres entre eux.

Français et anglais seulement (OFFLINE_LANGUAGES).
"""
from __future__ import annotations

import random
import re

from .text_tools import (OFFLINE_LANGUAGES, STOP_EN, STOP_FR, normalize, split_sentences, stable_seed, words_of)

_DET_FR = {
    'le': 'm', 'un': 'm', 'ce': 'm', 'cet': 'm', 'du': 'm', 'au': 'm', 'son': None, 'mon': None, 'ton': None,
    'la': 'f', 'une': 'f', 'cette': 'f', 'sa': 'f', 'ma': 'f', 'ta': 'f',
    'l': None, 'les': None, 'des': None, 'aux': None, 'ces': None, 'ses': None, 'leur': None, 'leurs': None,
    'mes': None, 'tes': None, 'notre': None, 'nos': None, 'votre': None, 'vos': None, 'chaque': None,
    'plusieurs': None, 'quelques': None, 'certains': None, 'certaines': None,
}
_DET_EN = frozenset({
    'the', 'a', 'an', 'this', 'these', 'those', 'his', 'her', 'its', 'their', 'our', 'your', 'my', 'each',
    'every', 'some', 'many', 'several', 'few',
})
_PRE_ADJ_FR = frozenset("""
grand grande grands grandes petit petite petits petites long longue longs longues jeune jeunes vieux vieil vieille
vieilles beau bel belle beaux belles bon bonne bons bonnes mauvais mauvaise mauvaises nouveau nouvel nouvelle nouveaux
nouvelles premier première premiers premières dernier dernière derniers dernières gros grosse grosses haut haute hauts
hautes seul seule seuls seules meilleur meilleure meilleurs meilleures second seconde deuxième troisième principal
principale principaux principales ancien ancienne anciens anciennes vrai vraie vrais vraies faux fausse certain
certaine différents différentes nombreux nombreuses simple simples immense immenses vaste vastes large larges
célèbre célèbres important importante importants importantes fameux fameuse fameuses véritable véritables unique
uniques énorme énormes joli jolie jolis jolies riche riches pauvre pauvres fort forte forts fortes double doubles
moindre moindres prochain prochaine prochains prochaines précieux précieuse précieuses superbe superbes
""".split())
_DET_BEFORE_VOWEL_FR = frozenset({'l', 'cet'})
_DET_BEFORE_CONSONANT_FR = frozenset({'le', 'la', 'ce', 'du', 'au', 'ma', 'ta', 'sa'})
_BEFORE_OBJECT_PRONOUN_FR = frozenset('qui ne n je j tu il elle on nous vous ils elles se s me m te t'.split())
_OBJECT_PRONOUNS_FR = frozenset({'le', 'la', 'les', 'l', 'leur'})
_VOWEL_START = re.compile(r'^[aeiouyhâàäéèêëîïôöûùüœæ]', re.IGNORECASE)
_NUMBER_WORD = re.compile(r'^\d[\d.,]*$')
_EN_ADJ_SUFFIX = re.compile(r'(ed|ing|ly|s)$')
_TOKEN_RE = re.compile(r'[^\W_]+')
_WORD_FIND_RE = re.compile(r'[^\W_]+(?:-[^\W_]+)*')

BLANK = '_____'


class OfflineError(Exception):
    """``too_short`` (pas assez de matière) ou ``language`` (langue non traitée)."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class _Term:
    __slots__ = ('key', 'kind', 'forms', 'sentences', 'pages', 'genders', 'count', 'articled', 'score')

    def __init__(self, key, kind):
        self.key = key
        self.kind = kind  # noun | proper | number
        self.forms = {}
        self.sentences = set()
        self.pages = set()
        self.genders = []
        self.count = 0
        self.articled = 0
        self.score = 0.0

    @property
    def surface(self):
        return sorted(self.forms.items(), key=lambda e: -e[1])[0][0]

    @property
    def gender(self):
        return self.genders[0] if len(self.genders) == 1 else None


class _Slot:
    __slots__ = ('surface', 'term', 'det')

    def __init__(self, surface, term, det):
        self.surface = surface
        self.term = term
        self.det = det


class _Num:
    __slots__ = ('raw', 'value', 'group', 'decimal_sep', 'decimals')

    def __init__(self, raw, value, group, decimal_sep, decimals):
        self.raw = raw
        self.value = value
        self.group = group
        self.decimal_sep = decimal_sep
        self.decimals = decimals

    def format(self, v):
        fixed = f'{v:.{self.decimals}f}'
        whole, _, dec = fixed.partition('.')
        if self.group is not None:
            out = []
            for i, ch in enumerate(whole):
                if i > 0 and (len(whole) - i) % 3 == 0:
                    out.append(self.group)
                out.append(ch)
            whole = ''.join(out)
        return f'{whole}{self.decimal_sep}{dec}' if self.decimals > 0 else whole


class _Sent:
    """Une phrase du document : son texte (espaces simplifiés), son rang global et sa page."""
    __slots__ = ('text', 'index', 'page', 'pos', 'tokens')

    def __init__(self, text, index, page, pos):
        self.text = ' '.join(text.split())
        self.index = index
        self.page = page
        self.pos = pos  # rang dans la page
        self.tokens = list(_WORD_FIND_RE.finditer(self.text))

    @property
    def words(self):
        return [m.group() for m in self.tokens]

    def joined(self, i, j):
        """Les mots i et j (= i + 1) ne sont-ils séparés que par une espace ? (pas une virgule)"""
        return self.text[self.tokens[i].end():self.tokens[j].start()] == ' '


def _word_re(surface):
    parts = [re.escape(p) for p in surface.split(' ')]
    return re.compile(r'(?<![^\W_])' + r'\s+'.join(parts) + r'(?![^\W_])')


class OfflineGenerator:
    """Analyse un document (liste de pages), puis fabrique des questions page par page.

    ``pages`` : [(clé, texte)] dans l'ordre du document. ``rnd`` : random.Random
    (graine stable du document par défaut : mêmes pages → mêmes questions).
    """

    def __init__(self, pages, lang, rnd=None):
        if lang not in OFFLINE_LANGUAGES:
            raise OfflineError('language')
        self.lang = lang
        self.fr = lang != 'en'
        self.stop = STOP_FR if self.fr else STOP_EN
        self.pages = [(key, text or '') for key, text in pages]
        self.rnd = rnd or random.Random(stable_seed(''.join(t for _k, t in self.pages)[:20000]))
        self.sentences = []
        self.by_page = {}
        for key, text in self.pages:
            for s in split_sentences(text):
                sent = _Sent(s.text, len(self.sentences), key, s.index)
                self.sentences.append(sent)
                self.by_page.setdefault(key, []).append(sent)
        self.norm_text = ' %s ' % normalize(' '.join(t for _k, t in self.pages))
        self.terms = {}
        self.slots = {}
        self.used_answers = {}
        self._analyse()
        self.ranked = sorted(self.terms.values(), key=lambda t: -t.score)

    # ------------------------------------------------------------------ analyse
    def _plural(self, w):
        lw = w.lower()
        if self.fr:
            return (lw.endswith('s') or lw.endswith('x')) and len(lw) > 3
        return lw.endswith('s') and not lw.endswith('ss') and len(lw) > 3

    def _stem(self, w):
        k = normalize(w)
        if len(k) > 4 and self._plural(w):
            return k[:-1]
        return k

    def _is_det(self, w):
        return w.lower() in (_DET_FR if self.fr else _DET_EN)

    def _object_pronoun(self, words, d):
        return (self.fr and d > 0 and words[d].lower() in _OBJECT_PRONOUNS_FR
                and words[d - 1].lower() in _BEFORE_OBJECT_PRONOUN_FR)

    def _adjective_before(self, words, i):
        lower = words[i].lower()
        if self.fr:
            return lower in _PRE_ADJ_FR
        if i + 1 >= len(words):
            return False
        nxt = words[i + 1]
        return (nxt[:1].islower() and len(nxt) >= 3 and nxt not in STOP_EN
                and not _EN_ADJ_SUFFIX.search(nxt)) or lower.endswith('est')

    def _det_before(self, words, i):
        if i > 0 and self._is_det(words[i - 1]) and not self._object_pronoun(words, i - 1):
            return words[i - 1].lower()
        if i > 1 and self._is_det(words[i - 2]) and not self._object_pronoun(words, i - 2) and \
                self._adjective_before(words, i - 1):
            return words[i - 2].lower()
        return None

    def _analyse(self):
        for s in self.sentences:
            words = s.words
            slots = self.slots[s.index] = []
            i = 0
            while i < len(words):
                w = words[i]
                lower = w.lower()
                term = None
                surface = w
                det = None
                if _NUMBER_WORD.match(w):
                    term = self.terms.setdefault('#' + w, _Term('#' + w, 'number'))
                elif i > 0 and w[:1].isupper() and lower not in self.stop and len(w) > 1:
                    # Nom propre, éventuellement sur plusieurs mots (« Burkina Faso »).
                    j = i + 1
                    while j < len(words) and words[j][:1].isupper() and words[j].lower() not in self.stop \
                            and s.joined(j - 1, j):
                        j += 1
                    surface = ' '.join(words[i:j])
                    key = 'P' + normalize(surface)
                    term = self.terms.setdefault(key, _Term(key, 'proper'))
                    if self._is_det(words[i - 1]):
                        det = words[i - 1].lower()
                        term.articled += 1
                    i = j - 1
                elif len(w) >= 4 and lower not in self.stop and not self._adjective_before(words, i):
                    det = self._det_before(words, i)
                    if det is not None:
                        key = 'N' + self._stem(w)
                        term = self.terms.setdefault(key, _Term(key, 'noun'))
                        g = _DET_FR.get(det) if self.fr else None
                        if g is not None and g not in term.genders:
                            term.genders.append(g)
                if term is not None:
                    term.count += 1
                    term.sentences.add(s.index)
                    term.pages.add(s.page)
                    term.forms[surface] = term.forms.get(surface, 0) + 1
                    if term.kind != 'number':
                        slots.append(_Slot(surface, term, det))
                i += 1
        # Un nom commun compte aussi quand il apparaît sans déterminant.
        counts = {}
        for _key, text in self.pages:
            for w in words_of(text):
                k = 'N' + self._stem(w)
                counts[k] = counts.get(k, 0) + 1
        total = max(1, len(self.sentences))
        for t in self.terms.values():
            if t.kind == 'noun':
                t.count = max(t.count, counts.get(t.key, 0))
            boost = {'proper': 1.4, 'number': 0.8}.get(t.kind, 1.0)
            spread = len(t.sentences) / total
            t.score = t.count * boost * (1 + 2 * spread) + min(len(t.key), 12) / 24

    # ------------------------------------------------------------------ génération
    @staticmethod
    def _sentence_ok(s):
        n = len(s.words)
        return 6 <= n <= 45

    @staticmethod
    def _spread(items):
        out = []
        lo, hi = 0, len(items) - 1
        while lo <= hi:
            out.append(items[lo])
            lo += 1
            if lo <= hi:
                out.append(items[hi])
                hi -= 1
        return out

    def page_capacity(self, key):
        """Phrases utilisables d'une page (une indication de ce qu'elle peut donner)."""
        return len([s for s in self.by_page.get(key, []) if self._sentence_ok(s)])

    def generate_page(self, key, count, avoid_quotes=frozenset()):
        """Au plus ``count`` questions sur la page ``key`` (moins si la page est pauvre).

        ``avoid_quotes`` : citations normalisées déjà interrogées (leurs phrases en dernier recours).
        """
        page_sents = self.by_page.get(key, [])
        usable = [s for s in page_sents if self._sentence_ok(s)]
        swappable = len([t for t in self.terms.values() if t.kind != 'number'])
        if not usable or swappable < 5:
            return []
        questions = []
        used_sentences = set()

        def add(q, sentence=None):
            if q is None or len(questions) >= count:
                return False
            if any(x['prompt'] == q['prompt'] or (x['quote'] == q['quote'] and x['skill'] == q['skill'])
                   for x in questions):
                return False
            q['page'] = key
            questions.append(q)
            if sentence is not None:
                used_sentences.add(sentence)
            return True

        def fresh(s):
            return normalize(s.text) not in avoid_quotes

        topic = self._main_topic(key, page_sents)
        if topic is not None and normalize(topic['quote']) not in avoid_quotes:
            add(topic)
        ordered = self._spread([s for s in usable if fresh(s)]) + self._spread([s for s in usable if not fresh(s)])
        plan = ['cloze', 'statement', 'number', 'cloze', 'order', 'cloze', 'statement', 'number', 'cloze']
        p = cursor = guard = 0
        while len(questions) < count and guard < count * 15:
            guard += 1
            kind = plan[p % len(plan)]
            p += 1
            pool = [s for s in ordered if s.index not in used_sentences]
            if not pool:
                break  # chaque question a sa phrase : une page courte donne peu de questions
            s = pool[cursor % len(pool)]
            cursor += 1
            if kind == 'cloze':
                add(self._cloze(s), s.index)
            elif kind == 'number':
                found = self._number(pool)
                if found:
                    add(found[0], found[1])
            elif kind == 'statement':
                add(self._statement(s), s.index)
            elif kind == 'order':
                add(self._order(usable))
        return questions

    # ------------------------------------------------------------------ distracteurs
    def _replacements(self, slot, exclude, n=3):
        ex = normalize(exclude)
        term = slot.term
        want_plural = self._plural(slot.surface) if term.kind == 'noun' else None
        slot_gender = _DET_FR.get(slot.det) if (self.fr and term.kind == 'noun' and slot.det) else None
        elision = self._elision(slot.surface, exclude) if self.fr else None
        scored = []
        for t in self.ranked:
            if t.key == term.key or t.kind != term.kind:
                continue
            if t.kind == 'noun':
                if slot_gender is not None and t.gender != slot_gender:
                    continue
                forms = [f for f in t.forms if self._plural(f) == want_plural]
                if not forms:
                    continue
                form = forms[0]
                if self.fr and slot.det:
                    vowel = bool(_VOWEL_START.match(form))
                    if slot.det in _DET_BEFORE_VOWEL_FR and not vowel:
                        continue
                    if slot.det in _DET_BEFORE_CONSONANT_FR and vowel and not form.lower().startswith('h'):
                        continue
                if not self.fr and slot.det in ('a', 'an'):
                    vowel = form[:1].lower() in 'aeiou'
                    if vowel != (slot.det == 'an'):
                        continue
                if slot.surface[:1].isupper() and not form[:1].isupper():
                    form = form[:1].upper() + form[1:]
            else:
                if t.kind == 'proper' and (slot.det is not None) != (t.articled * 2 > t.count):
                    continue
                form = t.surface
            if _word_re(normalize(form)).search(ex):
                continue
            if self.fr and elision is not None and bool(_VOWEL_START.match(form)) != elision:
                continue
            scored.append((form, t.score))
        scored.sort(key=lambda e: -e[1])
        top = [f for f, _s in scored[:8]]
        self.rnd.shuffle(top)
        seen = {normalize(slot.surface)}
        out = []
        for f in top:
            k = normalize(f)
            if k not in seen:
                seen.add(k)
                out.append(f)
        return out[:n]

    @staticmethod
    def _elision(surface, sentence):
        """Contrainte d'élision du mot à remplacer : True = il faut une voyelle (« d'Afrique »),
        False = une consonne (« de Guinée », « le fleuve »), None = libre."""
        m = _word_re(surface).search(sentence)
        if not m:
            return None
        before = sentence[:m.start()]
        if re.search(r"(?:^|[^\w])(?:[ldjmntsc]|qu|jusqu|lorsqu|puisqu)['’]\s*$", before, re.IGNORECASE):
            return True
        if re.search(r"(?:^|[^\w])(?:de|le|la|que|ce|je|me|te|se|ne)\s+$", before, re.IGNORECASE):
            return False
        return None

    def _slots_of(self, s):
        items = list(self.slots.get(s.index, []))
        items.sort(key=lambda sl: (self.used_answers.get(sl.term.key, 0), -sl.term.score))
        return items

    def _mark_used(self, term):
        self.used_answers[term.key] = self.used_answers.get(term.key, 0) + 1

    # ------------------------------------------------------------------ types de questions
    def _quote_says(self, text):
        return f'Le texte dit : « {text} »' if self.fr else f'The text says: “{text}”'

    def _main_topic(self, key, page_sents):
        """Thème de la page : le nom le plus présent DANS la page, s'il domine nettement."""
        if len(page_sents) < 4:
            return None
        idx = {s.index for s in page_sents}
        local = []
        for t in self.ranked:
            if t.kind == 'number':
                continue
            inside = t.sentences & idx
            if inside:
                local.append((t, len(inside)))
        if len(local) < 6:
            return None
        local.sort(key=lambda e: (-e[1], -e[0].score))
        top, top_n = local[0]
        if top_n < 3 or (len(local) > 1 and local[1][1] * 1.5 > top_n):
            return None
        first = min(top.sentences & idx)
        rare = [t for t, n in local[1:] if n == 1 and first not in t.sentences and len(t.sentences) <= 2]
        self.rnd.shuffle(rare)
        if len(rare) < 3:
            return None

        def cap(s):
            return s[:1].upper() + s[1:]
        answer = cap(top.surface)
        quote = self.sentences[first].text
        if self.fr:
            prompt = 'De quoi cette page parle-t-elle principalement ?'
            explain = (f'« {top.surface} » revient {top_n} fois dans la page, du début à la fin : '
                       f'c’est le sujet central. Les autres ne sont que des détails.')
        else:
            prompt = 'What is this page mainly about?'
            explain = (f'“{top.surface}” appears {top_n} times on the page, from start to finish: '
                       f'it is the central subject. The others are details.')
        return self._shuffled(prompt, answer, [answer] + [cap(t.surface) for t in rare[:3]], explain, quote,
                              'main_idea')

    def _cloze(self, s):
        for slot in self._slots_of(s):
            if self.used_answers.get(slot.term.key, 0) >= 2:
                continue
            options = self._replacements(slot, s.text)
            if len(options) < 3:
                continue
            blanked, n = _word_re(slot.surface).subn(BLANK, s.text, count=1)
            if n == 0 or blanked == s.text:
                continue
            self._mark_used(slot.term)
            prompt = (f'Complétez selon le texte :\n« {blanked} »' if self.fr
                      else f'Complete according to the text:\n“{blanked}”')
            return self._shuffled(prompt, slot.surface, [slot.surface] + options, self._quote_says(s.text), s.text,
                                  'detail' if slot.term.kind == 'proper' else 'vocabulary')
        return None

    def _numbers_in(self, text):
        group = r'[   ]' if self.fr else r'[,   ]'
        decimal = r'[.,]' if self.fr else r'\.'
        pattern = re.compile(r'(?<![^\W_.,])(\d{1,3}(?:%s\d{3})+|\d{1,4})(?:(%s)(\d+))?(?!\d|[.,]\d)' % (group, decimal))
        out = []
        for m in pattern.finditer(text):
            whole = m.group(1)
            sep_m = re.search(r'\D', whole)
            digits = re.sub(r'\D', '', whole)
            dec = m.group(3) or ''
            try:
                value = float(digits if not dec else f'{digits}.{dec}')
            except ValueError:
                continue
            out.append((m.start(), m.end(), _Num(m.group(0), value, sep_m.group(0) if sep_m else None,
                                                  m.group(2) or (',' if self.fr else '.'), len(dec))))
        return out

    def _number(self, candidates):
        for s in candidates:
            if len(s.words) < 6:
                continue
            for start, end, num in self._numbers_in(s.text):
                value = num.value
                if value == 0:
                    continue
                is_year = num.group is None and num.decimals == 0 and 1000 <= value <= 2100 and len(num.raw) == 4
                if is_year:
                    alts = [value - 10, value + 10, value - 50, value + 25, value - 5]
                elif value < 10 and num.decimals == 0:
                    alts = [value + 1, value + 2, value - 1, value * 3, value + 5]
                else:
                    alts = [value * 2, value / 2, value * 1.5, value * 10, value + 10]
                options = [num.raw]
                for v in alts:
                    if len(options) == 4:
                        break
                    if v <= 0 or v == value:
                        continue
                    t = num.format(round(v) if num.decimals == 0 else v)
                    if t not in options and f' {normalize(t)} ' not in self.norm_text:
                        options.append(t)
                if len(options) < 4:
                    continue
                blanked = s.text[:start] + BLANK + s.text[end:]
                prompt = (f'Selon le texte, quel nombre complète la phrase ?\n« {blanked} »' if self.fr
                          else f'According to the text, which number completes the sentence?\n“{blanked}”')
                q = self._shuffled(prompt, num.raw, options, self._quote_says(s.text), s.text, 'detail')
                if q is not None:
                    return q, s.index
        return None

    def _alter(self, s):
        """La phrase avec un nom remplacé par un autre nom du document, ou None."""
        for slot in self._slots_of(s):
            repl = self._replacements(slot, s.text, n=1)
            if not repl:
                continue
            altered = _word_re(slot.surface).sub(repl[0], s.text, count=1)
            if altered == s.text or f' {normalize(altered)} ' in self.norm_text:
                continue
            return altered, slot.surface, repl[0]
        return None

    def _statement(self, s):
        if len(s.words) > 28:
            return None
        others = []
        pool = self._spread([x for x in self.by_page.get(s.page, self.sentences)
                             if x.index != s.index and self._sentence_ok(x) and len(x.words) <= 28])
        if len(pool) < 3:
            pool = self._spread([x for x in self.sentences
                                 if x.index != s.index and self._sentence_ok(x) and len(x.words) <= 28])
        self.rnd.shuffle(pool)
        for other in pool:
            if len(others) == 3:
                break
            a = self._alter(other)
            if a is not None and a[0] not in others:
                others.append(a[0])
        if len(others) < 3:
            return None
        if self.fr:
            prompt = 'Laquelle de ces affirmations figure dans le texte ?'
            explain = 'Seule cette phrase est fidèle au texte ; dans les autres, un mot a été changé.'
        else:
            prompt = 'Which of these statements is in the text?'
            explain = 'Only this sentence matches the text; in the others, one word was changed.'
        return self._shuffled(prompt, s.text, [s.text] + others, explain, s.text, 'detail')

    @staticmethod
    def _excerpt(s, n):
        matches = list(_TOKEN_RE.finditer(s))
        if len(matches) <= n:
            return s
        return s[:matches[n - 1].end()] + '…'

    def _order(self, usable):
        if len(usable) < 6:
            return None
        picks = []
        step = len(usable) // 4
        for k in range(4):
            i = min(len(usable) - 1, k * step + self.rnd.randrange(max(1, step)))
            if all(p.index != usable[i].index for p in picks):
                picks.append(usable[i])
        if len(picks) < 3:
            return None
        first = min(picks, key=lambda p: p.index)
        if self.fr:
            prompt = 'Dans cette page, laquelle de ces idées vient en premier ?'
            explain = 'La page commence par cette idée avant d’aborder les autres.'
        else:
            prompt = 'On this page, which of these ideas comes first?'
            explain = 'The page starts with this idea before moving on to the others.'
        return self._shuffled(prompt, self._excerpt(first.text, 12), [self._excerpt(p.text, 12) for p in picks],
                              explain, first.text, 'structure')

    def _shuffled(self, prompt, correct, options, explain, quote, skill):
        unique = {}
        for o in options:
            unique.setdefault(normalize(o), o)
        if len(unique) < 3:
            return None
        items = list(unique.values())
        self.rnd.shuffle(items)
        if correct not in items:
            return None
        return {'prompt': prompt, 'options': items, 'answer': items.index(correct), 'explain': explain,
                'quote': quote, 'skill': skill}
