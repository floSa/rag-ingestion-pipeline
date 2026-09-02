# Première campagne de référence — 2 septembre 2026

Ce fichier est le **compte rendu mesuré** de la première campagne de référence du
pipeline d'ingestion : la réingestion complète du corpus par le code de `main`,
le verdict des deux instruments dessus, et le jeu de trente questions écrit
contre l'index qui en sort.

> **RÉSERVE, ET ELLE VIENT AVANT LES CHIFFRES.** Cette campagne est un
> **contrôle de bon fonctionnement, pas une décision d'architecture**. Trente
> questions suffisent à prouver que la chaîne marche de bout en bout et à voir un
> défaut grossier. **Elles ne suffisent pas à arbitrer un réglage** : un écart de
> deux points est du bruit. Aucun chiffre de ce fichier ne doit servir à choisir
> un `k`, un seuil, un modèle ou une pondération. Le registre, section 1, le dit
> avant nous ; c'est la ligne que la première campagne d'un système finit
> toujours par franchir, et c'est pour cela qu'elle est écrite ici en tête.

Toute valeur porte son étiquette `mesuré`, `calculé` ou `supposé`, sa commande et
sa date. Le poste : GNU Make 4.4.1, `uv` 0.11.28, pile Compose
`rag-ingestion-pipeline`, neuf services debout plus le daemon démarré par cette
campagne (`mesuré`, `docker compose ps`).

**Une correction de date, en préalable.** Le mandat et le registre datent la
fusion du lot 5 et les mesures de leur §7.2 du **3 septembre 2026**. Le commit de
fusion `d8c67c5` porte `2026-09-02 12:29:51 +0000` en auteur **et** en committer,
et l'horloge du poste rendait `2026-09-02 12:40 UTC` au début de cette campagne
(`mesuré`, `git log -1 --format='%ai %ci' main` et `date -u`). Toutes les mesures
de ce fichier sont donc datées du **2 septembre 2026**.

---

## 1. L'état du poste, remesuré et non lu

Le §7.2 du mandat décrit cinq points d'état de poste et prescrit de les
remesurer. Voici les cinq, `mesuré` le 2 septembre 2026 avant tout geste.

| Point du §7.2 | Ce qu'il annonçait | Mesuré |
|---|---|---|
| `dagster-daemon` arrêté | arrêté | **vrai** — `Exited (0) 3 hours ago` (`docker compose ps -a`) |
| index vivant du code du lot 3 | 4 365 chunks, 15 196 sommets, 15 374 arêtes, 23 documents, 13 objets MinIO | **4 365 / 15 196 / 15 173 `PARENT_OF` / 23 / 13**. Le 15 374 du mandat compte **toutes** les arêtes ; les `PARENT_OF` seules sont 15 173, et c'est ce nombre que `verify_contract` examine |
| `verify_contract` sort en 1 sur **quatre** anomalies | quatre | **vrai, et les quatre sont exactement celles annoncées** |
| `verify_data` / `verify_contract` ne tournent pas côté hôte | vrai | non rejoué : le geste du §4.27 a été employé d'emblée |
| répertoire mort `lot-1-observation-b12761` | subsiste, 484 Mo | **subsiste** — non touché, il ne relève pas de ce lot |

Les quatre anomalies de départ, `mesuré` le 2 septembre 2026 par
`docker run … -v "$PWD/src":/app/src:ro … python -m src.verify_contract`
(le geste du registre §4.27, qui monte le `src` de la branche et non celui du
clone principal) :

```
cles de metadonnees manquantes : ['page_no_end']
elements au jeu de chunks troue: 2
sommets sans page_no_end       : 15173/15173
sommets visuels sans minio_url : 251/264
```

`rc=1`. Le `src` de cette branche est **identique** à celui de `main` à cet
instant (`git diff main..HEAD --stat -- src/` rend le vide), donc la précaution
du §4.27 ne changeait rien ici — et c'est précisément pourquoi il faut la
mesurer plutôt que la supposer.

Porte qualité avant tout geste, `mesuré` : `make all` → **rc=0**, 865 tests,
`mypy` « no issues found in 35 source files », « 73 files already formatted ».

Corpus, `mesuré` : 25 fichiers, 57 381 999 octets, et l'empreinte de la liste
`git ls-files -z -- Datas | xargs -0 sha256sum | sort` vaut
`b66441820e9b8d3114d5dafa0d5c6d461c427b3e770fb2c0e584e947df5f508e`. Les deux
arbres — le clone principal, que Docker monte, et l'arbre de travail de ce lot —
portent **le même corpus à l'octet**. Cette empreinte est le témoin de
non-modification rejoué après chaque geste de la campagne.

---

## 2. Étape 0 — les trois gestes, et l'effet de chacun

Un geste dont on ne mesure pas l'effet n'a pas eu lieu. Voici les trois, avec ce
que chacun a réellement changé.

### 2.1 Geste 1 — redémarrer `docling-service` : effet mesuré NUL sur le schéma

