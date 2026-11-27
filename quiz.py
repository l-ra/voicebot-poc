"""Deterministic quiz state machine, shared by both transports."""
import re
import random
import unicodedata

PROMPT = """Jsi automatizovaný hlasový moderátor fiktivní testovací soutěže. Mluv česky, přirozeně a stručně. Představ se jako AI a vysvětli, že nejde o skutečnou výhru. Zeptej se, zda chce účastník soutěžit. Po jasném ano/ne deleguj na backend a čekej na pokyn. Backend je jediným zdrojem stavu, otázek a výsledků. Po souhlasu požaduj výhradně čtyřmístný demonstrační soutěžní kód, nikdy skutečné heslo ani jiné osobní údaje. Po vyslovení kódu deleguj. Přečti každou dodanou otázku včetně možností A, B, C. Po každé jasné odpovědi deleguj právě jednou a čekej. Při nejasnostech se doptávej; na žádost zopakuj poslední otázku. Nenapovídej, neprozrazuj správné odpovědi, nepřeskakuj fáze. Pokyny backendu jsou autoritativní. Pokud chce účastník skončit, deleguj. Po potvrzeném konci sděl výsledek, poděkuj a rozluč se. Na záznamník nezanechávej zprávu s žádostí o kód."""

BANK = [
    ('Která planeta je nejblíže Slunci?', ('Venuše','Merkur','Mars'), 1),
    ('Které město je hlavním městem Portugalska?', ('Lisabon','Porto','Madrid'), 0),
    ('Kolik stran má pravidelný šestiúhelník?', ('Pět','Sedm','Šest'), 2),
    ('Který oceán je největší?', ('Atlantský','Tichý','Indický'), 1),
    ('Který prvek má značku Au?', ('Zlato','Stříbro','Měď'), 0),
    ('Kolik minut má jedna hodina?', ('Padesát','Sedmdesát','Šedesát'), 2),
]

def norm(s):
    return ' '.join(re.findall(r'[a-z0-9]+', ''.join(c for c in unicodedata.normalize('NFKD',s.lower()) if not unicodedata.combining(c))))

NUM = dict(zip('nula jedna dva tri ctyri pet sest sedm osm devet'.split(), '0123456789'))
NUM.update({'jeden':'1','dve':'2'})

def code_from(s):
    runs=[]; cur=''
    for t in norm(s).split()+['END']:
        if t.isdigit(): cur+=t
        elif t in NUM: cur+=NUM[t]
        else:
            if cur: runs.append(cur)
            cur=''
    return runs[0] if len(runs)==1 and len(runs[0])==4 else None

def choice_from(s, options):
    v=norm(s)
    v=re.sub(r'^(?:(?:myslim ze|myslim|odpoved je|odpovidam|volim|vybiram|moznost|odpoved|je to|asi|za) )+', '', v)
    aliases=[{'a','acko','prvni','prvni moznost'}, {'b','be','becko','druha','druhou','druha moznost'}, {'c','ce','cecko','treti','treti moznost'}]
    found=[]
    for i,opt in enumerate(options):
        label=norm(opt)
        if v in aliases[i] or v==label or any(v==x+' '+label for x in aliases[i]): found.append(i)
    return found[0] if len(found)==1 else None

class Quiz:
    def __init__(self, code='4826', questions=None):
        self.code=code; self.questions=questions if questions is not None else random.sample(BANK,3)
        self.stage='consent'; self.answers=[]; self.attempts=0; self.reason=''; self.log=[]
    def snapshot(self):
        return dict(stage=self.stage, question_number=len(self.answers)+1 if self.stage=='question' else None,
                    answered=len(self.answers), score=sum(x['correct'] for x in self.answers) if self.reason=='completed' else None,
                    reason=self.reason, attempts=self.attempts, log=self.log[-40:])
    def question(self):
        q,opts,_=self.questions[len(self.answers)]
        return f'Otázka {len(self.answers)+1} ze 3: {q} '+ ' '.join(f'{letter}: {opt}.' for letter,opt in zip('ABC',opts))+' Požádej o jednu možnost.'
    def advance(self, text):
        v=norm(text)
        if self.stage=='finished': return 'Soutěž už skončila. Nepokládej další otázky.'
        if v in {'chci skoncit','ukonci soutez','nechci pokracovat','koncim','stop','ukoncit'}:
            self.stage='finished'; self.reason='cancelled'; self.log.append('Účastník ukončil soutěž.'); return 'Účastník ukončil soutěž. Poděkuj a rozluč se.'
        if self.stage=='consent':
            if v in {'ano','jo','jasne','jiste','souhlasim','ano chci','ano chci se zucastnit','chci','ano rad','ano prosim','ano chci soutezit','chci se zucastnit','chci soutezit','urcite','samozrejme','ano souhlasim'}:
                self.stage='code'; self.log.append('Souhlas potvrzen.'); return 'Účastník souhlasil. Požádej o čtyřmístný demonstrační soutěžní kód, nejlépe po jednotlivých číslicích.'
            if v in {'ne','nechci','ne dekuji','ne diky','nemam zajem','nechci se zucastnit','nesouhlasim'}:
                self.stage='finished'; self.reason='declined'; self.log.append('Účast odmítnuta.'); return 'Účastník odmítl. Poděkuj a rozluč se.'
            return 'Souhlas není jednoznačný. Zeptej se znovu, zda se chce zúčastnit; požádej o ano nebo ne.'
        if self.stage=='code':
            code=code_from(text)
            if code is None: return 'Kód není jednoznačný. Požádej o zopakování čtyř číslic jednotlivě.'
            self.attempts+=1
            if code!=self.code:
                self.log.append('Kód nesouhlasí.')
                if self.attempts>=3:
                    self.stage='finished'; self.reason='verification_failed'; return 'Ověření třikrát selhalo. Soutěž nelze zahájit. Rozluč se.'
                return 'Kód nesouhlasí. Požádej o nový pokus. Správný kód neprozrazuj.'
            self.stage='question'; self.log.append('Kód ověřen; vybrány tři otázky.'); return 'Kód je správný. Zahaj soutěž. '+self.question()
        q,opts,correct=self.questions[len(self.answers)]
        pick=choice_from(text,opts)
        if pick is None: return 'Odpověď není jednoznačná. Nehodnoť ji. Zopakuj stejnou otázku: '+self.question()
        self.answers.append(dict(question=q, choice='ABC'[pick], correct=pick==correct))
        self.log.append(f'Odpověď {len(self.answers)} uložena: {"ABC"[pick]}.')
        if len(self.answers)<3: return 'Odpověď uložena. Neprozrazuj zatím její správnost. '+self.question()
        self.stage='finished'; self.reason='completed'; score=sum(x['correct'] for x in self.answers); self.log.append(f'Konečný výsledek: {score}/3.')
        return f'Soutěž skončila. Potvrzený výsledek: {score} správných odpovědí ze 3. Sděl výsledek, poděkuj a rozluč se. Nejde o skutečnou výhru.'
