# NebulaGraph (Graphe de connaissances)

## Role

Base de donnees graphe distribuee stockant la hierarchie structurelle des documents :
noeuds (Document, SectionHeader, Paragraph, Table, Picture...) et relations
(PARENT_OF, LINKED_TO).

## Containers

| Container       | Image                         | Port interne | Role                     |
|-----------------|-------------------------------|--------------|--------------------------|
| metad           | nebula-metad:v3.6.0           | 9559         | Service de metadonnees   |
| storaged        | nebula-storaged:v3.6.0        | 9779         | Stockage distribue       |
| graphd          | nebula-graphd:v3.6.0          | 9669         | Moteur de requete nGQL   |
| nebula-studio   | nebula-graph-studio:v3.8.0    | 7001 (expose)| UI de visualisation      |

## Schema nGQL

Le schema est cree par le service Docling au demarrage (`init_schema()` dans
`src/docling_service/nebula.py`). Les sites canoniques sont dans
`src/docling_service/ngql.py` : `create_space_statement()`, `VID_MAX_BYTES`,
`DOCUMENT_PROPERTIES`, `VERTEX_PROPERTIES` et `VERTEX_TYPES`. Le bloc ci-dessous
les reproduit pour lecture.

**`vid_type` vaut 256 octets.** Les identifiants de document du corpus vont de
**38** a **111** octets (mesure le 2 septembre 2026 sur le graphe vivant,
`MATCH (v:Document) RETURN id(v)` : 23 identifiants, dont **16** au-dessus de
64). Un space cree a 64 refuse ces documents : « *Storage Error: The VID must be
a 64-bit integer or a string fitting space vertex id length limit* ». Nebula ne
sait pas modifier un `vid_type` : le changer impose une purge complete des
stores.

```ngql
CREATE SPACE rag_space(partition_num=10, replica_factor=1, vid_type=FIXED_STRING(256));

-- Tags (types de noeuds)
CREATE TAG Document(filename string, type_file string, total_pages int,
                    collection string, source_path string, language string,
                    content_hash string);

-- Les onze tags d'element portent tous le meme schema, genere par
-- `tag_schema_statements()` a partir de `VERTEX_PROPERTIES` / `VERTEX_TYPES`.
CREATE TAG SectionHeader(label string, page_no int, page_no_end int, text string, media_url string, object_key string, depth int);
CREATE TAG Paragraph(label string, page_no int, page_no_end int, text string, media_url string, object_key string, depth int);
CREATE TAG Table(label string, page_no int, page_no_end int, text string, media_url string, object_key string, depth int);
CREATE TAG Picture(label string, page_no int, page_no_end int, text string, media_url string, object_key string, depth int);
-- ... (ListItem, Caption, Code, Formula, Footnote, PageHeader, PageFooter)

-- Migration : un ALTER par colonne et par tag, joue a chaque demarrage.
-- Sur un space deja peuple, le CREATE ci-dessus ne fait rien : c'est l'ALTER
-- qui ajoute les colonnes manquantes. Une colonne deja presente repond
-- « Existed! », ce qui est tolere. Le service constate ensuite le schema reel.
ALTER TAG SectionHeader ADD (depth int);
ALTER TAG SectionHeader ADD (page_no_end int);
-- ... (une ligne par colonne de VERTEX_PROPERTIES, pour chacun des onze tags)

-- Edges (relations)
CREATE EDGE PARENT_OF(sequence int);
CREATE EDGE LINKED_TO(relation string);

-- Index
CREATE TAG INDEX doc_index ON Document(filename(20));
```

### Evolution du schema

- `init_schema()` n'est joue qu'au demarrage du service. Apres une purge
  (`python -m src.wipe_stores`), redemarrer `docling-service` avant toute
  reingestion, sinon les INSERT visent un space ou des tags absents.
- Une colonne supprimee ne revient jamais : Nebula garde l'historique de schema
  d'un tag et refuse le re-ajout avec « Schema exisited before! » (mesure le
  31 aout 2026). `ALTER TAG ... DROP` n'est donc pas un moyen de retour arriere.
- Renommer une colonne suit la meme regle. `media_url` et `object_key` ont
  remplace l'ancienne colonne d'adresse du media : `ALTER TAG ... ADD` pose les
  deux nouvelles colonnes, mais l'ancienne reste sur le tag. Le seul etat propre
  est le `DROP SPACE` de `python -m src.wipe_stores`, suivi du redemarrage qui
  rejoue `init_schema()`.

**Les deux colonnes de media.** `media_url` est l'adresse que l'agent affiche ;
elle porte l'hote du stockage objet. `object_key` est la cle nue de l'objet dans
le bucket : elle reste valable si l'hote change.

## Variables d'environnement

| Variable     | Description         | Defaut  |
|--------------|---------------------|---------|
| NEBULA_HOST  | Hostname graphd     | graphd  |
| NEBULA_PORT  | Port graphd         | 9669    |
| NEBULA_USER  | Utilisateur         | root    |
| NEBULA_PASSWORD | Mot de passe     | nebula  |

## Dependances

`metad` -> `storaged` -> `graphd` (demarrage sequentiel)

## Persistence

- `./Datas/database/nebula/meta:/data/meta`
- `./Datas/database/nebula/storage:/data/storage`

## Healthcheck

Depuis un conteneur du reseau `rag_network` (le port n'est pas publie sur l'hote) :

```bash
curl -s http://graphd:19669/status
```

## UI

Nebula Studio accessible sur `http://localhost:7001`. Se connecter avec
`graphd:9669`, user `root`, password `nebula`.