`docker compose restart docling-service`, 12:42:05 → 12:42:37 UTC, `rc=0`,
`healthy` en ~40 s. `init_schema()` a joué : le journal porte
`12:42:32,575 INFO [src.docling_service.nebula] Schema semantique NebulaGraph pret.`
et `GET /health` rend **200** avec `graph_ready: true`, `objects_ready: true`,
`models_ready: true`.

**L'effet sur le schéma est nul, et c'est le résultat.** `DESCRIBE TAG` sur les
**11** tags d'élément plus `Document`, avant et après, rend la même chose
(`mesuré`) : les 11 tags portent leurs **6** colonnes — `label`, `page_no`,
`text`, `minio_url`, `depth`, `page_no_end` — et `Document` ses **7**, avec
**0 colonne manquante sur les 12 tags**. Le poste était déjà dans le second des
deux états que `anomalie_de_colonne` distingue : schéma migré, données vides. La
consigne « redémarrer avant de réingérer » ne périme pas pour autant — elle est
sans coût quand le schéma est à jour, et c'est la seule des deux choses qui vaille
d'être apprise par cœur.

### 2.2 Geste 2 — `wipe_stores` purge QUATRE choses, et la quatrième est celle qui compte

`python -m src.wipe_stores` dans l'image d'extraction, avec **le `src` de cette
branche** et `Datas` monté en écriture. `rc=0`, et sa sortie titre les quatre :

```
collection rag_documents supprimee
13 objets supprimes du bucket documents
space rag_space supprime
22 fichiers retires de /opt/dagster/app/Datas/.cleaned
```

État **avant** le geste, `mesuré` : `Datas/.cleaned/` porte **22** fichiers,
2,4 Mo, et son HTML référence **199** URL `http://minio:9000/…` — alors que le
bucket n'en porte que **13** objets, tous des crops du PDF. **Les 199 images
référencées par le HTML nettoyé n'existaient pas.** C'est le §4.28.b, reproduit
au chiffre près.

État **après** le geste, `mesuré` : `Datas/.cleaned/` **n'existe plus** (0
fichier), ChromaDB porte **0** collection, NebulaGraph **0** space, le bucket
existe et porte **0** objet. Corpus **intact à l'octet** — même empreinte, `git
status --porcelain -- Datas` vide.

**Un quatrième geste, prescrit par `wipe_stores` lui-même, a suivi** :
`docker compose restart docling-service`, parce que la purge a joué
`DROP SPACE` et que `init_schema()` ne tourne qu'au démarrage du service — son
docstring le dit, et sa dernière ligne de sortie le répète. `mesuré` après ce
redémarrage : `SHOW SPACES` rend `rag_space`, et les 11 tags d'élément portent
leurs 6 colonnes, cette fois par `CREATE TAG` et non par `ALTER`. Ce n'est pas un
écart au §7.2 : c'est la complétion du geste 2, et le §7.2 ne la nomme pas.

### 2.3 Geste 3 — l'asset `cleaned_html`, seul chemin qui re-téléverse les images

Le daemon étant arrêté, l'asset a été matérialisé partition par partition, hors
capteur :

```bash
dagster asset materialize -f src/pipeline/definitions.py \
  --select 'livres_html/cleaned_html' --partition "<clé de partition>"
