# Coach Fortnite local — 0.3.1

Prototype Windows : captures écran analysées par Gemma 3 4B via Ollama local,
conseils vocaux Windows, mesure du délai et rejet des réponses périmées.
Aucune clé API. Aucun contrôle automatique du jeu.

## Installer ou migrer

Première installation : installer Python 3.11+ et Ollama, ouvrir Ollama,
puis `Installer-modele.bat` et `Lancer.bat`.

Depuis une version précédente : fermer le coach, copier les fichiers de cette
version dans le dossier existant et accepter le remplacement. Garder `.venv`.
Ne pas relancer l'installation du modèle s'il est déjà présent dans Ollama.

## Mises à jour GitHub

Le dépôt par défaut est `Clemen5t/Coach_Fortnite_Ia`, branche `main`.
Il doit être **public** pour que le coach puisse télécharger les mises à jour sans
jeton GitHub, sans clé API et sans installation de Git.

Pour les futures corrections : modifier le code dans ce dépôt puis cliquer
**Mettre à jour** dans le coach (session arrêtée). Fermer puis relancer `Lancer.bat`
pour utiliser le nouveau code. Le bouton **Dépôt GitHub** reste disponible pour
changer de dépôt si nécessaire.

Les fichiers sont téléchargés depuis une révision précise, contrôlés puis remplacés.
Les fichiers existants sont sauvegardés dans `.updates/backup-*`.
Une erreur de copie déclenche une restauration ; une mise à jour interrompue
est restaurée au lancement suivant. Les réglages, l'environnement Python et
le modèle Ollama sont conservés. Les dépendances sont vérifiées par le lanceur.
La vérification de syntaxe ne garantit pas l'absence de bugs dans une nouvelle version.
Les modèles ne sont pas téléchargés à nouveau par le bouton de mise à jour.

## Publier une correction

Mettre à jour les fichiers et `VERSION`, puis enregistrer le commit dans `main`.
`update-manifest.json` identifie l'application et les fichiers distribués.
L'ajout de nouveaux fichiers doit aussi être prévu dans la liste autorisée de
l'updater déjà installé ; cette version prend en charge les fichiers existants.
Le premier raccordement peut installer le même numéro de version afin de mémoriser
la révision GitHub. Le contrôle suivant indiquera alors « Déjà à jour ».
Le dépôt ne doit contenir qu'un seul manifeste de cette application.

## Conseils moins répétitifs

Le modèle doit fournir une catégorie, un indice visible et une action précise.
Les phrases génériques de couverture sont filtrées. Une même catégorie est
bloquée pendant 30 secondes, et deux conseils sont espacés d'au moins 8 secondes.
Ce sont des heuristiques : la présence d'une explication n'est pas une preuve
que le modèle a correctement vu la scène. La qualité reste à vérifier en jeu.
La sortie structurée peut augmenter le délai par rapport aux versions précédentes.

## Tests

`python -m unittest -v test_local_ai test_updater`

18 tests : client local simulé, annulation, filtre, délai, archives de mise à jour,
conservation des réglages, restauration après erreur et après interruption.
Pas de test réel sur Windows/GPU ni de publication GitHub réalisée dans ce paquet.
Consulter `LIRE-MOI.txt` pour les détails du coach local.
