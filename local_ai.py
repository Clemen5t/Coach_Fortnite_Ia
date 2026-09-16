"""IA locale Acolyte : conversation, mémoire de session et vision Fortnite."""
import http.client
import json
import socket
import threading
import unicodedata

MODEL = 'gemma3:4b'

# coach.py demande encore Whisper "base". On remplace ce chargement par "small"
# sans casser l'updater historique, et on ajoute un contexte de vocabulaire français.
WHISPER_HINT = (
    "Acolyte, Fortnite, créatif, salon, Battle Royale, Zéro construction, build, edit, "
    "aim, inventaire, mini-carte, bouclier, soins, recharge, rotation, analyse mon jeu."
)
try:
    import faster_whisper as _faster_whisper
    if not getattr(_faster_whisper, '_acolyte_whisper_patch', False):
        _OriginalWhisperModel = _faster_whisper.WhisperModel

        class _AcolyteWhisperModel:
            def __init__(self, model_size_or_path, *args, **kwargs):
                target = 'small' if str(model_size_or_path).lower() == 'base' else model_size_or_path
                self._inner = _OriginalWhisperModel(target, *args, **kwargs)

            def transcribe(self, audio, *args, **kwargs):
                kwargs.setdefault('initial_prompt', WHISPER_HINT)
                return self._inner.transcribe(audio, *args, **kwargs)

            def __getattr__(self, name):
                return getattr(self._inner, name)

        _faster_whisper.WhisperModel = _AcolyteWhisperModel
        _faster_whisper._acolyte_whisper_patch = True
except Exception:
    # Les tests ou une installation incomplète peuvent ne pas avoir faster-whisper.
    # VoiceEngine affichera alors son erreur normale au moment du chargement.
    pass


PROMPT = """Tu es un coach Fortnite qui observe UNE capture d'écran.
Donne un conseil uniquement s'il est directement utile à partir d'un élément VISIBLE.
Dans un salon, menu ou chargement, réponds catégorie none. En créatif, ne simule pas
une urgence de Battle Royale si elle n'existe pas réellement.

Catégories :
- cover : menace visible et protection utile.
- heal : vie/bouclier visiblement bas et soin raisonnable.
- reload : chargeur visiblement faible ou rechargement clairement utile.
- rotate : zone/tempête/position visible justifiant un déplacement.
- height : hauteur visible apportant un avantage immédiat.

N'invente jamais un ennemi, objet, ressource ou information hors écran.
Dans evidence, décris brièvement ce que tu vois. Dans advice, une seule consigne en
français, 12 mots maximum. Retourne seulement le JSON demandé."""

CONVERSATION_PROMPT = """Tu es Acolyte, le coéquipier IA vocal personnel du joueur.
Tu discutes naturellement avec lui, comme dans une conversation vocale normale.

Aucune image n'est fournie dans ce mode. Ne prétends jamais voir l'écran, Fortnite,
le joueur ou son environnement. Si voir l'écran est nécessaire, dis brièvement que tu
peux l'analyser si le joueur te le demande.

Le bloc HISTORIQUE RÉEL est ta seule source concernant les messages précédents.
N'invente jamais une ancienne question, un réglage, un sujet ou un souvenir absent de
cet historique. Ne prétends pas non plus avoir une activité ou une pensée en arrière-plan.

Tu peux parler de Fortnite, entraînement, stratégie, réglages, matériel, questions
générales ou simplement discuter. Réponds directement au message actuel et utilise
l'historique seulement lorsqu'il est pertinent.

Style : français naturel, oral, direct. En général 1 à 4 phrases courtes. Pas de JSON,
pas de préambule robotique."""

SCENE_PROMPT = """Tu es un CLASSIFICATEUR VISUEL d'écran Fortnite, pas un coach.
Identifie uniquement l'état ACTUEL visible sur UNE capture.

Choisis exactement une scène :
- lobby : salon Fortnite, avatar central, groupe, mode, bouton Jouer/Prêt, matchmaking.
- creative : session créative/entraînement EN JEU dans un monde 3D jouable.
- match : partie Battle Royale/Zéro construction active.
- spectator : spectateur ou replay.
- menu : casier, boutique, quêtes, paramètres ou navigation plein écran.
- loading : chargement ou transition.
- unknown : pas assez d'indices fiables.

Règles : un mode créatif seulement sélectionné dans le salon reste lobby. Une mini-carte
ou un HUD seuls ne suffisent pas à conclure match. Ne déduis jamais la scène depuis la
question du joueur. evidence doit citer uniquement des indices visibles. Retourne le JSON."""

