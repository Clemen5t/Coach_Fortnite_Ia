"""IA locale Acolyte : conversation, mémoire de session et vision Fortnite."""
from datetime import date
from difflib import SequenceMatcher
import http.client
import json
import re
import socket
import threading
import unicodedata

MODEL = 'gemma3:4b'

PROMPT = """Tu es un coach Fortnite qui observe UNE capture d'écran.
Donne un conseil uniquement s'il est directement utile à partir d'un élément VISIBLE.
Dans un salon, menu ou chargement, réponds catégorie none. En créatif, ne simule pas
une urgence de Battle Royale si elle n'existe pas réellement.

Catégories : cover, heal, reload, rotate, height.
N'invente jamais un ennemi, objet, ressource ou information hors écran.
Dans evidence, décris brièvement ce que tu vois. Dans advice, une seule consigne en
français, 12 mots maximum. Retourne seulement le JSON demandé."""

CONVERSATION_PROMPT = """Tu es Acolyte, le coéquipier IA vocal personnel du joueur.
Tu discutes naturellement avec lui, comme dans une conversation vocale normale.

Aucune image n'est fournie dans ce mode. Ne prétends jamais voir l'écran ou l'environnement.
Le bloc HISTORIQUE RÉEL est ta seule source concernant les messages précédents.
N'invente jamais une ancienne question, un souvenir, un nom, un classement ou un fait récent.
Ne prétends jamais avoir une activité, une pensée ou une action en arrière-plan.

RÉSOLUTION DES RÉFÉRENCES : si le prompt contient « ENTITÉ COURANTE RÉSOLUE », cette
entité est prioritaire pour comprendre « il », « elle », « lui », « son », « sa », « ses »,
« le joueur », « ce joueur », « cette personne », « ce pseudo » et « ce nom ». Une correction
explicite du joueur remplace l'interprétation précédente. Si l'entité est marquée comme
« joueur Fortnite », ne la transforme jamais en bot logiciel, assistant ou programme.
Réponds directement à la vraie question posée. Si tu ne connais pas le fait avec assez de
confiance, dis-le clairement au lieu de changer de sujet.

ACTUALITÉ : tu n'as aucun accès Internet ni classement en direct. Pour une donnée actuelle
ou récente (classement, meilleur joueur actuel, dernier résultat, équipe actuelle si
incertaine), dis que tu ne peux pas la confirmer sans source récente. N'invente pas.

Style : français naturel, oral, direct. En général 1 à 4 phrases courtes. Pas de JSON."""

SCENE_PROMPT = """Tu es un CLASSIFICATEUR VISUEL d'écran Fortnite, pas un coach.
Identifie uniquement l'état ACTUEL visible sur UNE capture.
Choisis exactement une scène : lobby, creative, match, spectator, menu, loading, unknown.
Un mode créatif seulement sélectionné dans le salon reste lobby. Une mini-carte ou un HUD
seuls ne suffisent pas à conclure match. Ne déduis jamais la scène depuis la question.
evidence doit citer uniquement des indices visibles. Retourne le JSON demandé."""

ANSWER_PROMPT = """Tu es Acolyte, coéquipier/coach vocal Fortnite local.
Un classificateur visuel séparé a déjà déterminé la scène. Cette scène est une contrainte.
Ne la contredis pas et n'invente aucun élément hors écran.
Adapte ta réponse : lobby = interface/salon ; creative = entraînement/mécaniques visibles ;
match = situation de partie ; spectator = observation ; menu = interface ; loading = transition ;
unknown = incertitude explicite. Français naturel, direct, maximum 3 phrases courtes.
Retourne uniquement le JSON demandé."""

CATEGORIES = ['none', 'cover', 'heal', 'reload', 'rotate', 'height']
SCENES = ['lobby', 'creative', 'match', 'spectator', 'menu', 'loading', 'unknown']
CONFIDENCE = ['high', 'medium', 'low']

SCHEMA = {'type': 'object', 'properties': {
    'category': {'type': 'string', 'enum': CATEGORIES},
    'evidence': {'type': 'string'}, 'advice': {'type': 'string'}},
    'required': ['category', 'evidence', 'advice'], 'additionalProperties': False}
