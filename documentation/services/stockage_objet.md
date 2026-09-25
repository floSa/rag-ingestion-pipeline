# Stockage objet (passerelle S3)

## Role

Stockage objet S3-compatible pour les medias extraits des documents (images
croppees, tableaux en PNG). Leur adresse et leur cle sont referencees dans le
graphe de connaissances et dans les metadonnees ChromaDB.

Le serveur est SeaweedFS (depuis le 25 septembre 2026). Le code ne le nomme
pas : il parle a une passerelle S3, et seule `S3_ENDPOINT` la designe.

## Container

- `seaweedfs` : image `chrislusf/seaweedfs:3.80`, port interne 8333 (passerelle
  S3). Ni console, ni port publie : le service n'est joignable que depuis
  `rag_network`.

La passerelle exige un fichier d'identites (`-s3.config`). Ce fichier est ecrit
au demarrage dans un tmpfs du conteneur, a partir des variables du `.env`. Rien
n'est monte depuis le depot, et aucune cle n'y est ecrite.

## API

API S3 standard. Le depot la consomme par un client S3 generique (bibliotheque
Python `minio`), construit a un seul endroit :
`src/docling_service/images.py:build_client`. Aucun autre module de `src/` ne
nomme la bibliotheque cliente.

## Bucket

- `documents` : bucket principal, cree au demarrage du service d'extraction s'il
  n'existe pas.
  - crops PDF : `images/{filename_stem}/{element_id}_{type}.png`
  - images Markdown : `images/md/{doc_key}/{rang}_{nom}`
  - images de captures HTML : `images/html/{doc_key}/img_{rang}.{ext}`

## Ce que le contrat publie

Deux champs, sur les sommets visuels du graphe comme sur les chunks ChromaDB :

| Champ        | Ce que c'est                                                  |
|--------------|---------------------------------------------------------------|
| `media_url`  | `http://<S3_ENDPOINT>/<bucket>/<cle>`, en style chemin         |
| `object_key` | la cle nue, celle passee a `put_object`, sans reencodage       |

**L'adresse est interne et authentifiee, jamais publique.** Un `GET` anonyme y
rend 403, y compris depuis un conteneur de `rag_network`. L'hote est un nom de
service Docker qui ne se resout pas hors de ce reseau. `rag-agent-chat` sert de
proxy : il lit l'objet avec son jeu d'identifiants en lecture seule et le
re-sert. Il ne transmet jamais cette adresse a un navigateur.

**La cle survit a l'adresse.** L'adresse porte l'hote, donc elle devient fausse
quand le stockage change d'hote : c'est arrive pour 212 objets le 25 septembre
2026. La cle est l'identite de l'objet. Un consommateur qui veut relire un
objet, le compter ou le rapprocher d'un listing utilise `object_key` sans avoir
a decomposer l'adresse.

## Variables d'environnement

Ce que le client presente :

| Variable        | Description        | Defaut      |
|-----------------|--------------------|-------------|
| `S3_ENDPOINT`   | `hote:port`        | **aucun** (exige) |
| `S3_BUCKET`     | Nom du bucket      | `documents` |
| `S3_ACCESS_KEY` | Cle d'acces        | **aucun** (exige) |
| `S3_SECRET_KEY` | Cle secrete        | **aucun** (exige) |

**Sans `S3_ENDPOINT` ni identifiants, les reglages refusent de se construire**,
avec un message qui nomme la variable manquante (`src/reglages_s3.py`). Le
refus a lieu avant la construction de tout client. C'est voulu :
`python -m src.wipe_stores` lit ces memes reglages et vide le bucket qu'ils
designent. Une adresse par defaut lui ferait purger un autre stockage que
celui de la pile en rendant compte d'une purge reussie.

`S3_ACCESS_KEY` et `S3_SECRET_KEY` ne sont pas ecrites dans le `.env` :
`docker-compose.yml` les derive du jeu RW ci-dessous, pour `docling-service`,
`dagster-webserver` et `dagster-daemon`. Une seule valeur, deux noms, un seul
endroit qui la porte.

Les identites du serveur :

| Variable                  | Droits                                 |
|---------------------------|----------------------------------------|
| `SEAWEEDFS_RW_ACCESS_KEY` | `Admin`, `Read`, `Write`, `List`, `Tagging` |
| `SEAWEEDFS_RW_SECRET_KEY` | idem                                   |
| `SEAWEEDFS_RO_ACCESS_KEY` | `Read`, `List`                         |
| `SEAWEEDFS_RO_SECRET_KEY` | idem                                   |

**Deux jeux aux droits distincts.** Le pipeline ecrit (`make_bucket` exige
`Admin`) ; `rag-agent-chat` ne fait que lire. Avec un seul jeu partage, tout
lecteur pourrait effacer le corpus d'images.

Un refus se presente au client comme un 403, et l'agent le rend en 404
silencieux : ces droits ne se controlent pas a l'ecran. Ils se controlent par
appel direct avec `scripts/campagne/essayer-la-passerelle-s3.py`, qui fait,
pour chaque critere et chaque jeu, l'appel que le pipeline ou l'agent ferait
(mode d'emploi dans l'en-tete du script).

## Dependances

Aucune (service autonome).

## Persistence

Volume : `./Datas/database/seaweedfs:/data`

## Diagnostic

```bash
docker compose logs seaweedfs --tail 50
docker compose exec docling-service python -m src.verify_data
```

`verify_data` affiche l'adresse qu'il interroge a cote du compte d'objets.

La sonde du conteneur interroge `http://seaweedfs:8333/healthz`. Pas `localhost` :
`-ip=seaweedfs` fait ecouter le serveur sur cette seule adresse. Pas `/` : la
racine S3 non authentifiee rend un 403, ce qui est le comportement attendu mais
ferait echouer la sonde en permanence.