ANSWER_PROMPT = """Tu es Acolyte, coéquipier/coach vocal Fortnite local.
Un classificateur visuel séparé a déjà déterminé la scène. Cette scène est une contrainte.
Ne la contredis pas et n'invente aucun élément hors écran.

Adapte ta réponse : lobby = interface/salon sans faux conseil de combat ; creative =
entraînement/mécaniques visibles ; match = situation de partie visible ; spectator =
observation ; menu = interface ; loading = transition ; unknown = incertitude explicite.

Français naturel, direct, maximum 3 phrases courtes. Retourne uniquement le JSON demandé."""

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
    """Normalisation tolérante aux apostrophes et à la ponctuation de Whisper."""
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
    normalized = _clean(text)
    banned = ['meilleure couverture', 'plus securis', 'position de securite',
              'position plus sure', 'scanner', 'scanne', 'couverture immediate',
              'couverture solide', 'couverture sure']
    if any(x in normalized for x in banned):
        return 'SILENCE', 'none', evidence
    return text, cat, evidence


def _general_analysis_request(question):
    q = _clean(question)
    phrases = (
        'analyse mon jeu', 'analyse ma partie', 'analyse ce que je fais', 'analyse mon ecran',
        'analyse la partie', 'analyse moi', 'tu vois quoi', 'qu est ce que tu vois',
        'je suis ou', 'dans quel mode', 'quel mode', 'analyse le jeu', 'fais une analyse'
    )
    return any(p in q for p in phrases) or q in {'analyse', 'analyse moi', 'regarde'}


def _mode_question(question):
    q = _clean(question)
    phrases = (
        'creatif ou', 'ou creatif', 'salon ou', 'ou dans le salon', 'dans quel mode',
        'quel mode', 'je suis en creatif', 'je suis dans le salon', 'je suis au salon',
        'est ce que je suis', 'ou est ce que je suis', 'je suis ou', 'quel contexte'
    )
    return any(p in q for p in phrases)


def _needs_vision(question):
    q = _clean(question)
    explicit = (
        'regarde mon ecran', 'regarde l ecran', 'regarde ce que', 'regarde ma partie',
        'analyse mon jeu', 'analyse ma partie', 'analyse ce que je fais', 'analyse mon ecran',
        'analyse la partie', 'analyse l ecran', 'fais une analyse', 'tu vois quoi',
        'qu est ce que tu vois', 'que vois tu', 'je suis ou', 'dans quel mode', 'quel mode',
        'creatif ou', 'salon ou', 'je suis en creatif', 'je suis dans le salon',
        'qu est ce que j ai a l ecran', 'mon inventaire', 'mes armes', 'ma vie', 'mon bouclier',
        'ma mini carte', 'mon skin', 'ce skin', 'cette arme', 'cet objet', 'cette position',
        'ce que je fais', 'sur mon ecran', 'a l ecran', 'et la tu vois', 'et maintenant tu vois'
    )
    if any(p in q for p in explicit):
        return True
    situational = ('je prends le fight', 'je dois push', 'je dois fuir', 'je dois rotate',
                   'je dois me soigner', 'je dois reload', 'ou je vais', 'je fais quoi ici')
    return any(p in q for p in situational)


def needs_vision(question):
    return _needs_vision(question)


def _history_messages(history):
    users = []; assistants = []
    for raw in str(history or '').splitlines():
        line = raw.strip()
        if line.startswith('Joueur:'):
            text = line[len('Joueur:'):].strip()
            if text:
                users.append(text)
        elif line.startswith('Acolyte:'):
            text = line[len('Acolyte:'):].strip()
            if text:
                assistants.append(text)
    return users, assistants