```

**22 partitions sur 22, `rc=0` chacune** (`mesuré`, 12:46:07 → 12:49:55 UTC).

Effet mesuré, et il se ferme sans résidu :

| | avant le geste 3 | après |
|---|---|---|
| fichiers dans `Datas/.cleaned/` | 0 | **22** |
| URL MinIO distinctes référencées par le HTML nettoyé | 0 | **199** |
| objets dans le bucket `documents` | 0 | **199** |
| URL référencées **et** présentes | — | **199** |
| URL référencées **absentes** du bucket | — | **0** |
| objets présents **non** référencés | — | **0** |

La première partition a été mesurée seule avant d'étendre aux 21 autres : 1
fichier, 4 URL référencées, **4** objets dans le bucket. Corpus intact à l'octet
après le geste.

**Ce geste est ce qui rend la réparation du lot 4 opérante.** `extraction.
propager_les_url_dimages` lit les URL **dans le HTML nettoyé**, où
`cleaning.py` les a écrites, puis les pose sur les éléments `picture` par
correspondance positionnelle, gardée par un refus. Sans `.cleaned/` à jour, il
n'y a rien à lire.

---

## 3. Étape 1 — la réingestion, et pourquoi démarrer le daemon n'a PAS suffi

### 3.1 Le fait qui contredit le mandat, et sa cause mesurée

Le mandat écrit : « Le daemon est arrêté et les capteurs sont livrés armés : le
démarrer déclenche l'ingestion. » **C'est faux sur ce poste, et la cause est
mesurée.**

Le Postgres de Dagster a **survécu** — il n'est pas reparti vierge comme au
lot 3 (§7.1). Ses curseurs portaient donc les mtimes de l'ingestion du 31 août :
`livres_html_sensor` **22 entrées**, `pdfs_sensor` **1** (`mesuré`,
`instance.all_instigator_state()`). Et les mtimes du corpus n'ont pas bougé —
identiques **au microseconde près** :

```
1788184102.942725  Datas/htms/MLOps with Databricks/1. MLOps Principles and Components.html
1788184102.942725  ← curseur
```

Les curseurs des deux capteurs porteurs ont donc été **vidés délibérément** —
acte daté 12:51:41 UTC, `dagster sensor cursor --delete`, vérifié à 0 entrée pour
les quatre capteurs — puis le daemon a été **démarré**, acte daté
**12:51:57.765 UTC**, `docker compose up -d dagster-daemon`, `rc=0`.

**Et l'ingestion ne s'est toujours pas déclenchée.** La cause est dans le tick,
et elle est mesurée sur l'objet que Dagster enregistre :

| Capteur | premier tick après démarrage | `run_keys` demandées | runs créés | `skip_reason` |
|---|---|---|---|---|
| `livres_html_sensor` | 12:52:02.534 UTC | **22** | **0** | **`None`** |
| `pdfs_sensor` | 12:52:01.822 UTC | **1** | **0** | **`None`** |
| les deux, ticks suivants | 12:52:33 et après | 0 | 0 | « Sensor function returned an empty result » |

Le capteur a bien construit ses **23** demandes de run — et le curseur, revenu à
22 et 1 entrées, le prouve par un second chemin : il n'est réécrit que dans la
branche qui ajoute une demande. **Dagster en a créé zéro, et n'a donné aucune
raison.**

**La cause est le `run_key`.** `factory.py`, `file_sensor` :
`run_key=f"{source.name}_{partition_key}_{mtime}"`. Ces 23 clés avaient été
consommées le 31 août avec **les mêmes mtimes**, et Dagster cherche un `run_key`
consommé dans **tout** l'historique, sans borne de temps. C'est **mot pour mot**
le défaut que la réparation du lot 0 a fermé dans `reindex_job.py` — registre
§8 : « Une clé d'idempotence déterministe interdit la reprise… le geste de
récupération naturel — remettre le curseur à zéro — ne rattrapait rien » — et il
est **intact dans le capteur d'ingestion**, où il n'était consigné nulle part.
Voir le registre §4.32.a, ouvert par cette campagne.

### 3.2 Ce qui a été fait à la place, déclaré comme écart

Les 23 partitions ont été lancées explicitement, par le **vrai** coordinateur de
runs et le vrai lanceur :

```bash
dagster job launch -w src/workspace.yaml -j livres_html_job \
  --tags '{"dagster/partition": "<clé de partition>"}'
```

**23 lancements, `rc=0` chacun** (`mesuré`). Ce qui est exercé : tout le chemin
d'ingestion du code de `main` — `cleaned_html` → `extracted_document` → service
Docling → les trois stores → le capteur de réindexation. Ce qui ne l'est pas : la
**création** du run par le capteur, que Dagster refuse. L'écart est écrit au §7
de ce fichier, à sa taille exacte.

### 3.3 Le temps, les partitions, et ce qui a rougi

`mesuré` le 2 septembre 2026, lu dans les `run_records` de l'instance Dagster :

| | |
|---|---|
| runs d'ingestion | **23**, dont **23 `SUCCESS`** et 0 échec |
| dont `livres_html_job` | 22 |
| dont `pdfs_job` | 1 |
| durée par run | min **8,8 s**, médiane **21,3 s**, max **93,1 s** (le PDF) |
| somme des durées | **556,4 s** |
| durée **murale** de l'ingestion | **451,2 s**, de 12:55:24 à 13:02:55 UTC |
| plafond de parallélisme | `max_concurrent_runs: 2` (`dagster.yaml`) |

**Ce qui a rougi, et c'est un seul objet : `agent_reindex_job`.** Un run par
tick de 30 s, **tous en échec**, tous sur `ReindexError` levée dans
`reindex_job.lexical_index`. La cause est mesurée et n'est pas un défaut de ce
dépôt : `AGENT_SERVICE_URL=http://agent-api:8000` et **le service
`rag-agent-chat` ne tourne pas sur ce poste** — il n'apparaît dans aucun
conteneur. C'est l'exigence 5 du contrat, et le §7 de ce fichier dit pourquoi
elle n'est **pas éprouvable** ici. Le compte de ces runs **croît tant que le
daemon tourne** : il valait **49** au moment de la mesure ci-dessous, ce qui est
un état de poste et non un résultat. Le §10 dit dans quel état la pile est
laissée.

**Et le garde du §4.15 a été observé en vol pour la première fois, mesuré.** Sur
les **49** ticks de réindexation, **un seul** a démarré alors qu'un run
d'ingestion était non terminal — à **12:55:19 UTC**, c'est-à-dire trois secondes
après la création du premier run d'ingestion, l'évaluation du capteur ayant donc
commencé avant que ce run ne soit enregistré. Puis **plus rien pendant 403
secondes**, de 12:56:21 à 13:03:04 UTC : le plus grand trou de toute la série, et
il recouvre exactement la fenêtre d'ingestion (12:55:24 → 13:02:55). La
réindexation a repris **9 secondes** après la fin du dernier run d'ingestion.
*(Le compte de 49 est celui de la mesure ; il croît d'un run toutes les 30
secondes.)*