SCENE_SCHEMA = {'type': 'object', 'properties': {
    'scene': {'type': 'string', 'enum': SCENES},
    'confidence': {'type': 'string', 'enum': CONFIDENCE},
    'evidence': {'type': 'string'}},
    'required': ['scene', 'confidence', 'evidence'], 'additionalProperties': False}
ANSWER_SCHEMA = {'type': 'object', 'properties': {'answer': {'type': 'string'}},
                 'required': ['answer'], 'additionalProperties': False}


def _normalize(text):
    return ''.join(c for c in unicodedata.normalize('NFD', str(text).lower())
                   if unicodedata.category(c) != 'Mn')


def _clean(text):
    value = _normalize(text)
    value = ''.join(c if c.isalnum() else ' ' for c in value)
    return ' '.join(value.split())


def parse_advice(raw):
    try:
        result = json.loads(raw)
    except (ValueError, TypeError):
        return 'SILENCE', 'none', ''
    if not isinstance(result, dict):
        return 'SILENCE', 'none', ''
    cat = result.get('category'); text = result.get('advice'); evidence = result.get('evidence')
    if cat not in CATEGORIES or cat == 'none':
        return 'SILENCE', 'none', str(evidence or '').strip()
    if not isinstance(text, str) or not isinstance(evidence, str):
        return 'SILENCE', 'none', ''
    text = text.strip(); evidence = evidence.strip()
    if not 1 <= len(text.split()) <= 14 or len(evidence.split()) < 2:
        return 'SILENCE', 'none', evidence
    return text, cat, evidence


def _general_analysis_request(question):
    q = _clean(question)
    phrases = ('analyse mon jeu','analyse ma partie','analyse ce que je fais','analyse mon ecran',
               'analyse la partie','analyse moi','tu vois quoi','qu est ce que tu vois',
               'je suis ou','dans quel mode','quel mode','analyse le jeu','fais une analyse')
    return any(p in q for p in phrases) or q in {'analyse', 'analyse moi', 'regarde'}


def _mode_question(question):
    q = _clean(question)
    phrases = ('creatif ou','ou creatif','salon ou','ou dans le salon','dans quel mode',
               'quel mode','je suis en creatif','je suis dans le salon','je suis au salon',
               'est ce que je suis','ou est ce que je suis','je suis ou','quel contexte')
    return any(p in q for p in phrases)


def _needs_vision(question):
    q = _clean(question)
    explicit = (
        'regarde mon ecran','regarde l ecran','regarde ce que','regarde ma partie',
        'analyse mon jeu','analyse ma partie','analyse ce que je fais','analyse mon ecran',
        'analyse la partie','analyse l ecran','fais une analyse','tu vois quoi',
        'qu est ce que tu vois','que vois tu','je suis ou','dans quel mode','quel mode',
        'creatif ou','salon ou','je suis en creatif','je suis dans le salon',
        'qu est ce que j ai a l ecran','mon inventaire','mes armes','ma vie','mon bouclier',
        'ma mini carte','mon skin','ce skin','cette arme','cet objet','cette position',
        'ce que je fais','sur mon ecran','a l ecran','et la tu vois','et maintenant tu vois')
    if any(p in q for p in explicit):
        return True
    situational = ('je prends le fight','je dois push','je dois fuir','je dois rotate',
                   'je dois me soigner','je dois reload','ou je vais','je fais quoi ici')
    return any(p in q for p in situational)


def needs_vision(question):
    return _needs_vision(question)


def _history_turns(history):
    turns = []
    for raw in str(history or '').splitlines():
        line = raw.strip()
        if line.startswith('Joueur:'):
            text = line[len('Joueur:'):].strip()
            if text: turns.append(('user', text))
        elif line.startswith('Acolyte:'):
            text = line[len('Acolyte:'):].strip()
            if text: turns.append(('assistant', text))
    return turns


def _history_messages(history):
    turns = _history_turns(history)
    return ([text for role, text in turns if role == 'user'],
            [text for role, text in turns if role == 'assistant'])


_ASSISTANT_MEMORY = ('qu est ce que tu m as repondu','que m as tu repondu','ta derniere reponse',
                     'tu m as repondu quoi','c etait quoi ta reponse','tu as repondu quoi avant')