def _memory_request_kind(question):
    """Détecte les demandes de rappel, même avec une transcription imparfaite."""
    q = _clean(question)
    words = set(q.split())

    assistant_phrases = (
        'qu est ce que tu m as repondu', 'que m as tu repondu', 'ta derniere reponse',
        'tu m as repondu quoi', 'c etait quoi ta reponse', 'tu as repondu quoi avant'
    )
    if any(p in q for p in assistant_phrases):
        return 'assistant'

    topic_phrases = (
        'on parlait de quoi', 'de quoi on parlait', 'quel etait notre sujet',
        'c etait quoi le sujet', 'on disait quoi avant'
    )
    if any(p in q for p in topic_phrases):
        return 'topic'

    user_phrases = (
        'tu te rappelle', 'te rappelle tu', 'tu te rappelles', 'te rappelles tu',
        'tu te souviens', 'te souviens tu', 'rappelle toi', 'rappelle moi',
        'ce que je viens de te demander', 'ce que je t ai demande',
        'ce que j ai eu a te demander', 'ce que je t ai dit avant',
        'ce que j ai dit avant', 'ce que je viens de dire', 'ce que je t ai dit',
        'dis moi ce que je t ai dit', 'dis moi ce que j ai dit',
        'j ai dit quoi avant', 'je t ai dit quoi avant', 'j ai demande quoi avant',
        'ma derniere question', 'qu est ce que je viens de te demander',
        'que viens je de te demander', 'c etait quoi ma question',
        'quelle etait ma question', 'repete ce que je viens de dire'
    )
    if any(p in q for p in user_phrases):
        return 'user'

    # Heuristique tolérante aux petites erreurs Whisper : notion de rappel + passé.
    recall = any(stem in q for stem in ('rappel', 'souven', 'memoir'))
    past = any(p in q for p in ('avant', 'juste avant', 'precedent', 'derniere', 'dernier',
                                'viens de', 'tout a l heure'))
    speech = bool(words.intersection({'dit', 'dire', 'demande', 'demander', 'question',
                                      'parle', 'parlait', 'repondu', 'reponse'}))
    if recall and (past or speech):
        return 'assistant' if ('repondu' in words or 'reponse' in words) else 'user'
    if past and speech and ('je' in words or 'moi' in words or 'tu' in words):
        return 'assistant' if ('repondu' in words or 'reponse' in words) else 'user'
    return None


def _direct_memory_answer(question, history):
    kind = _memory_request_kind(question)
    if not kind:
        return None
    users, assistants = _history_messages(history)
    if not users:
        return "Je n’ai pas de message précédent enregistré dans cette session."
    if kind == 'assistant':
        if assistants:
            return 'Ma dernière réponse était : « ' + assistants[-1] + ' »'
        return "Je n’ai pas encore de réponse précédente enregistrée dans cette session."
    if kind == 'topic':
        return 'Juste avant, on parlait de ton message : « ' + users[-1] + ' »'
    return 'Oui. Juste avant, tu m’as dit : « ' + users[-1] + ' »'


class AdviceGate:
    def __init__(self):
        self.last_any = -1e9; self.last_category = {}

    def blocked(self, now):
        return [cat for cat, t in self.last_category.items() if now - t < 20]

    def allow(self, category, now):
        if category == 'none' or now - self.last_any < 5 or category in self.blocked(now):
            return False
        self.last_any = now; self.last_category[category] = now
        return True


