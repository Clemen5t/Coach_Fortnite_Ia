## Acolyte 2.5.1 — correction du redémarrage après mise à jour

- Corrige l’erreur PyInstaller `Security validation failure: invalid originating onefile parent process (PID not found)` après remplacement automatique de `Acolyte.exe`.
- Le helper de mise à jour supprime désormais les variables privées `_PYI_*` héritées de l’ancien onefile et transmet `PYINSTALLER_RESET_ENVIRONMENT=1` au nouvel Acolyte.
- Le lancement de transition vers l’EXE utilise la même remise à zéro d’environnement.
- Build verrouillé sur PyInstaller 6.22.3.x afin d’utiliser les correctifs récents de validation du bootloader.

## Acolyte 2.5 — mesure, monitoring et restauration intelligente

- **Analyse complète** : matériel, Windows, réseau, stockage, démarrage, jeux, versions installées, températures disponibles et dernier benchmark.
- **Monitoring matériel** : CPU/RAM à haute fréquence, GPU/VRAM et capteurs thermiques en arrière-plan quand Windows ou un provider matériel fiable les expose. Une température absente reste affichée comme indisponible.
- **Overlay jeu opt-in** : FPS, 1 % low et frametime en direct via PresentMon lorsque Fortnite tourne, plus CPU/RAM et températures disponibles. La capture s'arrête avec l'overlay et avant un benchmark classique.
- **Benchmark AVANT / APRÈS** : verdict basé sur FPS moyen, 1 % low, 0,1 % low et stutters. Le rollback automatique est optionnel et différé jusqu'à la fermeture de Fortnite.
- **Historique détaillé** : journal des réglages suivis avec ancienne/nouvelle valeur et restauration individuelle protégée contre l'écrasement d'une modification externe.
- **Profils automatiques par jeu** : le profil générique applique la préférence GPU au lancement puis la restaure à la fermeture ; Fortnite conserve son profil compétitif réversible séparé.
- **Score explicable** : bouton « Pourquoi ce score ? » avec détail des points et des recommandations qui ont réellement retiré des points.
- **Versions & mises à jour** : lecture locale des pilotes GPU et du BIOS, recherche des mises à jour proposées par Windows Update et accès aux sources officielles fabricant. Acolyte ne prétend pas connaître une version « latest » sans source vérifiée.
- **Coach IA + diagnostic PC** : les mesures Acolyte du PC et le dernier benchmark sont injectés dans le contexte local du coach pour répondre aux questions de performances sans inventer de métriques.
- Les protections existantes restent en place : pas d'OC/UV automatique, pas de modification BIOS automatique, pas de désactivation automatique de Defender/pare-feu, pas de faux tweaks HPET/timers.

## Acolyte 2.3 — Gaming Lab & pack compétitif
- Nouveau **Lab Gaming** : sépare les optimisations automatiques, les réglages à mesurer et les tweaks volontairement exclus.
- Nouveau **Pack compétitif sûr** : Game Mode, captures off, plan Ryzen équilibré, souris sans accélération, alimentation réseau, RSS/TCP sain et P2P Windows Update désactivé.
- AMD RX 7000 : recommandations guidées pour Anti-Lag / Chill / HYPR-RX sans écrire de clés Adrenalin privées non documentées.
- Windows 11 : raccourci vers les optimisations pour jeux fenêtrés, au lieu de forcer une clé registre non documentée.
- Fortnite : profil compétitif réversible + benchmark PresentMon + reset shader cache uniquement en dépannage.
- Les hacks HPET/timers, NetworkThrottlingIndex forcé, désactivation de Defender/pare-feu/Windows Update, désactivation massive de services, OC/UV automatique et custom Windows automatique restent exclus.
- Les réglages variables (HAGS, Nagle, RSC/Interrupt Moderation, VBS) restent séparés et doivent être validés par mesure avant/après.

## Acolyte 2.2 — auto-tune mesuré & recherche anti-placebo

- **Auto-tune PC complet** : base Windows/GPU/réseau réversible adaptée au matériel, fréquence écran maximale détectée puis test réseau avant/après.
- **Auto-tune réseau faible latence** : RSS reste actif ; RSC et Interrupt Moderation sont testés quand le pilote les expose. Acolyte restaure automatiquement l’ancien réglage si la mesure régresse.
- **Benchmark des régions Fortnite** avec les endpoints Epic : ping moyen, jitter et pertes.
- **Profil Fortnite compétitif** réversible + benchmark FPS / 1 % low / 0,1 % low / stutters.
- **Score séparé Santé / Gaming / Global**, sans donner les points du benchmark quand rien n’a été mesuré.
- Nouvelle page **Méthode** : catalogue des optimisations automatiques, mesurées, manuelles ou refusées.
- Les tweaks HPET/bcdedit timers, purges de standby RAM, suppressions massives de services et clés historiques non validées ne sont pas appliqués automatiquement.
- Les launch arguments Fortnite populaires (`-LANPLAY`, `-NOSPLASH`, `-NOTEXTURESTREAMING`, `-USEALLAVAILABLECORES`) sont documentés mais pas forcés : ils ne constituent pas un gain universel démontré.
- **Aucun overclock CPU/GPU/RAM automatique** : EXPO/PBO/Curve Optimizer/OC restent des réglages avancés qui exigent validation de stabilité, températures et tensions.