_TOPIC_MEMORY = ('on parlait de quoi','de quoi on parlait','quel etait notre sujet',
                 'c etait quoi le sujet','on disait quoi avant')
_USER_MEMORY = ('tu te rappelle','te rappelle tu','tu te rappelles','te rappelles tu',
                'tu te souviens','te souviens tu','rappelle toi','rappelle moi',
                'ce que je viens de te demander','ce que je t ai demande','ce que je t ai dit avant',
                'ce que j ai dit avant','ce que je viens de dire','dis moi ce que je t ai dit',
                'j ai dit quoi avant','je t ai dit quoi avant','j ai demande quoi avant',
                'ma derniere question','c etait quoi ma question','quelle etait ma question',
                'repete ce que je viens de dire','derniere vraie question')


def _fuzzy_ratio(text, phrases):
    return max((SequenceMatcher(None, text, phrase).ratio() for phrase in phrases), default=0.0) if text else 0.0


def _memory_request_kind(question):
    q = _clean(question); words = set(q.split())
    if any(p in q for p in _ASSISTANT_MEMORY) or _fuzzy_ratio(q, _ASSISTANT_MEMORY) >= .78:
        return 'assistant'
    if any(p in q for p in _TOPIC_MEMORY) or _fuzzy_ratio(q, _TOPIC_MEMORY) >= .80:
        return 'topic'
    if any(p in q for p in _USER_MEMORY) or _fuzzy_ratio(q, _USER_MEMORY) >= .68:
        return 'user'
    recall = any(stem in q for stem in ('rappel','souven','souvien','memoir'))
    past = any(p in q for p in ('avant','precedent','derniere','dernier','viens de','tout a l heure'))
    speech = bool(words.intersection({'dit','dire','demande','demander','question','parle','parlait','repondu','reponse'}))
    if recall and (past or speech):
        return 'assistant' if words.intersection({'repondu','reponse'}) else 'user'
    if past and speech and words.intersection({'je','moi','tu'}):
        return 'assistant' if words.intersection({'repondu','reponse'}) else 'user'
    return None


def _last_non_memory_user(users):
    for text in reversed(users):
        if _memory_request_kind(text) is None:
            return text
    return users[-1] if users else ''


def _direct_memory_answer(question, history):
    kind = _memory_request_kind(question)
    if not kind: return None
    users, assistants = _history_messages(history)
    if not users: return "Je n’ai pas de message précédent enregistré dans cette session."
    if kind == 'assistant':
        return ('Ma dernière réponse était : « ' + assistants[-1] + ' »') if assistants else "Je n’ai pas encore de réponse précédente enregistrée."
    previous = _last_non_memory_user(users)
    if kind == 'topic': return 'Juste avant, on parlait de ton message : « ' + previous + ' »'
    return 'Oui. Juste avant, tu m’as dit : « ' + previous + ' »'


def _smalltalk_answer(question):
    q = _clean(question)
    if len(q.split()) <= 10 and (q in {'ca va','ca va bien','tu vas bien','comment tu vas','comment ca va'}
                                or 'est ce que ca va' in q or 'est ce que tu vas bien' in q):
        return 'Oui, ça va bien, merci. Et toi ?'
    return None


def _current_facts_guard(question):
    q = _clean(question)
    ranking = any(p in q for p in ('meilleur joueur','meilleure joueuse','numero 1','top 1','qui domine',
                                   'meilleur du monde','classement actuel','classement mondial'))
    fresh = any(p in q for p in ('actuellement','en ce moment','aujourd hui','maintenant','recent','dernier','cette saison'))
    if ranking or fresh and any(w in q for w in ('classement','joueur','resultat','tournoi','champion')):
        return ("Je n’ai pas accès aux classements ni aux résultats en direct, donc je ne peux pas confirmer "
                "qui est le meilleur actuellement sans source récente.")
    return None


