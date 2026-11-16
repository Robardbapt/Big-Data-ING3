# Script de la démo (environ 7 minutes)

## Avant de passer

```bash
docker compose up --build -d
```

Lancer la stack **5 minutes avant** la présentation : le chantier aura déjà avancé (environ 15 %), et on aura de
l'historique à rejouer. Ouvrir deux onglets : http://localhost:8000 (jumeau) et http://localhost:8080 (Kafka UI).

Sans Docker : `python -m construct_ai demo --speed 10`. Tout fonctionne hors ligne, three.js est embarqué.

## Déroulé

| Minute | À l'écran | À dire |
|---|---|---|
| 0:00 | Vue globale, LIVE | « Voici un chantier : 2 maçons, 2 manœuvres, 1 chef. On voit chaque ouvrier en squelette 3D, reconstruit à partir de 4 caméras fixes et de 5 caméras portées au torse. » |
| 0:45 | Panneau Équipe | « Personne n'a déclaré son rôle : l'IA le déduit de ce que fait chaque personne. Même chose pour l'action en cours. » |
| 1:15 | Les parpaings qui apparaissent en jaune | « Chaque parpaing posé est détecté par la body-cam et rattaché à son emplacement dans la maquette BIM. L'avancement se calcule tout seul, avec une date de fin estimée. » |
| 2:00 | Bouton **Suivre** puis **POV** | « On peut suivre un ouvrier, ou se mettre à sa place : c'est exactement le point de vue qu'un robot devra apprendre. » |
| 2:45 | Panneau **Pipeline Big Data** + Kafka UI | « Environ 700 messages par seconde arrivent de façon asynchrone. Les body-cams perdent le wifi et renvoient leurs données en rafale. On fusionne en *event time* avec un watermark : X messages sont arrivés dans le désordre, Y trop tard, et ceux-là partent dans le batch. » Dans Kafka UI : montrer les topics, les partitions et les consumer groups. |
| 3:45 | Boutons **Caméras** + **Heatmap** | « Les champs de vision des caméras. Quand les murs montent, les caméras perdent les ouvriers et l'odométrie de la body-cam prend le relais. La heatmap montre les zones les plus fréquentées. » |
| 4:15 | Timeline → glisser au début | « Tout est stocké dans un data lake Parquet. On peut rejouer le chantier depuis la première minute. » Lancer en x20. |
| 5:00 | **Analytics** | « Ce sont des requêtes SQL DuckDB directement sur les fichiers Parquet : temps par activité, cadence de pose, attente, alertes de posture. C'est la valeur pour l'entreprise de BTP. » |
| 5:45 | **Dataset robotique** → Télécharger | « Et voici ce qu'on vend : chaque geste devient un épisode au format LeRobot, le standard de Hugging Face. Pose 3D, cible BIM, description de la tâche. Aucun visage, aucun nom. » |
| 6:30 | Retour LIVE, conclusion | « Une seule collecte, deux clients : le BTP pilote ses chantiers, les roboticiens entraînent leurs robots. » |

## Questions probables

* **« C'est simulé ? »** Oui, le chantier l'est, et c'est voulu : la simulation fournit une vérité terrain qui
  permet de **mesurer** la précision du pipeline (92 % sur les actions). Mais le pipeline est indépendant de la
  source : `construct_ai/vision/video_adapter.py` envoie une vraie vidéo (MediaPipe) dans les mêmes topics.
* **« Et le RGPD ? »** La vidéo reste à l'edge, les visages sont floutés, seuls des squelettes pseudonymisés
  circulent, et les KPI sont agrégés. Voir [PITCH.md](PITCH.md).
* **« Pourquoi Kafka ? »** Pour découpler producteurs et consommateurs : on ajoute un traitement en créant un
  nouveau consumer group, sans toucher aux capteurs. Kafka sert aussi de buffer quand un service tombe, et les
  données sont rejouables pendant 7 jours.
* **« Pourquoi pas Spark ? »** Le volume de la démo tient sur un portable. L'architecture est celle de Spark
  Structured Streaming / Flink (fenêtres, watermark, médaillon), et le passage en production est décrit dans
  [ARCHITECTURE.md](ARCHITECTURE.md).
