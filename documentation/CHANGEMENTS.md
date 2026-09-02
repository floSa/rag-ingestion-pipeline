# Ce qui a changé, et ce que ça implique

> **À lire en premier si vous reprenez le projet, ou si vous travaillez sur
> [`rag-agent-chat`](llm_integration_plan.md).**
>
> Ce document liste les changements de fond, ce qu'ils impliquent côté agent, et
> renvoie vers la page détaillée de chacun. Il ne remplace pas ces pages, il sert
> de point d'entrée.

---

## 1. Le modèle d'embedding a changé — action requise côté agent

| | Avant | Après |
|---|---|---|
| Modèle | `all-MiniLM-L6-v2` | **`paraphrase-multilingual-MiniLM-L12-v2`** |
| Dimensions | 384 | **384 — inchangé** |
| Langues | anglais | **français, anglais et une cinquantaine d'autres** |

### Pourquoi

L'ancien modèle n'était entraîné que sur de l'anglais. Sur un corpus mixte, il
classait **par langue avant de classer par sens**. Mesuré sur une question
française, face à six passages :

| Rang | Ancien modèle | Nouveau modèle |
|---|---|---|
| 1 | FR pertinent (0,453) | **EN pertinent (0,746)** |
| 2 | FR proche (0,433) | **FR pertinent (0,741)** |
| 3 | **FR hors sujet (0,397)** | FR proche (0,492) |
| 4 | **EN pertinent (0,366)** | EN proche (0,441) |
| 5 | EN proche (0,267) | FR hors sujet (0,338) |
| 6 | EN hors sujet (0,105) | EN hors sujet (0,313) |

Avec l'ancien modèle, un **hors-sujet français** passait devant la **bonne
réponse anglaise**. Poser sa question en français revenait à se couper de toute
la bibliothèque anglaise.

Avec le nouveau, les deux bonnes réponses arrivent en tête à 0,005 d'écart,
quelle que soit leur langue.

> Les scores sont des **similarités cosinus** : 1,0 = même sens, 0,7–0,8 = dit la
> même chose autrement, 0,4–0,5 = même domaine, 0 = aucun rapport. Ce qui compte
> n'est pas la valeur absolue mais l'écart entre candidats.

### Ce que `rag-agent-chat` doit faire

**Obligatoire, sans quoi les réponses seront fausses sans qu'aucune erreur
n'apparaisse** — la recherche renverra des passages au hasard :

```bash
EMBEDDING_MODEL_NAME=paraphrase-multilingual-MiniLM-L12-v2
```

La dimension étant identique (384), aucun autre changement n'est nécessaire :
ni schéma, ni format de collection, ni code de recherche.

