# ConStruct-AI : le pitch

## En une phrase

**ConStruct-AI filme les chantiers pour en faire un jumeau numérique 4D. Les entreprises du BTP s'en servent
pour piloter leurs chantiers, et les fabricants de robots achètent les gestes des ouvriers pour entraîner leurs
robots.**

## Le problème

1. **Les robots humanoïdes et de maçonnerie manquent de données.** Pour qu'un robot apprenne un geste par imitation,
   il lui faut des milliers de démonstrations humaines annotées : pose du corps, trajectoire des mains, objet
   manipulé, contexte, résultat attendu. Ce type de données se collecte aujourd'hui en laboratoire (motion capture,
   téléopération), ce qui coûte cher et ne ressemble pas à un vrai chantier : poussière, météo, matériaux,
   imprévus, coordination entre plusieurs personnes.
2. **Les chantiers sont mal mesurés.** Le suivi d'avancement se fait souvent à la main et en retard. On mesure mal
   les temps d'attente, les déplacements inutiles et les postures à risque (TMS).

## La solution

| Étape | Ce qu'on fait |
|---|---|
| **Capter** | Caméras fixes sur mâts et caméras POV portées au torse. L'inférence (pose 3D, floutage) tourne **sur place**. |
| **Fusionner** | Un pipeline temps réel reconstruit, pour chaque ouvrier, sa position, son squelette 3D, son action et son rôle, puis rattache chaque parpaing posé à la maquette **BIM**. |
| **Rejouer** | Un jumeau numérique 4D montre le chantier en direct ou rejoué de A à Z, vue globale ou vue POV. |
| **Valoriser** | Chaque geste devient un épisode de démonstration au format standard de la robotique (LeRobot), avec sa tâche en langage naturel et sa cible BIM. |

## Deux marchés, une seule collecte

### 1. Les entreprises du BTP : elles fournissent les données
Elles reçoivent un tableau de bord gratuit ou très bon marché :
* avancement automatique par rapport au BIM, cadence, date de fin estimée ;
* temps d'attente et goulots logistiques (par exemple : le maçon attend ses parpaings) ;
* prévention : alertes de posture à risque, zones fréquentées (heatmap) ;
* replay en cas de litige ou d'incident.

En échange, elles cèdent sous licence les données anonymisées (squelettes et événements, jamais d'images).

### 2. Les roboticiens : ils paient
Fabricants d'humanoïdes, de robots de maçonnerie et de bras de chantier, laboratoires d'IA incarnée.
* **Licence de dataset** à l'heure de démonstration ou à l'épisode, avec un prix par tâche (pose de parpaing,
  manutention, gâchage…).
* **Abonnement** au flux de nouveaux épisodes (nouveaux chantiers, nouveaux matériaux, nouvelles tâches).
* **Collecte sur mesure** : « on veut 10 000 épisodes de pose de plaques de plâtre ».

C'est un modèle **B2B2B** : les données collectées chez le BTP sont revendues à la robotique. Les données sont
d'autant plus précieuses qu'elles sont **réelles**, **massives** et **contextualisées**. Le simulateur de laboratoire
n'a aucun de ces trois atouts.

## Pourquoi c'est un sujet Big Data

* **Volume** : un chantier de 20 ouvriers filmés produit de l'ordre de 300 Go de vidéo par jour (voir
  [ARCHITECTURE.md](ARCHITECTURE.md)). La valeur est dans la réduction : vidéo → squelettes → épisodes.
* **Vélocité** : des flux continus, multi-sources, **asynchrones et désordonnés** (latences, coupures wifi).
* **Variété** : vidéo, poses 3D, odométrie, interactions, BIM, planning.
* **Valeur** : le même flux brut alimente à la fois un produit SaaS et un produit data.
* **Véracité** : la fusion multi-capteurs et le rattachement BIM rendent la donnée fiable. Dans la démo, on
  mesure **~92 % de précision** sur les actions, **~11 cm** d'erreur de position et **100 %** sur les rôles.

## Concurrence et positionnement

* Suivi d'avancement par capture 360° ou drone : ces outils mesurent **le bâtiment**, pas **les gestes**.
* Motion capture et téléopération en laboratoire : données propres, mais peu nombreuses et hors contexte.
* **ConStruct-AI** : la seule source de démonstrations humaines **réelles, à grande échelle, rattachées au BIM**.

## Éthique et RGPD (point clé de la soutenance)

* **Privacy by design** : la vidéo ne quitte jamais l'edge. Les visages sont floutés. Seuls des squelettes et des
  événements pseudonymisés (badge `H-107`) sont transmis.
* **Finalité limitée** : on vend des gestes, pas des personnes. Le dataset ne contient aucun identifiant réel.
* **Pas de flicage individuel** : les KPI remontés au client sont agrégés par équipe ou par poste. Une analyse
  d'impact (AIPD), l'information des représentants du personnel et le consentement des ouvriers porteurs de
  body-cams sont des prérequis.
* Contrepartie pour les ouvriers : prévention des TMS et, en perspective, des robots qui prennent les tâches les
  plus pénibles.

## Feuille de route

1. **POC (ce dépôt)** : chantier simulé, pipeline complet, jumeau 4D, export dataset, adaptateur vidéo réel.
2. **Pilote** : 1 chantier, 4 caméras et 5 body-cams, inférence sur boîtier GPU embarqué, modèle d'action
   entraîné sur nos propres données (remplace le classifieur à règles).
3. **Industrialisation** : Kafka managé, Flink, lakehouse Iceberg sur S3, place de marché de datasets.
