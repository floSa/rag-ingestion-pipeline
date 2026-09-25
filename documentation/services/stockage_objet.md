# Stockage objet (passerelle S3)

## Role

Stockage objet S3-compatible pour les medias extraits des documents (images
croppees, tableaux en PNG). Leur adresse et leur cle sont referencees dans le
graphe de connaissances et dans les metadonnees ChromaDB.

Le stockage objet etait MinIO jusqu'au 25 septembre 2026 ; remplace par
SeaweedFS.

## Container

- `seaweedfs` : image epinglee, port interne 8333 (passerelle S3). Ni console,
  ni port publie : le service n'est joignable que depuis `rag_network`.

Le fichier d'identites que la passerelle exige (`-s3.config`) est ECRIT AU
DEMARRAGE dans un tmpfs du conteneur, a partir des variables du `.env`. Rien
n'est monte depuis le depot, et aucune cle n'y est ecrite.

## API

API S3 standard. Le depot la consomme par un client S3 generique, construit a
UN SEUL SITE : `src/docling_service/images.py:build_client`. Rien d'autre dans
`src/` ne nomme la bibliotheque cliente, et rien nulle part ne nomme le
serveur — seul `S3_ENDPOINT` le designe.

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
| `media_url`  | `http://<S3_ENDPOINT>/<bucket>/<cle>`, en style CHEMIN         |
| `object_key` | la cle NUE, celle passee a `put_object`, sans reencodage       |

**L'adresse est INTERNE et AUTHENTIFIEE, jamais publique.** Un `GET` anonyme y
rend 403, y compris depuis un conteneur de `rag_network`, et l'hote est un nom
de service Docker qui ne resout pas au-dehors. `rag-agent-chat` est le PROXY :
il lit l'objet avec son jeu d'identifiants en lecture seule et le re-sert. Il ne
passe jamais cette adresse a un navigateur.

**La cle est la : l'adresse porte l'hote, donc elle perime.** Elle a change pour
212 objets d'un coup le 25 septembre 2026. La cle, elle, est l'identite de
l'objet et survit au deplacement du stockage — un consommateur qui veut le
relire, le compter ou le rapprocher d'un listing n'a plus a defaire l'adresse
lui-meme.

## Variables d'environnement

Ce que le CLIENT presente :

| Variable        | Description        | Defaut      |
|-----------------|--------------------|-------------|
| `S3_ENDPOINT`   | `hote:port`        | **AUCUN**   |
| `S3_BUCKET`     | Nom du bucket      | `documents` |
| `S3_ACCESS_KEY` | Cle d'acces        | (vide)      |
| `S3_SECRET_KEY` | Cle secrete        | (vide)      |

**`S3_ENDPOINT` N'A AUCUNE VALEUR PAR DEFAUT, et le service REFUSE DE DEMARRER
si elle manque.** Elle en avait une — l'adresse du stockage d'alors, ecrite dans
le code, a deux sites — et cette adresse a survecu au stockage qu'elle
designait. Or `python -m src.wipe_stores` lit ces memes reglages et VIDE le
bucket qu'ils designent : sans `.env`, il aurait purge l'ancien stockage en
rendant compte d'une purge reussie. Un defaut absent fait echouer le demarrage ;
un defaut faux fait REUSSIR la purge du mauvais stockage. Voir
`src/reglages_s3.py`.

`S3_ACCESS_KEY` et `S3_SECRET_KEY` ne sont pas ecrites dans le `.env` :
`docker-compose.yml` les DERIVE du jeu RW ci-dessous. Une seule valeur, deux
noms, un seul endroit qui la porte.

Les identites du SERVEUR :

| Variable                  | Droits                                 |
|---------------------------|----------------------------------------|
| `SEAWEEDFS_RW_ACCESS_KEY` | `Admin`, `Read`, `Write`, `List`, `Tagging` |
| `SEAWEEDFS_RW_SECRET_KEY` | idem                                   |
| `SEAWEEDFS_RO_ACCESS_KEY` | `Read`, `List`                         |
| `SEAWEEDFS_RO_SECRET_KEY` | idem                                   |

**DEUX JEUX AUX DROITS DISTINCTS, et c'est le point.** Le pipeline ecrit
(`Admin` est ce que `make_bucket` exige), `rag-agent-chat` ne fait que lire. Un
seul jeu partage rendait toute lecture capable d'effacer le corpus d'images.

Un refus se presente au client comme un 403, et l'agent le rend en 404
SILENCIEUX : ces droits ne se controlent pas a l'ecran. Ils se controlent par
appel direct — `scripts/campagne/essayer-la-passerelle-s3.py` fait, pour chaque
critere et chaque jeu, l'appel que le pipeline ou l'agent ferait, et lit le refus
dans l'exception.

## Dependances

Aucune (service autonome).

## Persistence

Volume : `./Datas/database/seaweedfs:/data`

## Diagnostic

```bash
docker compose logs seaweedfs --tail 50
docker compose exec docling-service python -m src.verify_data
```

`verify_data` affiche l'adresse qu'il interroge a cote du compte d'objets. Ce
bloc nommait auparavant un produit ecrit dans le code : il l'aurait nomme a
l'identique en interrogeant un tout autre serveur.

La sonde du conteneur porte sur `http://seaweedfs:8333/healthz`, et non sur `/`
ni sur `localhost` : `-ip=seaweedfs` fait ECOUTER le serveur sur cette
adresse-la seule, et la racine S3 non authentifiee rend un **403**, ce qui est
le bon comportement et rendrait la sonde eternellement rouge.