_ENTITY_ALIASES = {
    'peter bot': 'Peterbot',
    'peterbot': 'Peterbot',
}
_ENTITY_STOP = {
    'Acolyte','Fortnite','Battle','Royale','Creative','Créatif','Salon','Oui','Non','Salut','Bonjour',
    'Je','Tu','Il','Elle','En','Et','Mais','Le','La','Les','Un','Une','Ce','Cette','C','Ça','Ca',
    'Qu','Quel','Quelle','Ton','Son','Sa','Ses','Lui','Moi','Nous','Vous','Ils','Elles','Ok'
}


def _canonical_entity(name):
    value = ' '.join(str(name or '').strip(' .,!?:;«»\"“”').split())
    if not value:
        return ''
    alias = _ENTITY_ALIASES.get(_clean(value))
    return alias or value


def _extract_entity_from_text(text):
    """Extrait un pseudo/nom explicite sans casser les pseudos composés."""
    raw = str(text or '').strip()
    if not raw:
        return ''

    match = re.search(r'\b(?:[Ll]e\s+|[Ll]a\s+)?(?:joueur|joueuse)\s+([A-ZÀ-ÖØ-Þ][\wÀ-ÖØ-öø-ÿ\-]*(?:\s+[A-ZÀ-ÖØ-Þ][\wÀ-ÖØ-öø-ÿ\-]*){0,2})', raw)
    if match:
        return _canonical_entity(match.group(1))

    patterns = (
        r'(?i)\bconnais[\s-]*tu\s+([A-Za-zÀ-ÖØ-öø-ÿ0-9_\-]{2,24}(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ0-9_\-]{2,24})?)',
        r'(?i)\btu\s+connais\s+([A-Za-zÀ-ÖØ-öø-ÿ0-9_\-]{2,24}(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ0-9_\-]{2,24})?)',
        r'(?i)\bje\s+(?:parle|parlais|pensais)\s+(?:de|a|à)\s+([A-Za-zÀ-ÖØ-öø-ÿ0-9_\-]{2,24}(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ0-9_\-]{2,24})?)',
        r'(?i)\btu\s+parles\s+de\s+([A-Za-zÀ-ÖØ-öø-ÿ0-9_\-]{2,24}(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ0-9_\-]{2,24})?)',
    )
    for pattern in patterns:
        match = re.search(pattern, raw)
        if match:
            candidate = match.group(1).strip()
            parts = candidate.split()
            if len(parts) == 2 and _clean(parts[1]) in {'fortnite','joueur','player','non','bot'} and _clean(candidate) != 'peter bot':
                candidate = parts[0]
            return _canonical_entity(candidate)

    tokens = re.findall(r'\b[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ0-9_\-]{2,23}\b', raw)
    tokens = [token for token in tokens if token not in _ENTITY_STOP]
    if tokens:
        if len(tokens) >= 2:
            pair = _canonical_entity(tokens[-2] + ' ' + tokens[-1])
            if _clean(tokens[-2] + ' ' + tokens[-1]) in _ENTITY_ALIASES:
                return pair
        return _canonical_entity(tokens[-1])
    return ''


def _entity_context(history):
    """Retourne (entité, type). Les corrections du joueur priment sur les réponses IA."""
    turns = _history_turns(history)
    for role, text in reversed(turns[-12:]):
        if role != 'user':
            continue
        entity = _extract_entity_from_text(text)
        if not entity:
            continue
        q = _clean(text)
        descriptor = ''
        if 'fortnite' in q or 'joueur' in q or 'joueuse' in q or 'non le bot' in q or 'pas le bot' in q:
            descriptor = 'joueur Fortnite'
        return entity, descriptor
    for role, text in reversed(turns[-8:]):
        if role != 'assistant':
            continue
        entity = _extract_entity_from_text(text)
        if entity:
            return entity, ''
    return '', ''


def _reference_entity(question, history):
    q = _clean(question)
    if not q or q.startswith(('il faut ','il faudrait ','il y a ','il existe ','il semble ','il me faut ')):
        return '', ''
    words = set(q.split())
    explicit = any(p in q for p in (
        'le joueur','la joueuse','ce joueur','cette joueuse','cette personne','ce gars','ce mec',
        'ce pseudo','ce nom','son pseudo','son nom','sa nationalite','son pays','son equipe',
        'ses resultats','vient d ou','d ou vient','quel age','age a t il','age a il'))
    pronoun = bool(words.intersection({'il','elle','lui','son','sa','ses'}))
    if not (explicit or pronoun):
        return '', ''
    return _entity_context(history)