Deux compteurs du registre se reproduisent **au chiffre près** dans le journal du
service, sur le PDF :

- « **39 titres sur 87 (45 %)** ont reçu le rang de REPLI et non un rang mesuré :
  le document ne classe que **3** niveaux » — §4.21, identique ;
- « **18 éléments** enjambent une frontière de page : leur `page_no` est la page
  d'entrée, `page_no_end` la page de sortie » — le compteur du §4.22, désormais
  peuplé.

Corpus **intact à l'octet** après l'ingestion (même empreinte, `git status` vide).

---

## 4. Étape 2 — `verify_contract` : trois anomalies fermées, une survit

`mesuré` le 2 septembre 2026 à 13:03:47 UTC, geste du §4.27 :

```
chunks examines                : 4367
element_id au mauvais format   : 0
element_id != graph_node_id    : 0
cles de metadonnees manquantes : aucune
ids de chunk suffixes en #n    : 976
chunks sans source_path        : 0
chunk_index hors de chunk_count: 0
elements au jeu de chunks troue: 0
modele des vecteurs            : paraphrase-multilingual-MiniLM-L12-v2
aretes PARENT_OF examinees     : 15173
aretes sans sequence           : 0
inversions de page dans l'ordre: 0
sommets sans depth             : 0/15173
sommets sans page_no_end       : 0/15173
colonnes du tag Document       : 7, manquantes aucune
sommets visuels sans minio_url : 52/264
ancres presentes dans le graphe : 3750/3750

ANOMALIE : 52 sommets visuels sur 264 sans minio_url
```

`rc=1`.

### 4.1 Les trois qui se ferment

| Anomalie | avant | après |
|---|---|---|
| clé `page_no_end` absente des métadonnées ChromaDB | `['page_no_end']` | **aucune** |
| éléments au jeu de chunks troué | **2** | **0** |
| sommets sans `page_no_end` | **15 173 / 15 173** | **0 / 15 173** |

Et le jeu de chunks passe de **4 365 à 4 367**, exactement la prédiction du
§4.28.a. **L'attribution des deux chunks retrouvés est mesurée, pas déduite** :

```
aa3de10738 label=code chunk_count=7 presents=[0,1,2,3,4,5,6]  (il manquait l'index 4)
eb52c4ec8f label=code chunk_count=4 presents=[0,1,2,3]        (il manquait l'index 3)
```

Les deux sont `label=code`, ce qui explique au chiffre près le seul mouvement de
la répartition par label d'`index_report` : `code` passe de **973** (§4.24) à
**975**, tous les autres labels inchangés.

### 4.2 Celle qui survit — et sa cause n'est pas le passé

