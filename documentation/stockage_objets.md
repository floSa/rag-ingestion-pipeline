# Stockage d'objets

## Présentation du service
Le stockage objet est le coffre-fort de tous les médias lourds récupérés durant l'ingestion (notamment images et tableaux visuels complexes). Ce composant compte pour la suite : un RAG multimodal pourra rendre des réponses qui incluent directement les images de la source.

Il expose une API S3 et héberge l'ensemble des éléments non textuels, de manière à alléger les autres bases de données. Le dépôt ne le nomme nulle part : le pipeline et l'agent parlent à une **passerelle S3**, par un client générique, et seule la variable `S3_ENDPOINT` désigne le serveur en face. C'est ce qui a permis d'en changer le 25 septembre 2026 sans qu'une ligne de téléversement bouge.

*L'historique du remplacement tient en une ligne, et elle vit à un seul site : [`services/stockage_objet.md`](services/stockage_objet.md). Recopiée, elle finirait par diverger — c'est ce qui est arrivé à tous les autres chiffres de ce dépôt.*

## Accès au service
- **Serveur API interne** : la valeur de `S3_ENDPOINT` — `seaweedfs:8333` sur la pile en service. Il n'est ni publié sur l'hôte, ni accompagné d'une console : il n'est joignable que depuis `rag_network`.
- **Identifiants** : voir `.env`. **Deux jeux aux droits distincts**, et c'est le point : le pipeline écrit, l'agent ne fait que lire. Un seul jeu partagé rendait toute lecture capable d'effacer le corpus d'images.

La fiche de référence — variables, bucket, droits, diagnostic — est [`services/stockage_objet.md`](services/stockage_objet.md). Elle est le seul site des tableaux ; cette page-ci dit à quoi le service sert.

## Structure et définition des données
Les objets déposés sont uniquement visuels (`image/png` par exemple). Leur clé respecte l'arborescence `images/{NomDuLivre}/{Hash_ID}_{Type_element}.png` pour les crops PDF, `images/html/…` et `images/md/…` pour les deux autres chemins d'image.
- **Exemple** : `images/statisticsfordatascience/3af24_picture.png`
- **Bucket principal** : le service d'extraction vérifie l'existence du bucket **`documents`** à son démarrage et le crée s'il manque.

Le graphe NebulaGraph porte **deux** propriétés sur chaque élément `Table` ou `Picture` :

- `media_url` — l'adresse, en style chemin, `http://<S3_ENDPOINT>/<bucket>/<clé>` ;
- `object_key` — la clé nue, celle passée à `put_object`.

**L'adresse est interne et authentifiée, jamais publique** : un `GET` anonyme y rend 403, y compris depuis un conteneur du réseau. L'agent est le proxy. **Et la clé est là parce que l'adresse périme** : elle porte l'hôte, qui a changé pour 212 objets d'un coup lors de la bascule. La clé, elle, est l'identité de l'objet et lui survit.

Ces deux propriétés se sont longtemps appelées d'un seul nom, celui du produit qui stockait les octets. Un contrat public nomme ce qu'il publie, pas le logiciel qui le sert.

## Commandes utiles
- **État du store** (adresse interrogée et nombre d'objets) :
  ```bash
  docker compose exec docling-service python -m src.verify_data
  ```
- **Journaux du service** :
  ```bash
  docker compose logs seaweedfs --tail 50
  ```
- **Éprouver les droits**, jeu par jeu et critère par critère, par appel direct : `scripts/campagne/essayer-la-passerelle-s3.py`. Un refus S3 remonte chez l'agent en **404 silencieux** — l'image a simplement l'air absente. Ces droits ne se contrôlent donc jamais à l'écran.

## Problèmes rencontrés et solutions
- **Défaillance silencieuse au démarrage (images fantômes)**
  - *Problème* : le stockage pouvait être injoignable pendant les premières secondes, ou son nom de service Docker non encore résolu. Le service d'extraction échouait sans réessayer.
  - *Solution* : `images.ensure_bucket()` réessaie 15 fois, espacées de 5 secondes, et journalise l'adresse visée à chaque tentative.
- **Une adresse par défaut qui survit au serveur qu'elle désigne**
  - *Problème* : `S3_ENDPOINT` avait une valeur par défaut, écrite dans le code à deux sites. Elle est restée juste tant qu'elle a désigné le stockage en service. Or `python -m src.wipe_stores` lit ces mêmes réglages et **vide** le bucket qu'ils désignent : lancé sans `.env`, il aurait purgé le mauvais serveur en rendant compte d'une purge réussie.
  - *Solution* : le défaut a été **retiré**. Sans `S3_ENDPOINT`, la construction des réglages échoue, avec un message qui dit quoi faire — avant qu'aucun client ne soit bâti, donc avant toute suppression. *Un défaut absent fait échouer le démarrage ; un défaut faux fait réussir la purge du mauvais stockage.*