def _peterbot_fact(question, entity):
    """Petit cache local de faits stables pour éviter la confusion Peterbot/bot logiciel."""
    if _clean(entity) != 'peterbot':
        return None
    q = _clean(question)
    if any(p in q for p in ('vient d ou','d ou vient','origine','nationalite','quel pays')):
        return 'Peterbot est un joueur Fortnite américano-hongrois, associé aux États-Unis et à la Hongrie.'
    if any(p in q for p in ('vrai nom','nom reel','son nom complet')):
        return 'Le vrai nom de Peterbot est Peter Kata.'
    if any(p in q for p in ('quel age','age a t il','age a il','son age')):
        born = date(2007, 6, 20)
        today = date.today()
        age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
        return f'Peterbot a {age} ans ; il est né le 20 juin 2007.'
    return None


def _direct_reference_answer(question, history):
    q = _clean(question)
    current = _extract_entity_from_text(question)
    if current and _clean(current) == 'peterbot' and any(p in q for p in ('connais tu','tu connais')):
        return 'Oui. Peterbot est un joueur professionnel Fortnite, de son vrai nom Peter Kata.'

    entity, descriptor = _reference_entity(question, history)
    if not entity:
        return None

    fact = _peterbot_fact(question, entity)
    if fact:
        return fact

    spelling = any(stem in q for stem in ('epelle','epele','epeler','lettre par lettre'))
    reference = any(p in q for p in ('son pseudo','son nom','ce pseudo','ce nom','le pseudo','le nom'))
    if spelling and reference:
        compact = entity.replace(' ', '')
        return f"{entity} s’épelle {'-'.join(compact.upper())}."
    return None


def _looks_like_nonanswer(text):
    q = _clean(text)
    patterns = ('ok compris','d accord compris','je suis pret a discuter','je suis pret a parler',
                'dis moi ce que tu veux','qu est ce que tu veux savoir','je suis tout oui',
                'on peut en discuter','vas y je t ecoute','je t ecoute')
    return any(p in q for p in patterns)


class AdviceGate:
    def __init__(self): self.last_any = -1e9; self.last_category = {}
    def blocked(self, now): return [cat for cat,t in self.last_category.items() if now-t < 20]
    def allow(self, category, now):
        if category == 'none' or now-self.last_any < 5 or category in self.blocked(now): return False
        self.last_any = now; self.last_category[category] = now; return True