> Détail et alternatives écartées : [base_vectorielle.md](base_vectorielle.md#pourquoi-un-modèle-dembedding-multilingue) *(l'ancre précédente, `#limite-mesurée--le-modèle-dembedding-ne-parle-quanglais`, ne correspondait à aucun titre de la cible. Le registre §6.14 nomme un seul renvoi mort vers cette ancre ; il y en avait deux, celui-ci et celui du `README.md`.)*

---

## 2. Le graphe a maintenant une vraie hiérarchie de titres

Avant, tout titre était rattaché au document : la chaîne s'arrêtait à
`élément → titre → document`. Un sous-titre et son chapitre étaient frères.

Désormais un titre est rattaché **au titre qui le domine** :

```
[0] A Developer's Approach to Data Cleaning
    [1] Understanding basic data cleaning
        [2] Common data issues
    [1] R and common data issues
        [2] Outliers
            [3] Step 1 – Profiling the data
```

| Mesure sur le corpus de référence *(disparu — voir la réserve)* | Avant | Après |
|---|---|---|
| Arêtes `SectionHeader → SectionHeader` | 0 | **759** |
| Chemins de longueur 3 depuis le document | 0 | **13 220** |
| Chemins de longueur 4 | 0 | 2 778 |
| Chemins de longueur 5 | 0 | 1 021 |

> **Ces chiffres portent sur le CORPUS DE REFERENCE, qui n'existe plus.** Le §1 du
> registre le declare mort : c'etait un corpus mixte francais/anglais de 42
> documents, dont 6 notes Markdown et un PDF de 280 pages. Le corpus actuel est
> **24 chapitres HTML de deux ouvrages plus un PDF de 71 pages**, entierement en
> anglais, et `Datas/mds/` est vide. Aucun de ces nombres n'est reproductible
> aujourd'hui (registre §6.10, §6.11).
>
> Ils sont **conserves plutot que supprimes**, avec cette reserve : ils
> documentent la DECISION prise a l'epoque, et l'effacer laisserait le choix sans
> motif. Ce qu'ils ne sont pas, c'est une description de l'index d'aujourd'hui.
> Les chiffres du corpus actuel se lisent par `python -m src.index_report` et
> `python -m src.verify_contract`, dans le conteneur d'extraction.

> Sur le corpus **actuel**, la question « le graphe est-il plat ? » a été
> mesurée : il ne l'est pas, 21 chapitres sur 22 s'imbriquent, et le seul plat
> l'est pour une raison qui vient de sa capture et non du code (registre §3.2).
> Le contrat côté agent annonçait `0` et `0` sur ces deux lignes : ce n'est pas
> une contradiction, il mesurait un graphe produit par autre chose que ce code.

### Ce que ça change pour l'agent

- `reference_id` d'un titre ne vaut plus systématiquement `DOC` ; il désigne
  souvent un autre titre. **Une remontée récursive est désormais utile** : on
  peut reconstruire « chapitre > section > sous-section » pour contextualiser
  une citation.
- Nouvelle clé `depth` sur chaque chunk : profondeur dans la hiérarchie, 0 pour
  un titre de tête. *(Cette ligne ajoutait « plafonnée à 3 ». Le plafond a été
  retiré par le lot 3 — registre §4.24 — et la profondeur atteint 5 sur le
  corpus actuel. Le site canonique de la règle, et des deux échelles qui s'y
  croisent, est `ChunkMetadata.depth` dans `src/pipeline/schemas.py`.)*
- Rien ne casse si l'agent l'ignore : `reference_id` reste un identifiant
  d'élément valide.

> Règle, signaux par format et garde-fous :
> [extraction_donnees.md](extraction_donnees.md#4-hierarchie-et-positions)

---

## 3. Le découpage est confié à Docling

Le découpage maison coupait à la longueur en caractères. C'est désormais
`HybridChunker`, le découpeur de Docling, qui s'en charge : il respecte la structure
du document et reçoit **le tokenizer du modèle d'embedding lui-même**.

| Mesure, sur le chapitre 1 de *Practical MLOps* *(ouvrage absent du corpus actuel)* | Découpage maison | `HybridChunker` |
|---|---|---|
| Chunks | 146 | **100** |
| Tokens, médiane | 67 | **91** |
| Caractères, médiane | 269 | **353** |

À contenu égal, quarante-six chunks de moins, chacun portant davantage de contexte.

> **`Practical MLOps` n'est pas dans le corpus.** Les deux ouvrages actuels sont
> *MLOps with Databricks* et *Practical MLflow for Generative AI on Databricks* —
> les noms se ressemblent, et c'est précisément pourquoi la réserve est écrite ici
> plutôt que supposée. Cette comparaison n'est donc pas rejouable (registre
> §6.10) ; elle documente la décision de confier le découpage à Docling, et le
> **découpage maison a depuis été retiré du dépôt** (registre §5.1), ce qui rend
> la colonne de gauche définitivement non reproductible.

**Rien ne change pour l'agent.** Les identifiants restent les nôtres : chaque chunk
est rattaché à l'élément d'où part sa lecture, et un élément réparti sur plusieurs
chunks leur donne les suffixes `#0`, `#1` que le contrat prévoit déjà.

> [extraction_donnees.md](extraction_donnees.md#ce-qui-part-dans-lindex-vectoriel)

---

## 4. Nouvelles métadonnées de chunk

Trois clés se sont ajoutées à `ChunkMetadata`
([`src/pipeline/schemas.py`](../src/pipeline/schemas.py), qui reste le contrat de
référence) :

| Clé | Contenu | Usage côté agent |
|---|---|---|
| `language` | `en`, `fr`… vide si indéterminée | filtrer ou annoncer la langue des sources |
| `depth` | profondeur dans la hiérarchie des titres | reconstruire le fil des titres parents |
| `collection` | l'ouvrage dont vient le chapitre | citer le livre, pas seulement le fichier |

Toutes sont optionnelles : un agent qui les ignore fonctionne comme avant.

> [base_vectorielle.md](base_vectorielle.md#structure-et-définition-des-données)

---

## 5. Ce qui n'est plus ingéré

| Écarté | Pourquoi |
|---|---|
| Index, sommaire, couverture, page de copyright | aucune phrase à indexer, mais tout le vocabulaire de l'ouvrage : ils ressortaient sur presque toutes les questions |
| Doublons exacts | un même fichier déposé sous deux noms n'est ingéré qu'une fois (empreinte SHA-256) |
| PDF scannés | passés à l'OCR au lieu d'être refusés |

**Préface, glossaire et annexes sont conservés**, volontairement : c'est de la
prose, et un glossaire répond bien aux questions de définition.

> [README.md](../README.md#ce-qui-nest-pas-ingéré--index-sommaire-pages-liminaires)

---

## 6. Où trouver quoi

| Question | Document |
|---|---|
| Contrat de données avec l'agent | [llm_integration_plan.md](llm_integration_plan.md) |
| Métadonnées de chunk, modèle d'embedding | [base_vectorielle.md](base_vectorielle.md) |
| Hiérarchie, découpage, nettoyage HTML | [extraction_donnees.md](extraction_donnees.md) |
| Modèle de graphe et requêtes nGQL | [graphe_connaissances.md](graphe_connaissances.md) |
| Temps d'ingestion, cadencement | [orchestration.md](orchestration.md) |
| Vue d'ensemble, quickstart | [README.md](../README.md) |

---

## 7. Après un changement de modèle ou de règle d'extraction

Les vecteurs et le graphe doivent être reconstruits.

> **Piège à connaître.** `docker compose restart` **ne relit pas le fichier `.env`** : il
> relance le conteneur avec l'environnement qu'il avait déjà. Après avoir changé une
> variable, il faut **recréer** le conteneur, sinon l'ingestion tourne silencieusement avec
> l'ancienne valeur — rien dans les logs ne le signale sauf la ligne
> `Chargement du modele d'embedding ...`.

```bash
# 1. Recréer le conteneur pour qu'il prenne le nouveau .env
docker compose up -d --force-recreate docling-service

# 2. Vérifier que la variable est bien celle attendue
docker compose exec docling-service printenv EMBEDDING_MODEL_NAME

# 3. Purger, puis relancer l'ingestion depuis Dagster
docker compose exec -w /app -e PYTHONPATH=/app docling-service python src/wipe_stores.py
docker compose restart docling-service
```

Puis relancer l'ingestion depuis Dagster. Deux outils pour contrôler le résultat :

```bash
docker compose exec -w /app -e PYTHONPATH=/app docling-service python src/index_report.py
docker compose exec -w /app -e PYTHONPATH=/app docling-service python src/verify_contract.py
```

Le premier donne volume, qualité, langues et profondeur de hiérarchie. Le second
vérifie que le contrat avec `rag-agent-chat` est respecté.
