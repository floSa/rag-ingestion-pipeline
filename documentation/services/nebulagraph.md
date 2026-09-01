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
> Les autres écarts de ce bloc — le tag `Document` porte **7** propriétés et non
> 2 — restent ouverts au registre §6.18, pour le lot 5.

```ngql
CREATE SPACE rag_space(partition_num=10, replica_factor=1, vid_type=FIXED_STRING(256));

-- Tags (types de noeuds)
CREATE TAG Document(filename string, type_file string);
CREATE TAG SectionHeader(label string, page_no int, text string, minio_url string, depth int);
CREATE TAG Paragraph(label string, page_no int, text string, minio_url string, depth int);
CREATE TAG Table(label string, page_no int, text string, minio_url string, depth int);
CREATE TAG Picture(label string, page_no int, text string, minio_url string, depth int);
-- ... (ListItem, Caption, Code, Formula, Footnote, PageHeader, PageFooter)

-- `depth` est arrivee au lot 3 : l'agent pouvait remonter les PARENT_OF mais ne
-- pouvait lire aucun niveau declare sur un titre. Sur un space DEJA PEUPLE, le
-- CREATE ci-dessus ne fait rien : c'est l'ALTER qui migre, et le service le
-- joue a chaque demarrage puis CONSTATE le resultat.
ALTER TAG SectionHeader ADD (depth int);   -- « Existed! » si deja la : tolere

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