class LocalAI:
    def __init__(self, stop_event, port=11434):
        self.stop_event = stop_event; self.port = port; self.conn = None
        self.last_category = 'none'; self.last_evidence = ''
        self.last_scene = 'unknown'; self.last_observation = ''; self.last_scene_confidence = 'low'
        self.used_vision = False; self.lock = threading.Lock()

    def cancel(self):
        self.stop_event.set()
        with self.lock:
            conn = self.conn
        if conn:
            try:
                if conn.sock:
                    conn.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()

    def request(self, path, payload=None, timeout=30):
        if self.stop_event.is_set():
            raise InterruptedError('Arrêt demandé')
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=timeout)
        with self.lock:
            self.conn = conn
        try:
            conn.connect()
            if self.stop_event.is_set():
                raise InterruptedError('Arrêt demandé')
            body = None if payload is None else json.dumps(payload).encode('utf-8')
            conn.request('GET' if body is None else 'POST', path, body,
                         {'Content-Type': 'application/json'})
            response = conn.getresponse(); raw = response.read(4 * 1024 * 1024)
            if response.status != 200:
                if response.status == 404:
                    raise RuntimeError('Modèle absent : lance Installer-modele.bat.')
                raise RuntimeError('Ollama HTTP ' + str(response.status) + ': ' +
                                   raw.decode('utf-8', errors='replace')[:250])
            result = json.loads(raw)
            if result.get('error'):
                raise RuntimeError(str(result['error']))
            return result
        except ConnectionRefusedError:
            raise RuntimeError('Ollama non démarré. Ouvre Ollama depuis le menu Démarrer.') from None
        finally:
            conn.close()
            with self.lock:
                if self.conn is conn:
                    self.conn = None

    def verify(self):
        info = self.request('/api/show', {'model': MODEL}, timeout=10)
        if info.get('remote_host') or info.get('remote_model'):
            raise RuntimeError('Modèle distant refusé. Télécharge le modèle local gemma3:4b.')
        if 'vision' not in info.get('capabilities', []):
            raise RuntimeError('Vision indisponible. Mets Ollama à jour puis télécharge gemma3:4b.')

    def generate(self, image, previous='', timeout=30, warmup=False):
        result = self.request('/api/generate', {
            'model': MODEL, 'stream': False, 'keep_alive': '5m', 'system': PROMPT,
            'format': SCHEMA,
            'prompt': ('Réponds avec la catégorie none.' if warmup
                       else 'Capture actuelle. Dernier conseil : ' + previous),
            'images': [image],
            'options': {'num_ctx': 1536, 'num_predict': 56, 'temperature': 0}}, timeout=timeout)
        if not result.get('done'):
            raise RuntimeError('Réponse Ollama incomplète.')
        if result.get('done_reason') == 'length':
            self.last_category = 'none'; self.last_evidence = ''
            return 'SILENCE'
        text, self.last_category, self.last_evidence = parse_advice(result.get('response', ''))
        return text

    def chat(self, question, history='', timeout=30):
        self.used_vision = False
        self.last_scene = 'unknown'; self.last_scene_confidence = 'low'
        self.last_observation = 'Conversation sans vision.'

        memory_answer = _direct_memory_answer(question, history)
        if memory_answer is not None:
            return memory_answer

        prompt = 'MESSAGE ACTUEL DU JOUEUR : ' + question.strip()
        if history.strip():
            prompt += '\nHISTORIQUE RÉEL DE CETTE SESSION (source unique de mémoire) :\n' + history[-2600:]
        else:
            prompt += '\nHISTORIQUE RÉEL DE CETTE SESSION : aucun message précédent.'
        result = self.request('/api/generate', {
            'model': MODEL, 'stream': False, 'keep_alive': '5m',
            'system': CONVERSATION_PROMPT, 'prompt': prompt,
            'options': {'num_ctx': 3072, 'num_predict': 220, 'temperature': 0.15}}, timeout=timeout)
        if not result.get('done'):
            raise RuntimeError('Réponse Ollama incomplète.')
        text = ' '.join(str(result.get('response', '')).strip().split())
        return text[:700] if text else "Je n'ai pas réussi à répondre."

    def classify_scene(self, image, timeout=20):
        self.used_vision = True
        result = self.request('/api/generate', {
            'model': MODEL, 'stream': False, 'keep_alive': '5m', 'system': SCENE_PROMPT,
            'format': SCENE_SCHEMA,
            'prompt': 'CLASSIFIE uniquement la capture actuelle. Ne donne aucun conseil.',
            'images': [image],
            'options': {'num_ctx': 2048, 'num_predict': 110, 'temperature': 0}}, timeout=timeout)
        if not result.get('done'):
            raise RuntimeError('Classification visuelle incomplète.')
        try:
            parsed = json.loads(str(result.get('response', '')).strip())
        except (ValueError, TypeError):
            parsed = {}
        scene = parsed.get('scene', 'unknown'); confidence = parsed.get('confidence', 'low')
        evidence = ' '.join(str(parsed.get('evidence', '')).strip().split())
        if scene not in SCENES:
            scene = 'unknown'
        if confidence not in CONFIDENCE:
            confidence = 'low'
        if not evidence:
            confidence = 'low'
        if confidence == 'low':
            scene = 'unknown'
        self.last_scene = scene; self.last_scene_confidence = confidence
        self.last_observation = evidence[:280]
        return scene, confidence, evidence

    def _direct_scene_answer(self, scene, confidence, evidence):
        if scene == 'unknown' or confidence == 'low':
            return "Je ne peux pas déterminer avec certitude si tu es dans le salon ou en créatif sur cette capture."
        if scene == 'lobby':
            return "Tu es dans le salon Fortnite, pas dans une session créative en jeu."
        if scene == 'creative':
            return "Tu es dans une session créative en jeu, pas dans le salon."
        if scene == 'match':
            return "Tu es en partie, pas dans le salon ni dans une session créative d’entraînement."
        if scene == 'spectator':
            return "Tu es en mode spectateur ou replay."
        if scene == 'menu':
            return "Tu es dans un menu Fortnite, pas dans une session de jeu active."
        if scene == 'loading':
            return "Tu es sur un écran de chargement ou de transition."
        return "Je ne peux pas déterminer le contexte avec certitude."

    def ask(self, image, question, history='', timeout=30):
        question = question.strip()

        # La mémoire passe AVANT le routage vision. Une transcription imparfaite contenant
        # des mots comme « ce que je fais » ne doit pas déclencher une fausse capture.
        memory_answer = _direct_memory_answer(question, history)
        if memory_answer is not None:
            self.used_vision = False
            self.last_scene = 'unknown'; self.last_scene_confidence = 'low'
            self.last_observation = 'Rappel direct depuis la mémoire réelle de la session.'
            return memory_answer

        if not _needs_vision(question):
            return self.chat(question, history, timeout=timeout)

        self.used_vision = True
        if not image:
            return "J’ai besoin d’une capture d’écran pour répondre à cette question visuelle."
        scene_timeout = max(6, min(20, int(timeout * 0.45)))
        scene, confidence, evidence = self.classify_scene(image, timeout=scene_timeout)

        if _mode_question(question):
            return self._direct_scene_answer(scene, confidence, evidence)

        if _general_analysis_request(question):
            if scene == 'lobby':
                return ("Tu es dans le salon Fortnite. Je peux analyser le mode, le groupe et "
                        "l’interface visibles, mais pas ton gameplay tant que tu n’es pas en jeu.")
            if scene == 'menu':
                return "Tu es dans un menu Fortnite. Il n’y a pas de gameplay actif à analyser sur cette capture."
            if scene == 'loading':
                return "L’écran est en chargement ou en transition. Attends d’être en jeu et redemande-moi une analyse."
            if scene == 'unknown':
                return "Je vois Fortnite, mais cette capture ne permet pas d’identifier le contexte avec assez de certitude."

        prompt = (
            'SCÈNE IMPOSÉE PAR LE CLASSIFICATEUR : ' + scene + '\n'
            'CONFIANCE : ' + confidence + '\n'
            'INDICES VISUELS : ' + (evidence or 'aucun indice fiable') + '\n'
            'QUESTION DU JOUEUR : ' + question + '\n'
            'Réponds sans contredire la scène imposée et sans ajouter d’élément non visible.'
        )
        if history.strip():
            prompt += '\nCONTEXTE RÉCENT : ' + history[-1400:]
        answer_timeout = max(6, timeout - scene_timeout)
        result = self.request('/api/generate', {
            'model': MODEL, 'stream': False, 'keep_alive': '5m', 'system': ANSWER_PROMPT,
            'format': ANSWER_SCHEMA, 'prompt': prompt, 'images': [image],
            'options': {'num_ctx': 3072, 'num_predict': 170, 'temperature': 0.05}},
            timeout=answer_timeout)
        if not result.get('done'):
            raise RuntimeError('Réponse Ollama incomplète.')
        raw = str(result.get('response', '')).strip()
        try:
            parsed = json.loads(raw)
            answer = ' '.join(str(parsed.get('answer', '')).strip().split())
        except (ValueError, TypeError, AttributeError):
            answer = ' '.join(raw.split())
        if not answer:
            return "Je vois la capture, mais je n'ai pas assez d'éléments pour te conseiller précisément."
        return answer[:700]


def usable(text, age, limit, previous):
    return bool(text and text.upper().strip(' .!') != 'SILENCE' and age <= limit and text != previous)