class LocalAI:
    def __init__(self, stop_event, port=11434):
        self.stop_event=stop_event; self.port=port; self.conn=None
        self.last_category='none'; self.last_evidence=''; self.last_scene='unknown'
        self.last_observation=''; self.last_scene_confidence='low'; self.used_vision=False
        self.lock=threading.Lock()

    def cancel(self):
        self.stop_event.set()
        with self.lock: conn=self.conn
        if conn:
            try:
                if conn.sock: conn.sock.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            conn.close()

    def request(self, path, payload=None, timeout=30):
        if self.stop_event.is_set(): raise InterruptedError('Arrêt demandé')
        conn=http.client.HTTPConnection('127.0.0.1', self.port, timeout=timeout)
        with self.lock: self.conn=conn
        try:
            conn.connect()
            if self.stop_event.is_set(): raise InterruptedError('Arrêt demandé')
            body=None if payload is None else json.dumps(payload).encode('utf-8')
            conn.request('GET' if body is None else 'POST', path, body, {'Content-Type':'application/json'})
            response=conn.getresponse(); raw=response.read(4*1024*1024)
            if response.status != 200:
                if response.status == 404: raise RuntimeError('Modèle absent : lance Installer-modele.bat.')
                raise RuntimeError('Ollama HTTP '+str(response.status)+': '+raw.decode('utf-8',errors='replace')[:250])
            result=json.loads(raw)
            if result.get('error'): raise RuntimeError(str(result['error']))
            return result
        except ConnectionRefusedError:
            raise RuntimeError('Ollama non démarré. Ouvre Ollama depuis le menu Démarrer.') from None
        finally:
            conn.close()
            with self.lock:
                if self.conn is conn: self.conn=None

    def verify(self):
        info=self.request('/api/show', {'model':MODEL}, timeout=10)
        if info.get('remote_host') or info.get('remote_model'):
            raise RuntimeError('Modèle distant refusé. Télécharge le modèle local gemma3:4b.')
        if 'vision' not in info.get('capabilities',[]):
            raise RuntimeError('Vision indisponible. Mets Ollama à jour puis télécharge gemma3:4b.')

    def generate(self, image, previous='', timeout=30, warmup=False):
        result=self.request('/api/generate', {
            'model':MODEL,'stream':False,'keep_alive':'5m','system':PROMPT,'format':SCHEMA,
            'prompt':('Réponds avec la catégorie none.' if warmup else 'Capture actuelle. Dernier conseil : '+previous),
            'images':[image], 'options':{'num_ctx':1536,'num_predict':56,'temperature':0}}, timeout=timeout)
        if not result.get('done'): raise RuntimeError('Réponse Ollama incomplète.')
        if result.get('done_reason') == 'length':
            self.last_category='none'; self.last_evidence=''; return 'SILENCE'
        text,self.last_category,self.last_evidence=parse_advice(result.get('response',''))
        return text

    def _chat_generate(self, prompt, timeout, temperature=.08):
        result=self.request('/api/generate', {
            'model':MODEL,'stream':False,'keep_alive':'5m','system':CONVERSATION_PROMPT,'prompt':prompt,
            'options':{'num_ctx':3072,'num_predict':220,'temperature':temperature}}, timeout=timeout)
        if not result.get('done'): raise RuntimeError('Réponse Ollama incomplète.')
        text=' '.join(str(result.get('response','')).strip().split())
        return text[:700] if text else "Je n'ai pas réussi à répondre."

    def chat(self, question, history='', timeout=30):
        self.used_vision=False; self.last_scene='unknown'; self.last_scene_confidence='low'
        self.last_observation='Conversation sans vision.'
        for direct in (_direct_memory_answer(question,history), _direct_reference_answer(question,history),
                       _smalltalk_answer(question), _current_facts_guard(question)):
            if direct is not None: return direct

        prompt='MESSAGE ACTUEL DU JOUEUR : '+question.strip()
        prompt += ('\nHISTORIQUE RÉEL DE CETTE SESSION :\n'+history[-3600:]) if history.strip() else '\nHISTORIQUE RÉEL : aucun message précédent.'
        entity, descriptor=_reference_entity(question,history)
        if entity:
            detail = entity + (f' ({descriptor})' if descriptor else '')
            prompt += (
                '\nENTITÉ COURANTE RÉSOLUE : '+detail+
                '\nINTERPRÉTATION OBLIGATOIRE : les pronoms et références pertinents du MESSAGE ACTUEL '
                'désignent '+entity+'. '
            )
            if descriptor == 'joueur Fortnite':
                prompt += entity+' est ici une PERSONNE / JOUEUR FORTNITE, PAS un bot logiciel. '
            prompt += 'Réponds directement à la question. Si tu ne sais pas, dis-le clairement.'

        text=self._chat_generate(prompt, timeout, temperature=.06)
        if entity and _looks_like_nonanswer(text):
            retry=(prompt+'\nTA RÉPONSE PRÉCÉDENTE N’A PAS RÉPONDU. Réponds maintenant directement au sujet de '
                   +entity+'. Si tu ne sais pas, dis « Je ne suis pas sûr sans source fiable. »')
            text=self._chat_generate(retry, timeout, temperature=0)
        return text

    def classify_scene(self, image, timeout=20):
        self.used_vision=True
        result=self.request('/api/generate', {
            'model':MODEL,'stream':False,'keep_alive':'5m','system':SCENE_PROMPT,'format':SCENE_SCHEMA,
            'prompt':'CLASSIFIE uniquement la capture actuelle. Ne donne aucun conseil.', 'images':[image],
            'options':{'num_ctx':2048,'num_predict':110,'temperature':0}}, timeout=timeout)
        if not result.get('done'): raise RuntimeError('Classification visuelle incomplète.')
        try: parsed=json.loads(str(result.get('response','')).strip())
        except (ValueError,TypeError): parsed={}
        scene=parsed.get('scene','unknown'); confidence=parsed.get('confidence','low')
        evidence=' '.join(str(parsed.get('evidence','')).strip().split())
        if scene not in SCENES: scene='unknown'
        if confidence not in CONFIDENCE: confidence='low'
        if not evidence: confidence='low'
        if confidence == 'low': scene='unknown'
        self.last_scene=scene; self.last_scene_confidence=confidence; self.last_observation=evidence[:280]
        return scene,confidence,evidence

    def _direct_scene_answer(self, scene, confidence, evidence):
        if scene=='unknown' or confidence=='low': return "Je ne peux pas déterminer le contexte avec certitude sur cette capture."
        if scene=='lobby': return "Tu es dans le salon Fortnite, pas dans une session créative en jeu."
        if scene=='creative': return "Tu es dans une session créative en jeu, pas dans le salon."
        if scene=='match': return "Tu es en partie, pas dans le salon ni dans une session créative d’entraînement."
        if scene=='spectator': return "Tu es en mode spectateur ou replay."
        if scene=='menu': return "Tu es dans un menu Fortnite, pas dans une session de jeu active."
        if scene=='loading': return "Tu es sur un écran de chargement ou de transition."
        return "Je ne peux pas déterminer le contexte avec certitude."

    def ask(self, image, question, history='', timeout=30):
        question=question.strip()
        memory=_direct_memory_answer(question,history)
        if memory is not None:
            self.used_vision=False; self.last_scene='unknown'; self.last_scene_confidence='low'
            self.last_observation='Rappel direct depuis la mémoire réelle de la session.'
            return memory
        if not _needs_vision(question):
            return self.chat(question,history,timeout=timeout)
        self.used_vision=True
        if not image: return "J’ai besoin d’une capture d’écran pour répondre à cette question visuelle."
        scene_timeout=max(6,min(20,int(timeout*.45)))
        scene,confidence,evidence=self.classify_scene(image,timeout=scene_timeout)
        if _mode_question(question): return self._direct_scene_answer(scene,confidence,evidence)
        if _general_analysis_request(question):
            if scene=='lobby': return "Tu es dans le salon Fortnite. Je peux analyser l’interface visible, mais pas ton gameplay tant que tu n’es pas en jeu."
            if scene=='menu': return "Tu es dans un menu Fortnite. Il n’y a pas de gameplay actif à analyser sur cette capture."
            if scene=='loading': return "L’écran est en chargement ou en transition. Attends d’être en jeu et redemande-moi une analyse."
            if scene=='unknown': return "Je vois Fortnite, mais cette capture ne permet pas d’identifier le contexte avec assez de certitude."
        prompt=(f'SCÈNE IMPOSÉE : {scene}\nCONFIANCE : {confidence}\nINDICES VISUELS : {evidence or "aucun"}\n'
                f'QUESTION DU JOUEUR : {question}\nRéponds sans contredire la scène et sans inventer.')
        if history.strip(): prompt += '\nCONTEXTE RÉCENT : '+history[-1600:]
        answer_timeout=max(6,timeout-scene_timeout)
        result=self.request('/api/generate', {
            'model':MODEL,'stream':False,'keep_alive':'5m','system':ANSWER_PROMPT,'format':ANSWER_SCHEMA,
            'prompt':prompt,'images':[image], 'options':{'num_ctx':3072,'num_predict':170,'temperature':0.05}},
            timeout=answer_timeout)
        if not result.get('done'): raise RuntimeError('Réponse Ollama incomplète.')
        raw=str(result.get('response','')).strip()
        try:
            parsed=json.loads(raw); answer=' '.join(str(parsed.get('answer','')).strip().split())
        except (ValueError,TypeError,AttributeError): answer=' '.join(raw.split())
        return answer[:700] if answer else "Je vois la capture, mais je n'ai pas assez d'éléments pour te conseiller précisément."


def usable(text, age, limit, previous):
    return bool(text and text.upper().strip(' .!') != 'SILENCE' and age <= limit and text != previous)