## Acolyte 2.1 — scores séparés et profils par jeu
- Score global = 45 % santé Windows + 55 % performance gaming mesurable.
- Sous-scores Santé Windows et Gaming affichés séparément.
- Premier profil par jeu : Fortnite compétitif max FPS, réversible avec sauvegarde de GameUserSettings.ini.
- Le profil Fortnite active Game Mode, coupe Game DVR, force le GPU haute performance et applique un preset compétitif sans modifier le renderer, la résolution ni les touches.
- Le score gaming réserve 20 points au benchmark réel (1 % low, 0,1 % low, stutters) au lieu d'inventer un gain non mesuré.
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

## Correction 1.3.8 — benchmark réseau

Le benchmark utilise désormais `ping.exe` de Windows, teste les destinations en parallèle et conserve une interface réactive. Les erreurs et absences de réponses affichent une mesure indisponible avec le diagnostic, jamais une latence fictive de 0 ms. Une absence de réponse ICMP ne prouve pas une panne Internet. Les durées inférieures à 1 ms sont comptées à leur borne supérieure de 1 ms ; le jitter nécessite au moins deux réponses.

Installation : **Mettre à jour**, fermer puis relancer Acolyte, vérifier **1.3.8**, puis **Optimisation PC → Benchmark réseau**.

Tests du benchmark et du fil de travail : `python -m unittest -v test_pc_optimizer test_updater`. Ces tests simulent les sorties Windows ; une vérification sur un PC Windows reste nécessaire.

## Centre d’optimisation Windows 1.4.0

Huit catégories intégrées à Acolyte, inspirées des domaines présentés par FPSDoctor, avec une implémentation indépendante. Aucun réglage Fortnite n’est modifié.

| Catégorie | Action disponible |
| --- | --- |
| Logiciels | Lister et désinstaller au choix Actualités, Météo, Solitaire, Conseils et Clipchamp lorsqu’ils sont présents et amovibles, pour le compte courant ; ouvrir les applications Windows pour le reste. |
| Démarrage | Lister les entrées Run du compte courant, retirer une entrée sélectionnée, la rétablir avec Restaurer ; ouvrir Windows pour les autres sources de démarrage. |
| Mises à jour | Ouvrir Windows Update et les sites officiels AMD, NVIDIA et Intel. La recherche et l’installation sont effectuées dans ces outils, pas automatiquement par Acolyte. |
| RAM | Lire les barrettes et la vitesse configurée ; accéder au support MSI B650 Gaming Plus WiFi. EXPO/XMP reste à vérifier et configurer dans le BIOS selon le kit. |
| GPU | Afficher les versions installées ; accéder aux outils officiels et aux paramètres graphiques Windows. Pas d’overclocking automatique. |
| Windows | Appliquer au choix le mode Jeu et la désactivation des captures Game Bar. |
| USB | Lister les périphériques ; test optionnel sans suspension sélective sur secteur uniquement. Désactivation non cochée par défaut. |
| Alimentation | Voir les plans et sélectionner explicitement Équilibré, avec conservation de l’ancien plan. |

### Utilisation

1. Cliquer sur **Mettre à jour**, fermer puis relancer Acolyte et vérifier **1.4.0**.
2. Ouvrir **Optimisation PC**, puis la catégorie voulue.
3. Cocher les réglages désirés et cliquer sur **Appliquer la sélection**. La confirmation récapitule aussi les cases cochées dans les autres catégories.
4. Les désinstallations et retraits du démarrage nécessitent une sélection de ligne et une confirmation distincte.
5. **Restaurer** rétablit toutes les valeurs suivies depuis la sauvegarde initiale, y compris les entrées Run retirées. Fermer le jeu avant de comparer les changements.

La sauvegarde atomique `pc-optimizer-state-v2.json` conserve la première valeur de chaque réglage, son type de registre et son absence éventuelle. Elle est liée au PC et au compte Windows. Les écritures sont vérifiées et annulées en cas d’échec ; une restauration incomplète conserve son journal pour réessayer. Un verrou empêche deux instances de modifier simultanément la sauvegarde. Les opérations PC empêchent temporairement la fermeture normale et la mise à jour de l’application.

Les désinstallations peuvent supprimer des données locales : Restaurer ne les annule pas. Les actions effectuées manuellement dans Windows Update, AMD, NVIDIA, Intel ou le BIOS ne sont pas suivies. Si une sauvegarde de l’ancien optimiseur est présente, les nouveaux changements sont bloqués et l’ancien fichier est conservé : les paramètres manquants ne peuvent pas être reconstruits honnêtement.

Aucun gain de FPS ou de ping n’est garanti. L’application ne force plus HAGS, les paramètres TCP, RSS ou les réglages avancés de la carte réseau. Elle ne désactive ni la sécurité Windows ni les mises à jour.

### Validation et références

Depuis un clone du dépôt : `python -m unittest -v test_windows_optimizer test_pc_optimizer test_updater`. Les deux tests PC sont destinés au développement et ne sont pas ajoutés au manifeste de mise à jour pour conserver la compatibilité avec l’updater 1.3.8. Les scénarios simulent Windows : vérification réelle sur Windows 11 nécessaire, notamment pour les pilotes et la disponibilité des réglages USB.

- Catégories de référence : https://fpsdoctor.com/en
- Commandes d’alimentation : https://learn.microsoft.com/en-us/windows-hardware/design/device-experiences/powercfg-command-line-options
- Panneaux Windows : https://learn.microsoft.com/en-us/windows/apps/develop/launch/launch-settings
- Microsoft déconseille de désactiver généralement la suspension USB : https://learn.microsoft.com/en-us/windows-hardware/drivers/usbcon/usb-selective-suspend
