# Registre des axes d'amélioration

Ce fichier est la mémoire du chantier d'audit et de refonte du pipeline
d'ingestion. **Chaque ligne y est vérifiable dans le code** : un constat sans
preuve `fichier:ligne` n'a pas sa place ici, et un constat corrigé se déplace
en section « Traité » avec le commit qui l'a fermé — il ne s'efface pas.

Trois étiquettes accompagnent tout chiffre : `mesuré` (relevé par une commande
dont la sortie a été lue), `calculé` (dérivé de valeurs mesurées) ou `supposé`
(hypothèse à vérifier). Une valeur reprise d'ailleurs sans remesure porte sa
réserve **une fois**, à son site canonique ; les autres mentions y renvoient.

---

## 0. Le contrat avec `rag-agent-chat`

Ces sept points ne sont pas des demandes ponctuelles. Ce sont les conditions
sans lesquelles `rag-agent-chat` ne peut pas reprendre son propre chantier. Ils
survivent aux conversations qui travaillent ici. La source est
[`documentation/pour_le_pipeline_ingestion.md`](https://github.com/floSa/rag-agent-chat)
côté agent ; ce qui suit est autosuffisant.

### Les cinq exigences dures

1. **Le modèle d'embedding est `paraphrase-multilingual-MiniLM-L12-v2`
   (384 dimensions), identique des deux côtés.** Un désaccord est la panne la
   plus coûteuse du système et elle est parfaitement silencieuse : les **deux**
   modèles candidats rendent 384 dimensions, donc ChromaDB accepte sans
   broncher, aucune sonde ne voit rien, et la recherche rend des passages
   plausibles et faux. C'est déjà arrivé : un `.env` de juin portait
   `all-MiniLM-L6-v2`, un modèle anglais, face à un agent multilingue.
   Vérifier la dimension ne protège de rien — c'est le **nom** qui discrimine.

2. **`element_id` est déterministe, dérivé du contenu, 10 caractères
   hexadécimaux** (`^[a-f0-9]{10}$`). Le jeu de questions de l'agent désigne
   des `element_id` : un identifiant qui change rend toute la mesure historique
   incomparable. Le `source_path` entre dans le calcul, donc **tout renommage
   de fichier après ingestion tue le jeu de questions.**

3. **`source_path` est l'identité d'un document, jamais `filename` seul.** Le
   corpus actuel le prouve : `Index.html` et `Preface.html` existent dans les
   deux ouvrages.

4. **La propriété `sequence` de l'arête `PARENT_OF` porte l'ordre, et doit être
   monotone.** L'agent s'en sert pour la fenêtre d'éléments et pour le
   « avant / après » entre sections voisines. Absente ou non monotone, elle
   casse la reconstruction sans erreur visible.

5. **`POST /reindex` sur l'agent, en fin de pipeline.** L'agent tient un index
   BM25 en mémoire ; sans cet appel, un document ingéré après son démarrage est
   invisible en recherche lexicale. Son filet interne compare
   `collection.count()` au nombre de chunks indexés : il est donc aveugle à une
   réingestion qui retire autant de chunks qu'elle en ajoute — exactement notre
   cas.

### Les deux contraintes d'ordre

6. **L'ablation du graphe ne se mesure pas avant que la profondeur RÉELLE du
   graphe ait été constatée**, pas seulement le code relu. Le code hiérarchique
   existe (`hierarchy.py`, `ranking.py`) mais n'a jamais tourné sur un corpus.
   Un graphe plat ne restitue qu'un seul titre, jamais
   `Chapitre 3 > 3.2 > 3.2.1` : mesurer l'ablation dessus répondrait à une
   autre question, et la campagne — une demi-heure — serait à rejouer.

7. **Une hiérarchie réelle coûte de la fenêtre de contexte** : le cadrage d'une
   source vaut 34 caractères sans fil d'Ariane, 134 à deux niveaux, 275 à cinq
   (`mesuré` côté agent). C'est prévu là-bas ; la première campagne le verra.

---

## 1. La conception du jeu d'évaluation

Décision de conception, pas consigne de circonstance. L'ancien jeu de 138
questions est mort avec l'ancien corpus, et il ne doit pas être reconstruit
comme avant.

**Pourquoi l'ancien générateur fabriquait des questions faciles.** La stratégie
d'évaluation côté agent liste cinq garde-fous, dont « la preuve doit être
RECOPIÉE du passage » et « la question doit PARTAGER DU VOCABULAIRE DISTINCTIF
avec lui ». Le second garantit un recouvrement lexical entre question et
passage : l'embedding n'a plus aucun travail à faire.

**La conséquence est grave.** Si chaque question se répond avec **un seul**
passage, la reconstruction par le graphe — ±6 éléments, ±3 sections voisines —
ne peut que coûter du contexte, jamais en apporter. L'ablation conclurait « le
graphe ne sert à rien », et cette conclusion serait un artefact de la
population de questions. On aurait tranché le pari central du projet sur un
test incapable de le voir.

**La méthode retenue.** Un modèle capable lit des chapitres **entiers** et écrit
des questions dures, en notant quels passages contiennent la réponse — sans
quoi le rappel n'est plus calculable, et c'est la seule contrainte qu'on ne
peut pas lâcher.

**L'échantillonnage.** Les questions sont tirées de 2 chapitres par ouvrage plus
une section d'environ 5 pages du PDF. Mais **on ingère tout**, et elles sont
cherchées contre le corpus entier : grosse meule de foin, échantillon
d'aiguilles. L'erreur inverse — échantillonner l'ingestion — rend le rappel
trivial et la mesure creuse.

**Le volume : 30 questions au premier tour**, concentrées et non étalées sur six
strates (à 4 par strate, aucune ne dit rien) :

| Strate                                        | Nombre | Ce qu'elle sert à voir      |
|-----------------------------------------------|--------|-----------------------------|
| multi-passages, 2 ou 3 sections différentes    | 12     | rend l'ablation lisible     |
| simple, un passage                             | 8      | plancher de contrôle        |
| sans réponse                                   | 4      | teste l'abstention          |
| de suivi, avec `chat_history`                  | 4      | il n'y en avait **aucune**  |
| reformulée, vocabulaire différent du texte     | 2      | échantillon                 |

Les questions pièges sont **reportées au second tour** : c'est la strate où un
modèle écrit le plus facilement un faux piège, et elle demande une relecture
humaine.

**Ce que 30 questions peuvent et ne peuvent pas.** Elles suffisent à prouver que
la chaîne fonctionne de bout en bout et à voir un défaut grossier. Elles ne
suffisent pas à arbitrer un réglage : un écart de 2 points sur 25 questions est
du bruit, et le test de signe de l'agent le dira lui-même. **Première mesure =
contrôle de bon fonctionnement, pas décision d'architecture.**

**L'ordre est forcé.** Les `element_id` sont créés **par** l'ingestion, donc les
questions viennent **après** : ingérer tout → lire les chapitres échantillonnés
dans le store → écrire les 30 questions avec les vrais identifiants. Un
chapitre fait environ 60 000 caractères, soit ~15 000 tokens (`calculé`) : lire
4 chapitres est praticable. Les deux ouvrages font 764 000 et 554 000
caractères de texte réel, hors images inline (`mesuré`, source : relevé
préalable au chantier).

**Conséquence à consigner.** L'ancien corpus était mixte français/anglais, et 58
questions sur 138 portaient sur des documents français. Le nouveau est
**entièrement anglais**. La mesure translinguistique est donc coupée en deux :
« question française → document anglais » reste possible, « question anglaise →
document français » disparaît. Le réglage `TRANSLATION_WEIGHT=1.0` de l'agent
ne sera plus vérifiable que dans un sens.

---

## 2. État mesuré du dépôt au 28 août 2026

Toutes les valeurs de cette section sont `mesuré`, dans ce dépôt, ce jour.

| Objet | Tests | `ruff check src/` | `mypy src/` |
|---|---|---|---|
| `main` (77d4f5b) | **395** verts | propre | propre |
| `claude/rag-ingestion-pipeline-restore-5e9fa1` (832c566) | **477** verts | propre | **2 erreurs** |

Les 5 commits de la branche, individuellement : 420 / 430 / 430 / 457 / 477
tests verts ; `ruff` propre partout ; **`mypy` rouge dès le premier commit**
(`a739571`). Aucun commit de la branche ne passe `make all`.

Le chiffre « 407 tests verts » annoncé pour cette branche est **faux** : la
mesure donne 477.

Stabilité : 25 graines `PYTHONHASHSEED` sur `main` → 25/25 vertes.

Corpus en place : 24 fichiers HTML (2 ouvrages × 12) + 1 PDF de 73 pages
(`mesuré`). Parmi les 12 fichiers de chaque ouvrage, `Index.html` est écarté par
le capteur (`matter.py:40`) ; **`Preface.html` ne l'est pas** — « preface » n'est
pas dans `FRONT_BACK_MATTER_TITLES`. Il sera donc ingéré depuis les deux
ouvrages, ce qui est le cas d'école de l'exigence 3.

---

## 3. Ouvert — bloque une mesure

Un défaut qui bloque une mesure passe devant un défaut plus grave mais inerte.

### 3.1 `POST /reindex` est absent de `main` — exigence 5 non tenue

`grep -rn reindex` sur `main` : **zéro occurrence**. Le contrat n'est pas tenu.
Une implémentation existe sur `claude/rag-ingestion-pipeline-restore-5e9fa1`
(`src/pipeline/reindex.py`), mais elle appelle `/reindex` **par document** :
`factory.py:_record_metadata` invoque `_reindex(context)`, et `_record_metadata`
tourne une fois par partition. Le docstring du module dit « en fin
d'ingestion », le contrat dit « en fin de pipeline ». Sur 21 documents, cela
fait 21 reconstructions BM25 complètes et synchrones côté agent.

**Coût de l'attente** : total. Sans cet appel, toute campagne mesure une
recherche lexicale aveugle aux documents ingérés, sans que rien ne le signale.

### 3.2 Le graphe est peut-être toujours plat — contrainte 6

`ranking.docling_parent_rank` (`ranking.py:56-71`) rend **`0`**, et non `None`,
dès que le premier parent rencontré est `#/body`. `extraction._flat_rank`
(`extraction.py:370-373` ; `ranking.flat_rank` sur la branche) ne bascule sur
`docling_level_rank` que si le premier signal rend `None`. **Donc si Docling
n'imbrique pas les titres d'une capture SingleFile, tous les titres reçoivent
le rang 0, deviennent frères sous le document, et le graphe est plat** — les
901 / 0 / 0 mesurés côté agent. L'attribut `level` (h1..h6), signal fiable du
HTML, est écrasé par un signal moins fiable qui ne s'avoue jamais absent.

Ce n'est **pas prouvé** : ce code n'a jamais tourné sur ce corpus.

Deux chiffres s'opposent et aucun n'est daté :
`documentation/CHANGEMENTS.md:78-83` annonce **759** arêtes
`SectionHeader → SectionHeader` et 13 220 chemins de longueur 3, « mesuré sur le
corpus de référence » — un corpus qui n'existe plus ; le contrat côté agent
annonce **0** et **0** sur le graphe de production. Seule une ingestion tranche.

**Coût de l'attente** : une campagne d'ablation d'une demi-heure à rejouer, plus
une réingestion complète, puisque le schéma Nebula n'évolue pas en place.

### 3.3 Le test phare de la hiérarchie est vert des deux côtés du défaut

`tests/unit/test_hierarchie_bout_en_bout.py` (branche) **fabrique** l'arbre
imbriqué qu'il prétend vérifier : `enchaine()` (l.59-62) pose
`item.parent = Ref("#/texts/0", parent)` où `parent` est un titre. Le test
prouve que *si* Docling imbrique, *alors* le rang remonte — il ne peut pas
prouver que Docling imbrique. Pire, `test_un_titre_racine_a_le_rang_zero`
(l.81) exerce exactement le cas de production (parent `#/body`) et asserte
`== 0`, lisant le symptôme comme un succès.

### 3.4 L'instrument de mesure de la troncature tokenise le mauvais texte

`index_report.py:75-84` tokenise `documents`, c'est-à-dire le texte **stocké**.
Or `vectors.py:199-203` encode `contextualize(texte, section_title)`, c'est-à-dire
le texte **préfixé du titre de section**. Le rapport annoncera donc 0 %
de troncature alors que le texte réellement embarqué peut dépasser la fenêtre.

Aggravant : `HybridChunker` compte ses tokens sur **sa propre** sérialisation
contextualisée (titres compris). Préfixer un second titre par-dessus peut
refranchir la fenêtre de 128 tokens — exactement la troncature silencieuse que
le passage à `HybridChunker` prétendait supprimer. `supposé`, à mesurer.

**Coût de l'attente** : on croirait avoir supprimé la troncature sans l'avoir
vérifiée, sur l'instrument même censé la voir.

### 3.5 La chaîne d'images HTML n'est prouvée nulle part

`cleaning.py:422-423` réécrit `img src` avec l'URL MinIO ;
`extraction.py:335-337` ne propage cette URL que si `item.image.uri` commence
par `http`. Que le backend HTML de Docling renseigne `image.uri` depuis
l'attribut `src` n'est vérifié par aucun test ni aucune mesure. Si c'est faux,
**aucune image de capture HTML ne porte de `minio_url`** — donc aucune n'est
servie par l'agent, qui ne sert que ce que le graphe référence
(`RESTRICT_MEDIA_TO_GRAPH=true`). `supposé`, à prouver sur un chapitre.

### 3.6 La porte qualité n'est pas reproductible depuis `pyproject.toml`

`pyproject.toml` ne déclare **aucun** groupe de dépendances de développement :
`uv sync` n'installe ni `pytest`, ni `ruff`, ni `mypy`. `requirements-dev.txt`
existe mais n'est référencé ni par `pyproject.toml` ni par le `Makefile`. C'est
pourquoi le chiffre « 407 tests » n'a pas pu être vérifié tel quel.

---

## 4. Ouvert — grave mais inerte tant que le corpus ne bouge pas

### 4.1 Un lot PDF en échec laisse un document partiel écrit dans les stores

`extraction.py:468-487` : sur exception d'un lot, on journalise et **on
continue** ; les lots suivants sont persistés (`storage.persist`, l.485).
L'erreur n'est levée qu'en fin de document (`extraction.py:498-502`), et
`factory.py:198-201` la marque `allow_retries=False`. Résultat : la partition
Dagster est rouge, **et l'ouvrage est dans l'index, tronqué, sans que rien ne
l'en retire**. `verify_contract.py` ne peut pas le voir : les `element_id`
écrits sont valides.

C'est le pire cas de la Partie II sous une autre forme — non pas un run vert
sur un corpus incomplet, mais un run rouge sur des stores incomplets qu'on
croit vides.

### 4.2 Une réingestion d'un document modifié laisse des orphelins

Les identifiants dérivent du texte (`elements.py:153`) : un texte modifié donne
de nouveaux identifiants, les anciens survivent dans ChromaDB comme dans
NebulaGraph. `NebulaWriter.delete_document` (`nebula.py:262-265`) existe et
**n'a aucun appelant**. Or le capteur Dagster déclenche sur `mtime`
(`factory.py:361-372`) : mettre à jour un document est le chemin nominal.

Inerte aujourd'hui (stores vides, corpus figé), fatal dès la première
correction de corpus.

### 4.3 `nebula.py:125` code en dur les identifiants du graphe

`get_session("root", "nebula")`. `NEBULA_USER` et `NEBULA_PASSWORD` existent
dans `.env.example:15-16` et ne sont exposés par **aucun** settings :
`DoclingSettings` ne les déclare pas. Deux autres sites font de même :
`verify_contract.py:104` et `verify_data.py`. Le `.env` ment donc sur ce qui est
réellement lu.

### 4.4 `verify_contract` ne vérifie pas ce qui casse en silence

`src/verify_contract.py` ne teste ni l'existence des arêtes `PARENT_OF`, ni la
**monotonie de `sequence`** (exigence 4), ni que `source_path` est non vide, ni
la cohérence `chunk_index` / `chunk_count`, ni le modèle qui a produit les
vecteurs. L'échantillon de 400 (`verify_contract.py:38-40`) est justifié par
« une rupture de contrat est systématique » : c'est vrai d'un format, faux
d'une monotonie qui se casserait sur un document sur vingt. Phrase
d'exhaustivité (l.12-16) qui clôt une énumération que personne ne rouvre.

### 4.5 `verify_data.py` s'exécute à l'import

Pas de `main()` : le module fait ses entrées-sorties au niveau du module
(`settings = get_settings()`, `failures = []`, puis les contrôles). Intestable,
et un `import` accidentel déclenche les contrôles. `wipe_stores.py` avait le
même défaut sur `main` — la branche le corrige, mais pas `verify_data.py`.

### 4.6 Un nettoyage peut jeter 95 % du texte sans que rien ne le dise

`sources.py:63-66` : `min_text_ratio = 0.05`. `cleaning.py:494` accepte donc un
candidat qui ne conserve que 5 % du texte pré-nettoyé. Aucun seuil haut, aucun
journal — `cleaning.py` n'a pas de logger — et
`factory.py:243-251` ne publie ni `precleaned_bytes` ni le ratio dans les
métadonnées Dagster. La perte est structurellement invisible.

### 4.7 `except Exception` muet dans le choix de stratégie de nettoyage

`cleaning.py:486-489` : `except Exception: candidate = None`, sans justification
écrite au site et sans trace. Une stratégie qui plante devient un non-candidat
silencieux. Violation directe de la règle du dépôt.

### 4.8 Clés MinIO des crops PDF : ni assainies, ni porteuses de l'ouvrage

`images.py:174` : `f"images/{pdf_stem}/{image_id}_{element_type}.png"` où
`pdf_stem` est `identity.filename`, c'est-à-dire le nom **seul**, non assaini.
Deux PDF homonymes dans deux ouvrages écriraient au même endroit, et les
espaces ou caractères pleine chasse partent tels quels dans la clé d'objet.
`images.upload_file` (l.112), lui, assainit — deux conventions dans le même
module. Inerte à un seul PDF.

### 4.9 L'arête légende → illustration ne franchit pas une frontière de lot

`nebula.py:160` : `last_visual_id` est une variable locale de `write_elements`,
donc remise à `None` à chaque lot de pages. Une légende en tête de lot dont
l'image finit le lot précédent perd son arête `LINKED_TO`.

### 4.10 Un doublon exact rend une partition verte avec zéro élément

`extraction.py:132-141` retourne `{"elements": 0, "chunks": 0, ...}` et
`duplicate_of`. Mais `factory._record_metadata` (l.303-313) ne publie ni
`duplicate_of`, ni `pages_skipped`, ni `ocr`, ni `language`, ni
`failed_batches` : dans l'interface Dagster, un document écarté ressemble à un
document ingéré vide.

### 4.11 Le niveau du titre n'est pas stocké dans le graphe

`nebula.py:49` : `VERTEX_PROPERTIES = ("label", "page_no", "text", "minio_url")`.
Ni `depth`, ni `section_title`, ni `reference_id` ne sont écrits sur les
sommets ; `depth` n'existe que dans les métadonnées ChromaDB
(`schemas.py:94-95`). La correction demandée côté agent — « stocker le niveau du
titre sur le tag `SectionHeader` » — n'est pas faite. L'agent peut remonter les
`PARENT_OF`, mais ne peut pas lire un niveau.

### 4.12 Échelles de rang mélangées

`docling_parent_rank` compte à partir de 0 ; `docling_level_rank`
(`ranking.py:83-84`) rend le `level` de Docling, qui part à 1. Un document
offrant les deux signaux sur des titres différents produirait un arbre faux.
Inerte tant que 3.2 tient — le premier signal gagne toujours — et le devient au
moment même où 3.2 est corrigé. À traiter dans le même lot.

### 4.13 `LINKED_TO(relation="describes")` là où le contrat annonce `DESCRIBES`

`nebula.py:184`, `nebula.py:217`, `nebula.py:345`. La documentation d'ici l'écrit
fidèlement (`graphe_connaissances.md:29`, `services/nebulagraph.md:33`) ; celle
de l'agent annonce une arête `DESCRIBES`. L'agent accepte les deux, mais la
divergence n'était documentée d'aucun côté d'ici. Elle l'est désormais.

### 4.14 Le contrat « pas de chevauchement de lots » n'est gardé par aucun test

`extraction.py:453` et `extraction.py:496` réalisent l'absence de chevauchement
(`end_page = min(start + n - 1, range_end)` puis `start_page = end_page + 1`).
Aucun test ne fait régresser ce `+1` : le remplacer par `start_page = end_page`
laisserait la suite verte. Le bug est corrigé à la source, le contrat n'est pas
gardé.

---

## 5. Ouvert — le code mort, et la doctrine qu'il fait mentir

### 5.1 Le découpage : trois valeurs pour un réglage qui n'existe pas

`settings.chunk_size` (450) et `settings.chunk_overlap` (75)
(`settings.py:49-50`), `chunk_text` et `DEFAULT_CHUNK_SIZE` /
`DEFAULT_CHUNK_OVERLAP` (`chunking.py:18-74`), `chunk_ids`
(`chunking.py:105-126`) : **aucun appelant en production**. Seuls
`tests/unit/test_chunking.py` les exercent. Le découpage réel est
`HybridChunker(tokenizer=..., max_tokens=modele.max_seq_length)`
(`vectors.py:79-87`).

Le débat « 900 contre 450 » est donc vide : **les deux sont faux**.
`documentation/services/docling.md:112-113` présente `CHUNK_SIZE=900` et
`CHUNK_OVERLAP=150` comme des variables d'environnement effectives — elles ne
font rien. Et le commentaire de `settings.py:45-48` justifie 450 par une mesure
(« 31 % de troncature à 900, 1,3 % à 450 ») qui documente une constante morte.

### 5.2 `blocks.py` : une doctrine de 33 lignes que la production n'applique plus

Seul `has_content` est importé (`vectors.py:28`, `index_report.py:21`).
`build_blocks`, `Block`, `_family`, `PROSE_LABELS`, `CODE_LABELS` n'ont aucun
appelant. Le docstring du module (l.9-13) énonce « **fusionner plutôt que
jeter** » avec deux garde-fous de section — or la production **jette** :
`vectors.py:138` écarte tout chunk plus court que `min_chunk_chars`.

Le même docstring affirme que `HybridChunker` « n'a pas de `min_tokens` : les
fragments isolés y survivent » — et c'est précisément `HybridChunker` qui a
remplacé `build_blocks`. `documentation/base_vectorielle.md:20` affirme quant à
lui que les fragments isolés « sont absorbés dans leur paragraphe d'origine ».
Les deux ne peuvent pas être vrais.

### 5.3 `ngql.py:130-131` redéfinit un schéma faux

`VERTEX_PROPERTIES` et `DOCUMENT_PROPERTIES` y sont déclarés sans jamais être
importés, et `DOCUMENT_PROPERTIES` y compte **3** champs contre **7** dans
`nebula.py:50-58`. Un lecteur qui ouvre `ngql.py` lit un schéma périmé.

---

## 6. Ouvert — ce que la documentation affirme et que le code ne fait pas

| # | Affirmation | Preuve du contraire | Sévérité |
|---|---|---|---|
| 6.1 | `docling.md:112-113` : `CHUNK_SIZE=900`, `CHUNK_OVERLAP=150` | variables mortes, cf. §5.1 | haute |
| 6.2 | `chromadb.md:28-29` et `llm_integration_plan.md:249` : fenêtre de **256** tokens | `settings.py:45` dit **128** ; `HybridChunker` lit `max_seq_length` | moyenne |
| 6.3 | `chromadb.md:30-32` énumère les métadonnées | il manque `collection`, `source_path`, `language`, `depth` — les clés d'identité du contrat | haute |
| 6.4 | `chromadb.md:35` : « un vecteur par **bloc** » | vocabulaire de `build_blocks`, mort | basse |
| 6.5 | `base_vectorielle.md:20` : fragments isolés « absorbés dans leur paragraphe » | `vectors.py:138` les jette ; `blocks.py:9-13` dit l'inverse | moyenne |
| 6.6 | `llm_integration_plan.md:401` et `:543` prescrivent `cross-encoder/ms-marco-MiniLM-L6-v2` | reranker **anglais** face à un embedder multilingue — la faute exacte que l'agent a mesurée (étendue de scores 0,0 % sur 20 candidats en français) | haute |
| 6.7 | `docling.md:129` : « Ressources : GPU NVIDIA (CUDA 12.1) » | l'ingestion tourne sur processeur ; la réservation en dur (`docker-compose.yml:196-201`) rendait le service **incréable** sans runtime nvidia | haute |
| 6.8 | `docling.md:95-96` énumère les modules « sans dépendance externe » | oublie `blocks.py`, `anchoring.py`, `hierarchy.py`, `ranking.py`, `language.py`, `matter.py` | basse |
| 6.9 | `README.md:317` : volumétrie « mesurée » sur 42 documents (1 PDF de 280 pages, 35 HTML, 6 Markdown) | ce corpus n'existe plus ; l'actuel est 24 HTML + 1 PDF de 73 pages, 0 Markdown | moyenne |
| 6.10 | `extraction_donnees.md:276-280`, `CHANGEMENTS.md:107-113` : chiffres de découpage et de bruit | mesurés sur le corpus disparu, sans réserve ni date | moyenne |
| 6.11 | `CHANGEMENTS.md:78-83` : 759 arêtes `SectionHeader → SectionHeader`, 13 220 chemins de longueur 3 | contredit par le 0 / 0 mesuré côté agent ; cf. §3.2 | haute |
| 6.12 | `Dockerfile.docling:14-18` installe torch depuis l'index CUDA 12.1 | chaîne prévue pour le processeur ; image inutilement lourde | moyenne |
| 6.13 | `docker-compose.yml:167,207` monte `docling_models` | ni `rag_hf_cache` ni `rag_models_cache` — divergence de nommage à consigner, sans conséquence fonctionnelle | basse |
| 6.14 | `README.md:231` : « le modèle qui transforme le texte en vecteurs n'est entraîné que sur de l'anglais » | faux depuis `7b72854` ; c'est le **vestige d'`all-MiniLM-L6-v2`** contre lequel le contrat met explicitement en garde. Le lien qui suit pointe de surcroît vers une ancre disparue (`#limite-mesurée--le-modèle-dembedding-ne-parle-quanglais`) | haute |
| 6.15 | `src/pipeline/schemas.py:87-88` : « le modèle d'embedding actuel n'étant entraîné que sur de l'anglais » | même vestige, **dans le fichier qui est le contrat de référence** | haute |

---

## 7. Ouvert — fond et état de l'art

### 7.1 La vraie limite du découpage est le modèle, pas le découpeur

`paraphrase-multilingual-MiniLM-L12-v2` plafonne à **128 tokens**
(`settings.py:45`, `mesuré` indirectement par la médiane de 91 tokens rapportée
en `CHANGEMENTS.md:110`). Le RAG de 2026 travaille couramment sur 200 à 512
tokens, et les techniques qui dominent — *late chunking*, *contextual
retrieval* — supposent des fenêtres de 512 à 8 192. Le plafond de 128 est donc
structurel, imposé par le contrat, et non un défaut de `HybridChunker`.

Changer de modèle (`bge-m3`, `multilingual-e5-large`, `Qwen3-Embedding`) impose
une réingestion complète et ne se décide pas sans campagne comparative
appariée. **Ce n'est pas le moment** — mais c'est la question à rouvrir dès que
la première campagne de référence existe.

### 7.2 Les figures sans légende restent muettes

`nebula.py:183` ne relie que les `Caption` déjà présents dans le document. Une
figure sans légende est introuvable en recherche sémantique et impossible à
juger pertinente par le modèle, qui n'en voit qu'un marqueur. L'état de l'art :
une description générée par un VLM à l'ingestion, indexée dans ChromaDB. C'est
un chantier, pas un correctif.

---

## 8. Traité

*(vide — le chantier commence)*
