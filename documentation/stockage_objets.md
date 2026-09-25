# Stockage d'objets

## Présentation du service
Le stockage objet conserve les médias récupérés durant l'ingestion : images et tableaux rendus en PNG. Il permet à un RAG multimodal de rendre des réponses qui incluent les images de la source.

Il expose une API S3 et héberge l'ensemble des éléments non textuels, de manière à alléger les autres bases de données. Le serveur est SeaweedFS. Le code ne le nomme pas : le pipeline et l'agent parlent à une **passerelle S3** par un client générique, et seule la variable `S3_ENDPOINT` désigne le serveur. Changer de serveur ne touche donc à aucune ligne de téléversement.

## Accès au service
- **Serveur API interne** : la valeur de `S3_ENDPOINT` — `seaweedfs:8333` sur la pile en service. Il n'est ni publié sur l'hôte, ni accompagné d'une console : il n'est joignable que depuis `rag_network`.
- **Identifiants** : voir `.env`. **Deux jeux aux droits distincts** : le pipeline écrit, l'agent ne fait que lire. Avec un seul jeu partagé, tout lecteur pourrait effacer le corpus d'images.

La fiche de référence — variables, bucket, droits, diagnostic — est [`services/stockage_objet.md`](services/stockage_objet.md). Les tableaux n'existent que là ; cette page-ci dit à quoi le service sert.

## Structure et définition des données
Les objets déposés sont uniquement visuels (`image/png` par exemple). Leur clé suit l'arborescence `images/{NomDuFichier}/{element_id}_{type}.png` pour les crops PDF, `images/html/…` et `images/md/…` pour les images HTML et Markdown.
- **Exemple** : `images/statisticsfordatascience/3af24_picture.png`
- **Bucket principal** : le service d'extraction vérifie l'existence du bucket **`documents`** à son démarrage et le crée s'il manque.

Le graphe NebulaGraph et les métadonnées ChromaDB portent **deux** champs pour chaque élément `Table` ou `Picture` :

- `media_url` — l'adresse, en style chemin, `http://<S3_ENDPOINT>/<bucket>/<clé>` ;
- `object_key` — la clé nue, celle passée à `put_object`.

**L'adresse est interne et authentifiée, jamais publique** : un `GET` anonyme y rend 403, y compris depuis un conteneur du réseau. L'agent sert de proxy. **La clé survit à l'adresse** : l'adresse porte l'hôte et devient fausse quand il change ; la clé est l'identité de l'objet.

## Commandes utiles
- **État du store** (adresse interrogée et nombre d'objets) :
  ```bash
  docker compose exec docling-service python -m src.verify_data
  ```
- **Journaux du service** :
  ```bash
  docker compose logs seaweedfs --tail 50
  ```
- **Éprouver les droits**, jeu par jeu et critère par critère, par appel direct : `scripts/campagne/essayer-la-passerelle-s3.py`. Un refus S3 remonte chez l'agent en **404 silencieux** : l'image a simplement l'air absente. Ces droits ne se contrôlent donc pas à l'écran.

## Problèmes rencontrés et solutions
- **Défaillance silencieuse au démarrage (images fantômes)**
  - *Problème* : le stockage pouvait être injoignable pendant les premières secondes, ou son nom de service Docker non encore résolu. Le service d'extraction échouait sans réessayer.
  - *Solution* : `images.ensure_bucket()` réessaie 15 fois, espacées de 5 secondes, et journalise l'adresse visée à chaque tentative.
- **Une adresse par défaut qui survit au serveur qu'elle désigne**
  - *Problème* : `python -m src.wipe_stores` lit les réglages S3 et **vide** le bucket qu'ils désignent. Avec une adresse par défaut écrite dans le code, un lancement sans `.env` purgerait un autre serveur que celui de la pile, en rendant compte d'une purge réussie.
  - *Solution* : `S3_ENDPOINT`, `S3_ACCESS_KEY` et `S3_SECRET_KEY` n'ont **aucun défaut**. Sans elles, la construction des réglages échoue avec un message qui nomme la variable manquante, avant qu'aucun client ne soit construit, donc avant toute suppression (`src/reglages_s3.py`).
