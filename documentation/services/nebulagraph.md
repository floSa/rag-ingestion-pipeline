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

> **`vid_type` vaut 256 octets et non 64, et l'écart n'était pas cosmétique.**
> Ce bloc annonçait `FIXED_STRING(64)`, une requête directement copiable — et un
> space créé à 64 **refuse** les deux documents réels du corpus, dont les
> identifiants font 65 et 67 octets : « *Storage Error: The VID must be a 64-bit
> integer or a string fitting space vertex id length limit* » (`mesuré` le
> 1er septembre 2026 sur un space jetable). Nebula ne sait pas modifier un
> `vid_type` : la réparation coûte une purge complète des stores. Le site
> canonique de cette valeur est `VID_MAX_BYTES` dans
> `src/docling_service/ngql.py`, et `create_space_statement()` est la seule
> requête de création du dépôt.
>
> **Les autres écarts de ce bloc sont fermés par le lot 5**, et il y en avait un
> de plus que le registre §6.18 n'en annonçait. `mesuré` le 2 septembre 2026 sur
> le code livré (`ngql.DOCUMENT_PROPERTIES`, `ngql.VERTEX_PROPERTIES` et
> `ngql.VERTEX_TYPES`, lus dans l'interpréteur) :
>
> | Ce que ce bloc disait | Mesuré |
> |---|---|
> | `Document(filename string, type_file string)` — **2** propriétés | **7** : `filename`, `type_file`, `total_pages`, `collection`, `source_path`, `language`, `content_hash` |
> | les 11 tags d'élément portent `label, page_no, text, minio_url, depth` — **5** colonnes | **6** : `page_no_end` manquait |
>
> `source_path` est **l'exigence 3 du contrat** — l'identité d'un document — et
> elle manquait du tag documenté. `page_no_end`, elle, a été ajoutée par le
> **lot 4** aux onze tags, et ce document n'a pas été touché : c'est le motif de
> ce lot, le gibier naissant dans le commit qui fait bien son travail.

```ngql
CREATE SPACE rag_space(partition_num=10, replica_factor=1, vid_type=FIXED_STRING(256));

-- Tags (types de noeuds)
CREATE TAG Document(filename string, type_file string, total_pages int,
                    collection string, source_path string, language string,
                    content_hash string);

-- Les ONZE tags d'element portent tous le meme schema, et son site canonique est
-- `VERTEX_PROPERTIES` / `VERTEX_TYPES` dans `src/docling_service/ngql.py` : les
-- deux tuples sont lus ENSEMBLE par `tag_schema_statements()`, qui genere ce qui
-- suit. Ne recopie pas cette liste ailleurs.
CREATE TAG SectionHeader(label string, page_no int, page_no_end int, text string, minio_url string, depth int);
CREATE TAG Paragraph(label string, page_no int, page_no_end int, text string, minio_url string, depth int);
CREATE TAG Table(label string, page_no int, page_no_end int, text string, minio_url string, depth int);
CREATE TAG Picture(label string, page_no int, page_no_end int, text string, minio_url string, depth int);
-- ... (ListItem, Caption, Code, Formula, Footnote, PageHeader, PageFooter)

-- `depth` est arrivee au lot 3 : l'agent pouvait remonter les PARENT_OF mais ne
-- pouvait lire aucun niveau declare sur un titre. Sur un space DEJA PEUPLE, le
-- CREATE ci-dessus ne fait rien : c'est l'ALTER qui migre, et le service le
-- joue a chaque demarrage puis CONSTATE le resultat.
ALTER TAG SectionHeader ADD (depth int);        -- « Existed! » si deja la : tolere
ALTER TAG SectionHeader ADD (page_no_end int); -- ajoutee par le lot 4, meme regle

-- `init_schema()` emet un ALTER PAR COLONNE et non pour la seule colonne du jour :
-- aucune liste a tenir a jour, et un space ancien recoit exactement ce qui lui
-- manque. Sur un space neuf les douze echouent en « Existed! », ce qui est tolere.
-- ATTENTION : `init_schema()` n'est joue qu'AU DEMARRAGE du service. Redemarrer
-- `docling-service` AVANT toute reingestion, sans quoi les INSERT visent un tag
-- qui n'a pas la colonne.

-- Une colonne SUPPRIMEE ne revient jamais. Nebula garde l'historique de schema
-- d'un tag et refuse le ré-ajout avec « Schema exisited before! » (`mesure`,
-- 31 aout 2026). Un ALTER ... DROP condamne donc le tag jusqu'a la recreation
-- du space : ne l'utilise pas comme rollback.

-- Edges (relations)
CREATE EDGE PARENT_OF(sequence int);
CREATE EDGE LINKED_TO(relation string);

-- Index
CREATE TAG INDEX doc_index ON Document(filename(20));
```

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

```bash
curl -s http://graphd:19669/status
```

## UI

Nebula Studio accessible sur `http://localhost:7001`. Se connecter avec
`graphd:9669`, user `root`, password `nebula`.
