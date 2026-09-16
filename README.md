# Acolyte Fortnite — 1.0

Coach Fortnite local pour Windows. Le mode principal est maintenant **vocal à la demande** afin d'éviter de monopoliser le GPU pendant la partie.

## Fonctionnement

1. Appuie sur **F8** (ou sur **Parler au coach**).
2. Parle pendant quelques secondes : « Coach, je prends le fight ? ».
3. **Whisper** transcrit ta voix localement sur le CPU.
4. Acolyte capture l'écran choisi à cet instant seulement.
5. **Gemma 3 4B** analyse la capture via Ollama local et répond à la question.
6. **Piper** lit la réponse avec une voix neuronale française locale.

Aucune clé API et aucun paiement à l'usage. Après le premier téléchargement des modèles, le fonctionnement vocal est local.

## Premier lancement de la 1.0

Après la mise à jour, ferme puis relance Coach Fortnite. Le lanceur installera les nouvelles dépendances Python.

Dans l'application, clique une fois sur **Préparer la voix IA**. Le premier lancement peut télécharger :
- le modèle Whisper `base` pour la transcription ;
- la voix Piper française `fr_FR-siwis-medium`.

Ces fichiers sont conservés dans `.whisper` et `.voice` et ne sont pas retéléchargés à chaque mise à jour.

## Performances

Le mode vocal ne lance l'analyse vision que lorsque le joueur pose une question. C'est le mode conseillé pendant Fortnite.

L'ancien mode **Analyse automatique** reste disponible mais est optionnel et réglé plus lentement afin de limiter la perte de FPS.

## Installation de base

Installer Python 3.11+ et Ollama, ouvrir Ollama, puis lancer `Installer-modele.bat` une fois pour Gemma 3 4B. Utiliser ensuite l'icône **Coach Fortnite** du bureau.

## Mises à jour GitHub

Le dépôt par défaut est `Clemen5t/Coach_Fortnite_Ia`, branche `main`. Cliquer sur **Mettre à jour** dans l'application, attendre la fin, puis fermer et relancer l'icône du bureau.

L'updater conserve `.venv`, `.voice`, `.whisper`, les réglages et les modèles locaux. Les anciens fichiers sont sauvegardés dans `.updates/backup-*` en cas de restauration.

## Confidentialité

La capture écran, le microphone, la transcription, l'analyse Ollama et la synthèse Piper sont traités localement pendant l'utilisation. Les téléchargements initiaux des modèles nécessitent Internet.

## Tests

`python -m unittest -v test_local_ai test_updater`

Les tests couvrent le client Ollama local, la vision, le mode conversationnel, les filtres et le mécanisme de mise à jour. Le comportement réel du microphone, de Piper et les performances en jeu doivent être vérifiés sur Windows.