**52 sommets visuels sur 264 n'ont pas de `minio_url`.** Le décompte se ferme
sans résidu (`mesuré`, remontée des chaînes `PARENT_OF` côté client, le §4.30.j
interdisant un `WHERE` sur une propriété d'arête) :

| origine | tag | avec URL | sans URL |
|---|---|---|---|
| HTML | `Picture` | **199** | **0** |
| HTML | `Table` | 0 | **52** |
| PDF | `Picture` | **10** | 0 |
| PDF | `Table` | **3** | 0 |
| | **total** | **212** | **52** |

**La chaîne d'images HTML du §3.5 est entièrement fermée** : 199 images sur 199
portent leur URL, contre 0 sur 199 mesurés par le lot 1 sur le producteur.

**Les 52 restants sont, à 52 sur 52, des tables HTML — et aucune ingestion ne
pourra jamais les pourvoir.** La cause est un désaccord entre deux sites du code
livré, et l'un des deux l'écrit noir sur blanc :

- `extraction.propager_les_url_dimages` **exclut délibérément** les tables, et son
  docstring dit pourquoi : « Seuls les `picture` sont ciblés, et non tous les
  éléments visuels : un `table` est visuel mais n'est pas une `<img>` du HTML. Le
  compter décalerait toutes les URL. » Une table HTML est rendue par Docling en
  Markdown — les 52 textes commencent tous par `|` — il n'y a **aucune image à
  téléverser** ;
- `verify_contract._lire_les_urls_visuelles` lit `minio_url` « sur tous les
  sommets `Picture` **et** `Table` » et compte l'absence comme une anomalie.

**Conséquence, et il faut l'écrire : `verify_contract` ne peut pas rendre 0 sur ce
corpus**, quoi qu'on ingère. L'attente du mandat — « les quatre doivent se
fermer » — est inatteignable pour la quatrième. Ce n'est ni une ingestion
incomplète, ni une régression : c'est un instrument qui compte une catégorie que
son producteur exclut par construction. **Consigné et non corrigé** — périmètre
strict, et l'arbitrage appartient au pilote : registre §4.32.b.

---

## 5. Étape 3 — `index_report`

`mesuré` le 2 septembre 2026 à 13:05:14 UTC, geste du §4.27, `rc=0`.

| Poste | Valeur |
|---|---|
| chunks indexés | **4 367** |
| documents distincts | **23** |
| sans caractère alphanumérique | **1** (0,0 %) |
| moins de 40 caractères | **21** (0,5 %) |
| taille des chunks | médiane **299**, moyenne 303, min/max **8 / 683** |
| éléments fusionnés par chunk | médiane **2**, maximum **18** |
| modèle | `paraphrase-multilingual-MiniLM-L12-v2`, fenêtre **128** tokens |
| mesure de troncature sur | texte **encodé**, titre de section compris |
| chunks tronqués par le modèle | **137** (3,1 %), tokens médiane **95**, max **149** |
| profondeur de hiérarchie | niveau 5 : **4** documents ; 4 : **5** ; 3 : **8** ; 2 : **5** ; **1 : 1 document — reste plat** |
| langue | **4 367 `en`** |
| labels | `text` 2 604, `code` **975**, `list_item` 484, `table` 196, `caption` 108 |

Trois lectures méritent d'être relevées.

**La troncature est reproduite à l'identique sur un index neuf** : 137 chunks,
3,1 %, médiane 95, maximum 149 — les quatre valeurs du registre §3.4, mesurées
là sur l'index du lot 3. L'instrument est stable et le corpus n'a pas bougé.

**Le chapitre plat est unique, et il est là.** `1 document au niveau 1 — restes
plats`, sur 23. C'est le §3.2 confirmé à pleine portée du corpus : 22 documents
s'imbriquent, un est plat.

**`section_header` n'apparaît pas dans la répartition par label** — il vaut 0.
C'est la charge utile du §4.24, intacte : aucun titre n'est jamais un chunk, donc
l'agent ne peut lire le niveau d'un titre que dans le graphe.

---

## 6. Étape 4 — les trente questions

### 6.1 Ce qui a été échantillonné, et ce qui ne l'a pas été

**On échantillonne les questions, JAMAIS le corpus.** Les 23 documents sont
ingérés et les 30 questions sont cherchées contre les **4 367** chunks. Grosse
meule de foin, échantillon d'aiguilles.

Les questions sont tirées de **deux chapitres par ouvrage plus une section
d'environ cinq pages du PDF**, comme la spécification le prescrit :

| Source échantillonnée | `element_id` cités |
|---|---|
| `MLOps with Databricks/4. Model Serving：Architectures and Implementation.html` | 15 |
| `MLOps with Databricks/7. Foundation Models and Context Engineering.html` | 12 |
| `Practical MLflow…/6. Evaluating GenAI Applications with MLflow.html` | 8 |
| `Practical MLflow…/8. Deploying a GenAI Application with MLflow.html` | 8 |
| `pdfs/Hands-On_RAG_for_Production…pdf`, **pages 41 à 45** | 4 |

Le motif du choix : le chapitre 4 et le chapitre 8 traitent tous deux du
déploiement et du service de modèles dans les deux ouvrages — ce sont les
« sosies plausibles » que le registre §4 annonce comme voulus ; le chapitre 7 est
le plus profondément imbriqué du corpus ; et les pages 41–45 du PDF
(« Response Quality and Reduced Hallucinations » et ses quatre raisons, plus
« High Latency ») sont voisines thématiquement des deux, ce qui rend possibles des
questions **inter-documents**.

Les chapitres ont été **lus dans le store**, pas de mémoire : le texte de chaque
chunk avec son `element_id`, son `label`, son `depth` et son `section_title`, par
`collection.get(where={"source_path": …})`.

### 6.2 Le jeu, et sa conformité à la spécification

Le jeu vit dans
[`2026-09-02-jeu-de-questions.yaml`](2026-09-02-jeu-de-questions.yaml).

**Il est en YAML et non en JSON, et le motif est une mesure et non un goût.**
Écrit d'abord en JSON, il a fait **refuser le commit** : `detect-secrets` lit
l'empreinte SHA-256 du corpus comme une « Hex High Entropy String », et le dépôt
déclare ses faux positifs **au site**, par un `pragma: allowlist secret`
justifié — or JSON n'admet pas de commentaire, donc pas de pragma, donc pas de
justification qu'un relecteur voit dans le diff. C'est **exactement** l'arbitrage
déjà pris au registre §3.6 bis pour `tests/fixtures/arbres_docling.yaml`, et il
se reproduit ici sans qu'on l'ait cherché. `rc=1`, `HEAD` inchangé : le garde a
fait son travail. Aucun `--no-verify`, aucune baseline, aucune règle relâchée.
Bénéfice de plus, gratuit : le hook `check-yaml` valide désormais ce fichier à
chaque commit, ce que le JSON n'avait pas.

| Strate | Attendu (registre §1) | Livré |
|---|---|---|
| multi-passages, 2 ou 3 sections différentes | 12 | **12** |
| simple, un passage | 8 | **8** |
| sans réponse | 4 | **4** |
| de suivi, avec `chat_history` | 4 | **4** |
| reformulée | 2 | **2** |
| | **30** | **30** |

`mesuré` : **44 `element_id` distincts**, tous au format `^[a-f0-9]{10}$`, tous
présents dans l'index, et les 12 multi-passages couvrent **2 ou 3 sections
distinctes** chacune — dont deux qui franchissent une frontière de **document**.
Les questions pièges sont **reportées au second tour**, comme la spécification le
prescrit : c'est la strate où un modèle écrit le plus facilement un faux piège, et
elle demande une relecture humaine.

**Les quatre questions sans réponse sont vérifiées sans réponse, par deux voies.**
Une question « sans réponse » qui aurait une réponse punirait une abstention
correcte — elle mesurerait l'inverse de ce qu'elle prétend. `mesuré` sur les 4 367
chunks : balayage lexical des termes discriminants, puis lecture des cinq plus
proches voisins vectoriels.

| Question | terme discriminant | porteurs |
|---|---|---|
| prix horaire en USD d'un `CU_8` en `eu-west-1` | `usd`, `eu-west` | **0**, **0** |
| contrôleur d'ingress Kubernetes de Model Serving | `ingress`, `nginx`, `istio`, `traefik` | **0** partout |
| F1-score atteint par l'arXiv Curator | un F1 chiffré associé à arXiv | **0** |
| nombre de pages du manuel Samsung | `samsung … page` | **0** |

Et leurs plus proches voisins sont **précisément les passages plausibles** — la
table des coûts Databricks, les sections de l'arXiv Curator, et pour la
quatrième le passage même qui mentionne le manuel Samsung comme exemple de donnée
manquante. C'est le piège que l'abstention doit franchir, et il est mesuré.

**Une conséquence à consigner, qui n'est pas un défaut : le corpus est
entièrement anglais** — `index_report` rend **4 367 chunks `en`** sur 4 367. La
mesure translinguistique est donc coupée en deux : « question française →
document anglais » reste possible, l'inverse disparaît. Le jeu échantillonne la
moitié survivante avec **une** question, `q30`, en français sur un passage
anglais. **À n = 1, cet axe est échantillonné, pas mesuré** : c'est une réserve,
pas un résultat.

### 6.3 Ce qui garde le jeu, et ce qui ne le garde pas

Deux objets, et **aucun ne remplace l'autre**. La distinction est le sujet, parce
qu'un jeu de questions ne rougit pas tout seul : il devient faux en silence.

**`tests/unit/test_jeu_de_questions.py` garde la FORME** — 19 tests, dans
`make test`. Les effectifs attendus y sont écrits en **littéraux** tirés du
registre §1 : les dériver du fichier rendrait chaque assertion vraie par
construction, le motif des treize gardes creux de ce chantier.

**`scripts/campagne/verifier-le-jeu-de-questions.py` garde la PROVENANCE** — il
relit chaque ancrage dans l'index vivant, champ par champ, et sort en **1** au
premier désaccord. Il n'est pas un test parce qu'il ne peut pas l'être :
`chromadb` n'appartient pas aux dépendances du dépôt, et un test qui l'importerait
ne serait collectable sur aucun poste sans l'image d'extraction.

`mesuré` sur l'index de la campagne : **`rc=0`, les 44 ancrages concordent, champ
par champ.**

**Les deux moitiés sont prouvées par mutation du fichier livré**, texte vérifié
changé à chaque fois et empreinte restaurée après chacune. Onze mutations
rougissent les gardes de forme, et **la douzième les laisse entièrement verts** —
c'est elle qui prouve que la frontière annoncée est la vraie :

| Mutation du jeu livré | `test_jeu_de_questions.py` | script de provenance |
|---|---|---|
| une question change de strate | **rouge**, 2 tests | — |
| une `sans_reponse` porte un ancrage | **rouge**, 1 | — |
| une `simple` perd son ancrage | **rouge**, 3 | — |
| un `element_id` à 9 hexadécimaux | **rouge**, 1 | — |
| une multi-passages sur UNE seule section | **rouge**, 1 | — |
| une `de_suivi` perd son historique | **rouge**, 2 | — |
| plus aucune question française | **rouge**, 1 | — |
| le périmètre déclaré dépasse le réel | **rouge**, 1 | — |
| un doublon d'ancrage dans une question | **rouge**, 4 | — |
| le fichier certifie ses propres effectifs | **rouge**, 3 | — |
| le jeu est **vide** (témoin de creux) | **rouge**, 5 | — |
| **un ancrage désigne la mauvaise section** | **VERT, 0 rouge** | **rc=1**, cause nommée |
| le volume d'index annoncé est faux | non couvert | **rc=1** |
| un ancrage désigne un `element_id` inexistant | non couvert | **rc=1** |
| *témoin — aucune mutation* | vert, 19 tests | **rc=0** |

*(Le `rc` du script est lu **sans pipe**. Une première mesure le lisait derrière
un `grep`, qui rendait `0` sur les trois rouges : c'est le piège F3 du registre —
« le code de retour d'un `cmd | tail` est celui de `tail` » — et il a été commis
puis corrigé ici.)*

### 6.4 Le rappel vectoriel brut — ce qu'il mesure, et ce qu'il ne mesure pas

`mesuré` le 2 septembre 2026 par
`scripts/campagne/mesurer-le-rappel-vectoriel.py`, `rc=0`, contre les **4 367**
chunks. La question est encodée **exactement comme la production encode** :
`get_embedding_model().encode(...)` sans normalisation, la collection ne déclarant
pas `hnsw:space` — ChromaDB retombe donc sur `l2` (§4.29.f).

**Ce que cette mesure NE couvre pas, et il faut le lire avant les chiffres :** ni
BM25, ni la reconstruction par le graphe, ni le reranker, ni l'abstention. Tout
cela vit dans `rag-agent-chat`. C'est un **plancher dense**, mesuré de ce côté-ci
de la frontière.

| k | rappel micro | rappel macro | au moins un passage |
|---|---|---|---|
| 5 | **26 / 47 = 55,3 %** | 61,5 % | **20 / 26 = 76,9 %** |
| 10 | **29 / 47 = 61,7 %** | 66,0 % | 20 / 26 = 76,9 % |
| 20 | **34 / 47 = 72,3 %** | 72,4 % | **21 / 26 = 80,8 %** |

*(26 questions à réponse sur 30 ; les 4 sans réponse n'ont pas de rappel.)*

Par strate, à k = 10 :

| Strate | n | rappel micro | au moins un |
|---|---|---|---|
| simple | 8 | **8 / 8 = 100 %** | **8 / 8** |
| multi-passages | 12 | 18 / 31 = 58,1 % | 10 / 12 |
| reformulée | 2 | 2 / 3 = 66,7 % | 1 / 2 |
| de suivi | 4 | **1 / 5 = 20,0 %** | 1 / 4 |

**Trois lectures, et aucune n'est une conclusion de réglage.**

**Le plancher de contrôle tient : 8 sur 8, à k = 5.** C'est ce que la strate
`simple` existe pour dire, et c'est la preuve que la chaîne fonctionne de bout en
bout — le texte est extrait, découpé, encodé, indexé avec ses métadonnées, et
retrouvé par une question posée dans d'autres mots que le passage.

**Le 20 % des questions de suivi n'est pas un défaut de l'index : c'est le
périmètre de la mesure.** Le script encode la question **seule**, sans son
`chat_history` — « Et laquelle des trois demande un identifiant de plus dans la
charge ? » ne porte, hors contexte, presque aucun signal. La résolution de
l'antécédent est le travail de l'agent. Ce chiffre dit que la strate est **dure
comme prévu**, pas que quelque chose est cassé.

**La question française rend 2 sur 2 dans les cinq premiers.** `q30`, posée en
français sur un passage anglais dont elle ne partage aucun mot, retrouve ses deux
ancrages. La moitié survivante de la mesure translinguistique fonctionne — **à
n = 1**, ce qui est un échantillon et non une mesure.

**Une observation qu'il faut écrire sans en tirer de réglage.** La distance L2 du
premier voisin, `mesuré` : questions à réponse, min **6,11**, médiane **9,72**,
max **20,01** ; questions sans réponse, **8,73**, **11,44**, **13,11**, **14,86**.
**Les deux plages se recouvrent largement.** Sur cet échantillon, la distance
brute du premier voisin dense ne sépare donc pas « répondable » de « non
répondable ». C'est un fait mesuré sur 30 questions, et ce n'est **pas** un
argument pour ou contre un seuil : trente questions ne suffisent pas à en
dimensionner un.

---

## 7. Ce que la campagne n'a PAS pu mesurer

Une réserve écrite vaut mieux qu'une conclusion tirée.

**L'exigence 5 du contrat — `POST /reindex` en fin de pipeline — n'est PAS
éprouvable sur ce poste, et elle n'est pas déclarée tenue.** `mesuré` : les **49**
runs `agent_reindex_job` de la fenêtre de campagne ont **tous** échoué sur
`ReindexError`, parce que `AGENT_SERVICE_URL=http://agent-api:8000` désigne un
service qui ne tourne pas — `rag-agent-chat` n'existe dans aucun conteneur du
poste. Ce qui **est** mesuré, et qui n'est pas rien : le déclenchement fonctionne
— le capteur arme le job, il **saute pendant 403 secondes** tant qu'un run
d'ingestion est en vol (§4.15, observé en vol pour la première fois, §3.3), et
l'échec **rougit son run** au lieu de se perdre dans une métadonnée verte, ce qui
était la charge utile de la réparation du lot 0. Ce qui n'est pas mesuré est la
seule chose qui compte pour l'agent : que l'index BM25 d'en face ait été
reconstruit.

**La création du run par le capteur d'ingestion n'a pas pu être exercée**, et la
cause est le `run_key` déterministe du §3.1. Ce que la campagne a exercé du
capteur : le balayage du glob, l'écartement des deux `Index.html` par
`matter.is_front_back_matter` (visible au journal du daemon), la gestion du
curseur, et la construction des 23 demandes. Ce qu'elle n'a pas exercé : leur
transformation en runs.

**La qualité des trente questions n'est garantie par rien d'automatique.** Les
gardes tiennent leur forme et leur provenance ; qu'une question soit dure et
qu'une réponse attendue soit juste demande une relecture humaine. C'est le motif
même pour lequel la spécification reporte les questions pièges au second tour, et
il vaut aussi pour les cinq strates livrées.

**L'ablation du graphe n'est pas mesurée, et ce n'était pas le mandat.** La
contrainte d'ordre 6 du contrat est désormais **levée** — la profondeur réelle du
graphe est constatée : 22 documents sur 23 s'imbriquent, `depth` atteint 5, et
`index_report` en donne la distribution. L'ablation elle-même se mesure côté
agent, avec le jeu que cette campagne livre.

**La contrainte 7 — le coût en fenêtre de contexte d'un fil d'Ariane réel — n'est
pas mesurée ici** : les chiffres du contrat (34 caractères sans fil d'Ariane, 134
à deux niveaux, 275 à cinq) sont `mesuré`s **côté agent**, et c'est là qu'ils se
remesurent.

**Le rappel mesuré est un plancher dense**, non le rappel du système : §6.4.

---

## 8. Les écarts au mandat, à leur taille exacte

Trois, et aucun n'est un écart de périmètre.

**Écart 1 — les curseurs des capteurs ont été vidés, et le daemon n'a pas suffi
à déclencher l'ingestion.** Le mandat annonce « le démarrer déclenche
l'ingestion » ; c'est faux sur ce poste et la cause est mesurée (§3.1). J'ai vidé
les curseurs des deux capteurs porteurs — **état Dagster, jamais le corpus** —
puis démarré le daemon comme acte daté. Cela n'a toujours pas suffi, et les 23
partitions ont été lancées par `dagster job launch` avec le tag de partition.
**Taille exacte : la création du run par le capteur n'est pas exercée ; tout le
reste du chemin l'est.** Le corpus n'a pas été touché — j'ai écarté d'emblée le
geste qui aurait « marché » (un `touch` sur les 23 fichiers), parce que le mtime
est ce que le capteur lit et que le mandat interdit de modifier le corpus.

**Écart 2 — un quatrième geste s'est ajouté aux trois du §7.2** : un second
redémarrage de `docling-service` après la purge, parce que `wipe_stores` joue
`DROP SPACE` et que `init_schema()` ne tourne qu'au démarrage. Ce n'est pas mon
invention : le docstring de `wipe_stores` le prescrit et sa dernière ligne de
sortie le répète. **Taille exacte : un `docker compose restart`, mesuré avant et
après.**

**Écart 3 — j'ai livré du code, ce que le mandat n'annonçait pas.** Le lot 6 est
décrit comme produisant « un récit et non du code ». Il livre en plus **19
tests**, **deux scripts** et **un artefact de données**. Le motif : un jeu de
questions ne rougit pas tout seul, et le mandat exige que « tout garde neuf
rougisse à la mutation du code livré ». Sans garde, la spécification du registre
§1 resterait une phrase d'exhaustivité dans un document — exactement ce que le
lot 5 a passé trente commits à fermer. **Taille exacte : 4 fichiers neufs,
aucune ligne de `src/` touchée** (`git diff main..HEAD --stat -- src/` rend le
vide).

---

## 9. Ce que la campagne laisse au registre

Deux constats neufs, mesurés, **hors du diff** — périmètre strict.

- **§4.32.a** — le `run_key` déterministe du capteur d'ingestion interdit toute
  réingestion d'un fichier non modifié, et la remise à zéro du curseur ne
  rattrape rien. Sévérité : il rend le geste « réingérer le corpus » impossible
  par le chemin nominal, et il est **silencieux** — `skip_reason=None`, 22 runs
  perdus sans un mot.
- **§4.32.b** — `verify_contract` compte les `Table` parmi les sommets visuels
  alors que le producteur exclut les tables par construction et l'écrit à son
  site. Il ne peut donc pas rendre 0 sur ce corpus.

Un troisième point, plus petit, est consigné au même endroit : `make lint` et
`make format-check` portent sur `src/ tests/` et **ne voient pas `scripts/`**,
alors que le hook `ruff` voit tout ce qui est indexé. C'est la divergence de
portée de la famille D7, une nouvelle fois, et ce lot y ajoute deux fichiers.
Les deux scripts livrés passent `ruff check` et `ruff format --check` — mesuré,
et la porte ne le dira pas à ma place.
