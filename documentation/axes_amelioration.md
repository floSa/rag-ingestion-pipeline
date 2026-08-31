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

## 2. État mesuré du dépôt — 28 puis 29 août 2026

Toutes les valeurs de cette section sont `mesuré`, dans ce dépôt, à la date
qui les accompagne.

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

**Après le lot 0** (branche `claude/rag-pipeline-lot-0-repairs-b232a1`, pointe
`390ce8a`, 8 commits sur `main`) : `make all` passe sur **chacun des 8 commits
pris individuellement** — 395 / 420 / 430 / 430 / 457 / 477 / 508 / 508 tests
verts, `ruff` propre et `mypy --strict` sans erreur partout (`mesuré`). Le
compte canonique de tests vit désormais dans `README.md`, section Tests :
**508**. Stabilité : graine 0 puis graines 1 à 25 → **26/26 vertes sur chacun
des 8 commits** (la graine 0 désactive la randomisation du hachage, c'est un
cas distinct et elle est comptée à part).

**Apres la reparation du lot 0 et sa fusion** (fusion `--no-ff` `b59bf38`,
14 commits : les 8 du lot plus les 6 de la reparation) : `make all` passe sur
**chacun des 6 commits de reparation pris individuellement** — 508 / 508 / 511 /
521 / 532 / **535** tests verts, `ruff` propre et `mypy --strict` sans erreur
partout. Stabilite : graine 0 plus 25 graines aleatoires sur chacun des 6
commits → **156/156 vertes**. Sur le resultat de la fusion lui-meme, verifie par
le pilote avant de pousser : **535 verts**, `ruff` propre, `mypy` « no issues
found in 36 source files » (`mesure`, 29 aout 2026). Le compte canonique reste
dans `README.md`, section Tests.

**508 est un volume, pas une garantie.** Le fichier
`tests/unit/test_hierarchie_bout_en_bout.py` fabrique l'arbre imbriqué qu'il
prétend vérifier et reste vert des deux côtés de son défaut (§3.3) ; il compte
pourtant dans les 508. Le développeur du lot 0 l'a signalé de lui-même plutôt
que de laisser le chiffre parler seul. Toute lecture de ce compte doit porter
cette réserve.

**L'etat non versionne differe d'un poste a l'autre, et ce n'est pas un
detail.** Le corpus et les stores ne voyagent pas avec un clone. Ce qui suit
decrit le poste de reference. Sur le poste `/home/florian/mes_projets/`
(`mesure`, 29 aout 2026) : les stores ne sont **pas** vides — ChromaDB porte la
collection `rag_documents` avec **137 854** vecteurs, MinIO un bucket
`documents` non vide — et le corpus present n'est pas celui decrit ci-dessous :
36 fichiers HTML de deux autres ouvrages, un autre PDF, plus 170 fichiers dans
`Datas/mds/`, l'ancien corpus mixte francais/anglais que le §1 declare mort.
**Avant tout lot qui ingere, verifier le poste plutot que ce paragraphe.**

Corpus en place sur le poste de reference : 24 fichiers HTML (2 ouvrages × 12)
+ 1 PDF de 73 pages (`mesuré`). Parmi les 12 fichiers de chaque ouvrage, `Index.html` est écarté par
le capteur (`matter.py:40`) ; **`Preface.html` ne l'est pas** — « preface » n'est
pas dans `FRONT_BACK_MATTER_TITLES`. Il sera donc ingéré depuis les deux
ouvrages, ce qui est le cas d'école de l'exigence 3.

---

## 3. Ouvert — bloque une mesure

Un défaut qui bloque une mesure passe devant un défaut plus grave mais inerte.

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

**Nuance apportée par l'audit indépendant du lot 0, et retenue par le pilote.**
Le constat ci-dessus est exact sur les faits, mais il ne doit pas se lire
« ce fichier n'apporte rien ». L'auditeur a mesuré sa valeur **marginale** —
suite complète contre suite privée de ce fichier — et trouvé **3 mutations sur
7 que lui seul voit** : `flat_rank` qui ne retombe plus sur `level`, un
paragraphe qui reçoit un rang, un faux titre PDF qui reprend le rang 0. La
couverture est réelle ; c'est la **prétention** du docstring (« des items tels
que Docling les rend ») qui est fausse, pas le fichier entier.

**Avertissement pour le lot 2.** Appliquer la correction §3.2 (`#/body` rend
`None` au lieu de `0`) fait tomber **deux** tests, pas un :
`test_hierarchie_bout_en_bout.py::test_un_titre_racine_a_le_rang_zero` (livré
par le lot 0) et `test_ranking.py::test_a_title_attached_to_the_body_has_rank_zero`
(**antérieur**, déjà sur `main` en `77d4f5b`). Le lot 0 ne crée pas ce verrou,
il le double. Les deux sites sont à amender ensemble.

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

### 4.15 Famine : un run bloqué gèle la réindexation, sur deux jobs

`reindex_job.py` saute tant qu'un run d'ingestion est non terminal, **et**
tant qu'un run de réindexation est en vol (garde ajoutée par la réparation).
Les deux gardes sont justes et partagent le même mode de panne : un run coincé
en `STARTED` — worker tué, daemon interrompu — bloque la réindexation
**indéfiniment**. Aucun délai de garde, aucune alerte.

Le *run monitoring* de Dagster, qui reprend les runs orphelins, est **absent de
`dagster.yaml`**. C'est là que se corrige la famille entière, d'un seul geste,
plutôt qu'au cas par cas dans chaque sensor.

### 4.16 Deux réindexations concurrentes restent possibles, en plus étroit

La réparation a fermé le cas large (le sensor relançait à chaque tick pendant
qu'un run travaillait). Reste la fenêtre étroite : deux évaluations du sensor
qui se croiseraient **avant** qu'un run ne soit enregistré. C'est précisément
pourquoi le `run_key` reste déterministe **à l'intérieur** d'un tick — la
déduplication de Dagster est la seconde ligne. Consigné pour ne pas croire le
cas clos.

### 4.17 La classification des statuts terminaux n'est gardée par aucun test

`reindex_job.py` dérive `STATUTS_EN_COURS` par soustraction des terminaux, et
la soustraction est correcte : vérifié contre le Dagster **épinglé** (1.13.16),
`FINISHED_STATUSES` vaut exactement `{SUCCESS, FAILURE, CANCELED}`. Mais
retirer `CANCELED` de `STATUTS_TERMINES` laisse la suite **verte** : une
ingestion annulée bloquerait alors la réindexation pour toujours.

Aggravant : le docstring écrit « les trois **seuls** états dont un run Dagster
ne revient pas » — une phrase d'exhaustivité, qu'une montée de version de
Dagster peut rendre fausse en silence.

### 4.19 Le refus de démarrer hors contrat n'est prouvé par aucun test

`main.py:93` place le contrôle du modèle d'embedding **hors** du `try` du
préchargement, avant `queue.start()` : le service refuse donc bien de démarrer
sur un modèle hors contrat, et `README.md:78` l'annonce. Mais retirer cette
ligne, ou la déplacer *dans* le `try`, laisse la suite verte.

Le contrat lui-même reste tenu par `get_embedding_model` (gardé). C'est le
**fail-fast** — la propriété que la documentation vend — qui repose sur une
relecture. C'est mot pour mot la leçon du mandat : « un code de sortie
documenté et justifié n'était asserté nulle part ».

### 4.20 `make audit` est rouge, et n'audite pas ce que la porte installe

Deux choses distinctes, toutes deux antérieures au lot 0 (`mesuré`, 29 août
2026) :

- **rouge** : `pip-audit` sort en 1 — `chromadb 0.6.3`, `CVE-2026-45830`,
  `-45831`, `-45833`, **sans version corrective proposée**. Il n'y a donc pas
  de correction à appliquer, mais il y a une décision à prendre et à écrire ;
- **aveugle** : la cible n'audite que les deux `requirements.txt`, ni le groupe
  `dev` de `pyproject.toml`, ni `uv.lock`. Or le lot 0 fait de `pyproject.toml`
  la source de vérité : l'audit vise désormais à côté de ce que la porte
  installe.

`make audit` ne fait pas partie de `make all` : la porte est verte, l'audit est
rouge, et rien ne le rappelle.

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

### 5.4 `main` n'est pas format-propre — quatre fichiers, dont trois réservés au lot 2

`ruff format --check src/` signale **3 fichiers** sur `main` :
`extraction.py:412`, `:442`, `:479` ; `language.py:136-140` ;
`matter.py:134-137` (`mesuré` avec `ruff` 0.11.8 — la version épinglée par
`pyproject.toml` **et** par `.pre-commit-config.yaml` — et 0.16.5, même
résultat ; reconfirmé le 31 août 2026 : « 3 files would be reformatted, 33 files
already formatted »). Ce sont des lignes tenant dans les 100 colonnes mais
pliées à la main.

La correction est cosmétique et sans risque, mais elle touche `extraction.py`,
que le chantier de la hiérarchie réécrit : **à faire dans le lot 2, pas avant.**
Les reformater plus tôt noierait le diff du lot qui compte dans un reformatage
massif.

**Un QUATRIÈME fichier n'est pas format-propre, et il est dans un angle mort.**
`tests/unit/test_wipe_stores.py`, préexistant sur `main` (`mesuré`, 31 août
2026 : `uv run ruff format --check src/ tests/` → « 4 files would be reformatted,
56 files already formatted »). Il n'a rien à voir avec le lot 2. Son angle mort
est triple : `make format-check` est **borné à `src/`** et ne le signale jamais ;
`make format` ne le répare pas, pour la même raison ; mais le hook
`ruff-format --check`, installé depuis le lot 0b, **bloque** tout commit qui le
touche — sans issue automatique. **Le geste, quand ce jour viendra :**
`uv run ruff format tests/unit/test_wipe_stores.py`, dans le commit qui touche ce
fichier et nulle part ailleurs.

**Toute phrase de ce dépôt qui dit « trois fichiers » parle de la portée de
`make format-check`, jamais de l'état du dépôt.** Le lot 0b avait clos cette
énumération sur une portée qui n'était plus celle du garde qu'il installait :
c'est une phrase d'exhaustivité, et c'en est la deuxième de ce lot.

**Le coût du reformatage est MESURÉ, et il est petit.** `uv run ruff format src/`
produit **16 lignes** de diff — 4 ajoutées, 12 supprimées
(`git diff --numstat -- src`) — sur **1 213** lignes dans les trois fichiers
(`wc -l`), à **quatre** endroits, tous des replis de ligne faits à la main
(`mesuré`, 31 août 2026). **Ce n'est pas un « reformatage massif ».** La phrase
qui l'affirmait était surdimensionnée, et le mandat instruisait chaque
conversation à venir de l'accepter sans remesurer. **La décision de ne pas
reformater reste la bonne, pour une autre raison :** trois des quatre endroits
sont dans `extraction.py`, que le lot 2 réécrit, et un diff de formatage mêlé à
cette réécriture se relit mal. C'est un argument de lisibilité, pas de volume.

**Conséquence, assumée et voulue : `make all` est ROUGE sur `main`.** C'est la
moitié de ce constat qui a été fermée par le lot 0b (voir §8) : la porte
**constate** désormais au lieu d'écrire, donc elle dit la vérité sur l'état du
dépôt — et cette vérité est « **quatre** fichiers ne sont pas format-propres,
dont trois que `make format-check` sait voir ». Ne pas éteindre ce rouge avec
`make format` — la marche à suivre est écrite au `README.md`, section Tests.

### 5.6 Trois `except Exception` sans justification écrite au site

`wipe_stores.py:107`, `:115` et `:126` (branche `…restore-5e9fa1`, commit
`50fcb44`) attrapent `Exception` nu, journalisent et poursuivent, sans une
ligne qui dise pourquoi la largeur est voulue. La règle du dépôt l'exige au
site, et §4.7 relève exactement le même défaut dans `cleaning.py:486-489`.

Le cas est ici moins grave qu'en 4.7 : la conséquence est portée par
`wipe_stores.py:135-138`, qui accumule les stores en échec et sort en 1 — une
purge partielle ne passe donc pas pour une purge réussie. Ce qui manque est la
phrase, pas le garde-fou. À traiter avec 4.7, d'une seule main.


---

### 5.7 `ReindexOutcome.metadata_value` a désormais une branche morte

La branche qui rend `"ECHEC — …"` n'est plus atteinte en production : l'asset
lève avant de publier ses métadonnées (§8). Seuls les tests unitaires de
`reindex.py` l'exercent.

Le développeur de la réparation l'a rendue morte, l'a dit, et ne l'a pas
retirée — argument retenu : `request_reindex` est une fonction publique dont le
contrat est « ne lève jamais, dit ce qui s'est passé », et amputer son objet de
retour parce que son unique appelant d'aujourd'hui lève d'abord la coupleraient
à ce consommateur. À trancher avec §5.1 et §5.2, dans le lot du code mort.

### 5.8 Le message de commit de `3eb5aef` porte une affirmation devenue fausse

Il contient la phrase d'exhaustivité « la seule obligation que le contrat
impose au pipeline », corrigée depuis dans le docstring de `reindex.py` (§8).
Le commit n'a **pas** été réécrit : sa porte qualité a été prouvée verte, et la
règle du chantier interdit de réécrire pour un gain cosmétique.

Il existe donc une divergence permanente entre ce message et le code. C'est le
prix connu de la règle, il est assumé, et il est écrit ici pour que personne ne
le redécouvre comme un défaut.

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

Un constat fermé se déplace ici avec le commit qui l'a fermé, il ne s'efface
pas. Le lot 0 a été fusionné dans `main` le 29 août 2026 par la fusion `--no-ff`
`b59bf38` : 8 commits de livraison, puis 6 de réparation exigés par le pilote
après l'audit indépendant. Tous les commits cités sont désormais dans `main`.

### La réparation du lot 0 — six points, exigés avant fusion

L'audit indépendant a jugé le lot bon dans sa conception et a trouvé six
défauts à ses bords. Le pilote a refusé de les reporter au registre : deux
étaient des tests de quelques lignes dans des fichiers que le lot possédait
déjà, et le premier était une régression.

**1 → `33841c9` — une réindexation échouée n'est plus perdue.** C'était le seul
défaut de comportement, et une **régression** : sur `main`, un agent
momentanément injoignable recevait **une tentative par document ingéré**,
dont la dernière pouvait aboutir ; le lot livré lui en donnait **une seule**. Le curseur du sensor avançait à
l'**émission** de la demande, et le `run_key` déterministe
(`reindex-<storage_id>`) interdisait toute reprise — un `run_key` consommé
l'est pour toujours, Dagster le cherche dans tout l'historique. Remettre le
curseur à zéro ne rattrapait rien.

La correction retenue, et le pilote a lu ses alternatives : **le sensor ne
tient plus aucun état.** `update_cursor` a disparu du module. Il compare deux
*faits* lus dans l'historique des runs — le repère de la dernière ingestion
réussie, et celui de la dernière **réindexation réussie** — et le `run_key`
porte la rafale *et* la tentative. L'asset **lève** quand l'appel a été tenté
et n'a pas abouti ; une URL vide ne lève pas, un appel non tenté n'est pas un
appel échoué. Les trois propriétés du contrat sont tenues : une ingestion reste
verte quoi qu'il advienne de l'agent, un échec est retenté **indéfiniment**, et
un échec **rougit son run** — donc apparaît dans les filtres et les alertes,
là où « une métadonnée dans un run vert » ne le faisait pas.

La règle « ne jamais lever » a été rouverte à bon droit : elle venait de
`factory.py`, où une reprise signifiait reconvertir des centaines de pages ;
dans son propre run, une reprise coûte **un appel HTTP**. Le motif a disparu
avec le déménagement.

Prix assumé et consigné : un agent arrêté deux heures produit des runs rouges
pendant deux heures, un par tick. Le levier est `minimum_interval_seconds`
(30 s). Alternatives écartées et argumentées : garder un curseur mais l'avancer
au succès (un état que rien ne réconcilie avec la réalité), une `RetryPolicy`
bornée (**toute reprise bornée réintroduit la perte**), `run_key=None` (aurait
supprimé la seule protection contre deux évaluations concurrentes).

**2 → `bb74750` — le sensor est livré armé, et c'est prouvé.** `default_status=
DefaultSensorStatus.RUNNING` n'était gardé par rien : la retirer laissait 508
tests verts et rendait **tout le lot inerte au déploiement**. Un troisième test
tient les deux autres honnêtes — un sensor témoin déclaré sans le champ ne doit
**pas** être armé —, sans quoi ils resteraient vrais si Dagster changeait sa
valeur par défaut.

**3 → `1c002f2` — les deux moitiés du titre de `7d587b0`, enfin gardées.**
`tests/unit/test_wipe_stores.py` n'importait que les trois fonctions d'aide : `main()`
n'avait **aucun** test, et c'est là que vivent la purge MinIO et le
`sys.exit(1)` sur purge partielle. Testé en **sous-processus**, et l'argument
est bon : le comportement en cause *est* le code de sortie, ce qu'un
`docker compose exec` remonte et ce qu'un `&&` lit. Un import laisserait
attraper `SystemExit` — prouver qu'un objet a été levé, pas que la commande
échoue. Seconde raison, suffisante seule : boucher `chromadb`/`nebula3` dans
`sys.modules` laisserait les bouchons derrière soi et rendrait l'ordre des
tests significatif.

**4 → `3603492` — le `max()` sur plusieurs sources.** `max(reperes)` n'était
gardé par rien : le harnais appelait `build_reindex` avec **un** nom de job,
alors que `sources.yaml` en déclare **trois**. La configuration réellement
livrée n'était testée nulle part.

**5 → `46969bb` — « la seule obligation » corrigée.** `reindex.py` affirmait
être la seule obligation du contrat ; le §0 en énonce **cinq**, et l'exigence 1
est implémentée par `98bb20d`, du même lot. La correction ne recopie pas la
liste — ce serait la même faute d'un cran plus loin : elle dit ce que le module
porte, donne un contre-exemple vérifiable et renvoie ici. Le message de commit
de `3eb5aef`, lui, garde l'affirmation fausse : §5.8.

**6 → `4e3345c` — le chiffre 21 retiré.** Il vivait dans du **code de
production** sans étiquette et ne correspondait à aucun corpus connu. Retiré
plutôt qu'étiqueté, l'argument étant gardé : il ne dépend d'aucun corpus.

**Mesures de la réparation** (`mesuré`) : `make all` vert sur chacun des 6
commits — 508 / 508 / 511 / 521 / 532 / **535** —, balayage de graines
**156/156**, et le compte du README juste **dans chaque commit qui le change**.
Neuf mutations déclarées ; le pilote en a rejoué **huit de ses mains, toutes
rouges**.

**Les commits cités ci-dessous** sont ceux de la livraison initiale du lot.

### 5.5 → traité par le lot 0b — les hooks `pre-commit` sont installés

`.pre-commit-config.yaml` déclarait `ruff`, `ruff-format`, `detect-secrets`,
`trailing-whitespace` et `check-yaml`, et **rien ne les exécutait** : le
framework n'était installé nulle part. `.git/hooks/pre-commit` ne portait que le
contrôle d'identité d'auteur.

**L'arbitrage retenu : le contrôle d'identité devient un hook `repo: local`**, et
son `entry` pointe le script versionné `scripts/git-hooks/pre-commit`. Le fichier
`.git/hooks/pre-commit` — qui ne peut héberger qu'un script — est rendu au
framework, et l'installation tient en un geste :
`make install && uv run pre-commit install`.

La propriété qui fait tenir l'arbitrage : les deux voies d'installation
exécutent **les mêmes octets**. La liste blanche d'adresses n'a qu'un site,
`ADRESSES_AUTORISEES` dans le script versionné, donc elle ne peut pas diverger.
Le mandat §2.1 reste vrai, et il a été réécrit dans le même commit pour donner
les deux voies et dire laquelle prendre.

Écartées, et pourquoi :

- **chaîner à la main dans `.git/hooks/pre-commit`** (identité, puis
  `exec pre-commit run`) : c'est réécrire à la main ce que `pre-commit install`
  génère, dans un fichier **non versionné** — donc un garde-fou qui n'existe
  que sur le poste de celui qui l'a écrit. C'est exactement le défaut que §5.5
  ferme ;
- **`core.hooksPath` vers un dossier versionné** : `pre-commit` refuse
  d'installer quand `core.hooksPath` est positionné, et la valeur est partagée
  par tous les arbres de travail ;
- **déplacer l'identité vers `pre-push` ou `commit-msg`** pour libérer
  `pre-commit` : `pre-push` est trop tard — le commit porte déjà la mauvaise
  adresse, et c'est la réécriture d'historique qui coûte cher. Inutile de toute
  façon : `stages: [pre-commit]` conserve exactement le moment de déclenchement.

**Deux réglages sans lesquels le hook serait creux.** `always_run: true` :
un hook dont la liste de fichiers filtrée est **vide** est *sauté* par le
framework, et le contrôle d'identité ne dépend d'aucun fichier ;
`always_run` rétablit la parité. Et `pass_filenames: false`, le script ne lisant
pas d'arguments mais `git var GIT_AUTHOR_IDENT`.

**Ce paragraphe portait « c'est là la SEULE faiblesse de la voie framework face
au script brut ». C'était faux, et cette phrase a caché la régression R1 pendant
tout le lot.** Il y en avait une seconde, plus large : le hook généré ouvre sa
configuration en chemin **relatif**, donc le contrôle ne valait plus que pour
les arbres de travail dont la configuration le porte. Une phrase d'exhaustivité
clôt une énumération que personne ne rouvre — c'est la leçon du chantier, et
elle s'est appliquée au document qui l'énonce. Voir « R1 » ci-dessous.

**Prouvé par mutation, sur un clone frais** (`mesuré`, 31 août 2026). Le cas
atteignable est le commit sans fichier éligible, `git commit --allow-empty` :

| Configuration | Commit vide portant `…@aosis.net` |
|---|---|
| `always_run: true` (livré) | **refusé**, `rc=1`, « COMMIT REFUSÉ » |
| `always_run` retiré | **accepté**, `rc=0` — le hook affiche « (no files to check) Skipped » et le commit part avec l'adresse professionnelle |

C'est exactement le sinistre d'origine, à un réglage de configuration près.

**`ruff-format` est passé à `--check`.** Sans cela, le premier développeur à
toucher une ligne de `extraction.py` aurait vu le hook reformater le fichier
entier et emporté dans son commit les trois plis réservés au lot 2 (§5.4) : le
défaut que le lot 0b ferme dans `make all`, revenu par la porte du hook. C'est un
écart au fichier tel qu'il était déclaré, assumé et argumenté au site.

### R1 → traitée par la réparation du lot 0b — le contrôle d'identité était devenu conditionnel à la branche

**C'était une régression, et de la famille de défaut qui a coûté un dépôt
entier.** Le lot 0b, tel qu'il avait été livré, a fermé une porte et en a
entrouvert une autre sans l'écrire.

Le mécanisme, en trois lignes :

- **avant** le lot, `.git/hooks/pre-commit` portait le script d'identité. Ce
  fichier vit **hors** de l'arbre de travail : le contrôle valait pour toute
  branche, tout commit détaché, tout `git bisect` ;
- **après** le lot, le contrôle vivait dans `.pre-commit-config.yaml`, un
  fichier **de l'arbre de travail**, et le hook généré l'ouvre en chemin
  **relatif** (`--config=.pre-commit-config.yaml`) ;
- donc tout arbre dont la configuration ne porte pas le hook était désarmé, **en
  silence**. Sur les **111** commits de `main` (`a005172`), **aucun** ne la porte
  (`mesuré` le 31 août 2026 :
  `for c in $(git rev-list a005172); do git show "$c:.pre-commit-config.yaml" | grep -q identite-auteur; done`).

**Mesuré, dans un clone frais monté par `pre-commit install` seul, arbre sorti à
`298c77e`** (31 août 2026) :

```bash
GIT_AUTHOR_EMAIL=florian.horellou@aosis.net \
GIT_COMMITTER_EMAIL=florian.horellou@aosis.net \
git commit --allow-empty -m essai
```

→ `rc=0`, commit **créé**, auteur **et** committer `@aosis.net`. Le hook du
framework tourne bel et bien, et son rapport ne contient **aucune** ligne
d'identité.

**C'est la leçon « une règle survit à son motif », appliquée à un fichier qui
déménage.** La propriété « inconditionnel » du contrôle d'identité ne venait pas
du script : elle venait de l'**endroit** où il vivait. Le script a changé de
place, la propriété est morte dans le déménagement, et rien ne l'a notée — pas
même le développeur qui a écrit, dans le même lot, que le mandat §2.1 devait
rester vrai. La phrase d'exhaustivité du §5.5 ci-dessus a fermé la porte à
double tour.

#### Ce qui a été retenu, et pourquoi pas seulement de la documentation

La réparation documentaire proposée par l'audit — au mandat §2.1, inverser
l'ordre : copie manuelle **d'abord**, `pre-commit install` ensuite, jamais `-f`
— **est correcte et elle est retenue**. Elle laisse `pre-commit` en « migration
mode » et conserve le script en `pre-commit.legacy`, la seule couche indépendante
de la branche. Mesuré : sous ce montage, dans un arbre à `298c77e`, le commit
`@aosis.net` est **refusé**, `rc=1`, HEAD inchangé.

**Elle ne suffisait pas, et pour une raison mesurée plutôt que rhétorique.**
`pre-commit install` **suggère lui-même** le geste qui détruit la couche, dans sa
propre sortie : « `Use -f to use only pre-commit.` ». Une consigne documentaire
qui contredit l'outil qu'elle pilote perd, et elle perd en silence : `-f` ne
produit aucune erreur, seulement l'absence d'une protection. L'inversion de
l'ordre non plus. Une porte dont la seule preuve est une phrase dans un fichier
de 900 lignes n'est pas une porte.

**Ce qui a donc été livré, en plus :**

1. `scripts/installer-les-garde-fous.sh`, versionné, qui fait les deux gestes
   dans l'ordre, ne passe jamais `-f`, et **vérifie son propre résultat** —
   `.git/hooks/<type>` doit être le hook du framework, `<type>.legacy` doit être
   octet pour octet le script d'identité — en sortant en erreur avec la cause
   probable sinon ;
2. `make install` l'appelle. L'installation redevient **un seul geste**, ce qui
   rend vraie la phrase d'exhaustivité que la cible `install` portait déjà
   (« la seule étape d'installation de la porte qualité »), et qui était devenue
   fausse avec le lot 0b ;
3. `tests/unit/test_installation_des_garde_fous.py`, qui **prouve la propriété**
   au lieu de la documenter : un dépôt jetable dont la configuration ne déclare
   pas le contrôle d'identité, armé par le script livré, refuse le commit
   `@aosis.net` — en auteur seul, en committer seul, et sur les deux — et accepte
   une adresse de la liste blanche.

**Écarté, et pourquoi.** `prepare-commit-msg` est le seul point d'accroche commun
à `git commit`, `git revert`, `git cherry-pick` **et** `git merge` (`mesuré`,
mouchards posés sur chaque hook). Il aurait donc pu couvrir d'un geste tout ce
que `pre-commit` laisse passer. Il est écarté sur deux mesures :

- lors d'un `git cherry-pick`, `git var GIT_AUTHOR_IDENT` y rend l'identité
  **locale**, pas celle du commit produit. Mesuré : un commit dont l'auteur est
  `@aosis.net`, cueilli depuis un arbre configuré en `@gmail.com`, se présente au
  hook comme `@gmail.com` et passe. Le contrôle serait **vert sur le défaut** —
  exactement le garde qu'on croit avoir ;
- un refus depuis `prepare-commit-msg` laisse l'arbre sale : mesuré sur
  `git merge --no-ff`, `MERGE_HEAD` reste **présent**, la fusion est en cours
  et il faut un `git merge --abort` ; sur `git revert`, neuf fichiers modifiés
  restent dans l'arbre sans qu'aucun état de git ne le signale.

### D1 → traitée par la réparation du lot 0b — les commits de FUSION n'étaient couverts par rien

Ce n'était **pas** une régression du lot 0b : le script brut avait le même trou.
Mais le geste suivant du chantier est `git merge --no-ff`, et ce commit-là part
sur GitHub, où la liste des contributeurs ne se défait pas.

`pre-commit install` n'installe que le type `pre-commit`. Mesuré le 31 août 2026,
mouchards posés sur chaque hook de `.git/hooks`, ce que chaque geste déclenche :

| Geste | `pre-commit` | `prepare-commit-msg` | `commit-msg` | `pre-merge-commit` |
|---|---|---|---|---|
| `git commit` | oui | oui | oui | — |
| `git commit --amend` | oui | oui | oui | — |
| `git merge --no-ff` | **non** | oui | oui | **oui** |
| `git revert --no-edit` | **non** | oui | **non** | — |
| `git cherry-pick` | **non** | oui | **non** | — |

Mesuré, dans un clone monté selon le lot tel qu'il avait été livré :
`git merge --no-ff` avec `GIT_AUTHOR_EMAIL` et `GIT_COMMITTER_EMAIL` en
`@aosis.net` → `rc=0`, commit de fusion **créé**, auteur et committer
`@aosis.net`, et **aucun hook n'a tourné** — la sortie de `git merge` ne porte
aucune bannière du framework.

**Fermé des deux côtés**, parce qu'une moitié seule aurait été creuse :

- `scripts/installer-les-garde-fous.sh` installe les deux types
  (`--hook-type pre-commit --hook-type pre-merge-commit`) **et** pose la copie
  manuelle sur les deux, donc `pre-merge-commit.legacy` couvre les arbres dont la
  configuration ne porte pas le hook. Sans cette seconde moitié, la fusion serait
  gardée sur la branche du lot et nulle part ailleurs — exactement le défaut R1,
  revenu par la porte de la fusion ;
- `.pre-commit-config.yaml` déclare `default_install_hook_types: [pre-commit,
  pre-merge-commit]` et `stages: [pre-commit, pre-merge-commit]` sur le hook
  d'identité. La clé ne sert qu'à un `pre-commit install` tapé à la main : le
  script passe les types explicitement, l'installation ne devant rien à la
  branche sortie.

Prouvé par mutation sur un **vrai** `git merge`, dans
`tests/unit/test_installation_des_garde_fous.py::TestLesCommitsDeFusionSontCouverts`,
avec son témoin — une fusion portant une adresse de la liste blanche doit passer,
sans quoi le test serait vrai d'un montage qui refuse toute fusion.

**Reste ouvert, et borné : `git revert`, `git cherry-pick`, `git rebase`.** Le
seul point d'accroche commun est `prepare-commit-msg`, et il est écarté sur
mesure — voir R1 ci-dessus, « Écarté, et pourquoi » : il y voit l'identité
**locale**, donc il serait vert sur le défaut, et son refus laisse l'arbre sale.
La fermeture honnête est un hook `pre-push`. **À trancher par le pilote** : le
registre §5.5 avait écarté `pre-push` au motif qu'il est « trop tard, le commit
porte déjà la mauvaise adresse » — vrai, mais ce qui coûte cher n'est pas le
commit local, c'est le **push**, et réécrire un historique non poussé est
gratuit. L'argument mérite d'être rouvert ; il l'est ici, pas tranché.

### C → traitée par la réparation du lot 0b — les hooks contre le corpus versionné

**Ce constat n'est pas né du lot 0b, mais c'est sa fusion qui le rendait
vivant.** Pendant que le lot travaillait, une autre conversation a poussé
`a005172` sur `main` — « data: versionner le corpus, il fait partie de l'identité
du projet » : `Datas/htms/` et `Datas/pdfs/` sortent du `.gitignore`, 25 fichiers,
**55 Mo** (`mesuré`, `du -sh Datas` sur le résultat de la fusion d'essai). Le lot
0b installe la porte. Les deux ensemble donnent un arbre rouge, et c'est
exactement le cas que le mandat §7 fait mesurer : deux branches vertes peuvent
donner un arbre rouge.

Mesuré sur le résultat d'une fusion d'essai `--no-ff` avec `a005172`,
**0 conflit** :

| Fait | Mesure |
|---|---|
| `detect-secrets` refuse le corpus | `rc=1`, deux `Hex High Entropy String` — `Datas/htms/MLOps with Databricks/3. MLflow for Traditional ML.html:94` et `…/4. Model Serving： Architectures and Implementation.html:330`. Faux positifs |
| les hooks d'hygiène écrivent dedans | **24 fichiers sur 25**, **240 lignes** — 216 pour `trailing-whitespace`, 24 pour `end-of-file-fixer` |
| `check-added-large-files` interdit d'étendre | un fichier déjà suivi qu'on modifie : `rc=0`. Un chapitre **nouveau** de 661 ko : `rc=1`, refusé |
| fichiers suivis au-dessus du seuil de 500 ko | **25** |

**Le deuxième fait est le plus grave, et il ne se voit pas.** On ne peut pas
poser de `# pragma: allowlist secret` sur les deux détections : le **contenu** du
fichier entre dans le calcul de `element_id` (contrat, exigences 2 et 3). Et pour
les hooks qui écrivent, le geste naturel — `git add` puis recommit — fait entrer
le fichier **altéré**, au-delà de ce que l'humain a écrit, **sans aucune
erreur**. C'est le sinistre du mandat §2.2 appliqué au contenu au lieu du nom :
deux postes dont les fichiers diffèrent d'un caractère produisent des
identifiants différents, donc des campagnes incomparables, et rien ne le signale.

#### Ce qui a été retenu, et l'hypothèse écartée

**L'hypothèse du pilote — `exclude` sur `detect-secrets` seul — était
insuffisante, et l'audit l'a réfutée** : elle laisse ouvertes les deux autres
familles, dont celle qui écrit. **Retenu : un `exclude: '^Datas/'` au niveau
RACINE**, et non par hook.

La raison est celle du lot tout entier. `pre-commit` applique les motifs
`files`/`exclude` de la racine à la liste de fichiers **avant** de la distribuer
aux hooks : un seul site couvre donc les quatre hooks fautifs **et tous ceux
qu'on ajoutera**. Un `exclude` par hook aurait demandé de se souvenir de le
reporter sur le hook suivant — un garde-fou qui repose sur la mémoire du suivant
n'est pas un garde-fou.

Gardé par `tests/unit/test_hooks_contre_le_corpus.py`, qui reproduit le filtrage
de `pre-commit` (`re.search` du motif sur le chemin) et asserte le motif livré.
Il porte son témoin : un motif trop large — `'.'` — désarmerait la porte entière
en restant vert sur les deux autres tests. Quatre mutations, quatre rouges
ciblés : `'^Datas'` (sans barre, emporte `Datastore/`), `'Datas/'` (sans ancre,
emporte `src/Datas/`), `'^Datas/htms/'` (laisse le PDF dehors), `'.'`.

#### La conséquence du point 3, écrite là où le prochain la lira

Le constat « le corpus ne peut plus être étendu » demandait une réponse, pas
seulement une correction. **Le geste est écrit au `README.md`, section « Le
corpus est hors de portée des hooks, et voici comment l'étendre » :** c'est un
`git add` ordinaire, sans `--no-verify`, sans seuil à relever, sans exception à
ajouter — et c'est précisément l'effet voulu de l'exclusion. Les deux seules
réserves y sont écrites : **ne renommer aucun fichier** (`source_path` entre dans
`element_id`), et les bornes de GitHub (avertissement à 50 Mo par fichier, refus
à 100 Mo — sans objet aujourd'hui, le plus gros fichier du corpus pesant moins
de 1 Mo).

**Ce que l'exclusion coûte, assumé et écrit au site :** un secret réel déposé
sous `Datas/` ne serait pas vu par `detect-secrets`. Accepté — le corpus est une
capture de documentation publique, et l'alternative consiste à altérer les
données de mesure du chantier. La borne est étroite : ce chemin-là, et lui seul.

### La réserve du pilote sur `.env` et `detect-secrets` — CONFIRMÉE

Le §5.5 disait que `detect-secrets` « n'a jamais tourné en garde-fou sur un dépôt
dont le `.env` porte les mots de passe MinIO et PostgreSQL ». La première moitié
était vraie. **La seconde induisait en erreur, et le raisonnement du pilote est
confirmé par la mesure** (31 août 2026) :

- un hook `pre-commit` ne voit que les fichiers **indexés** ;
- `.env` est ignoré (`git check-ignore -v .env` → `.gitignore:2`) et **non
  suivi** (`git ls-files --error-unmatch .env` → échec). Il n'est donc jamais
  indexé, et **installer le hook ne le fera jamais scanner** ;
- `docker-compose.yml` ne porte **aucun** secret en clair : les sept sites
  concernés passent tous par une interpolation `${…}` (`mesuré`,
  `grep -nE "PASSWORD|SECRET|ROOT_USER|_KEY" docker-compose.yml`).

Le gain réel est donc **ailleurs, et il est réel** : empêcher qu'un secret parte
un jour dans un fichier **versionné**. C'est une protection prospective, pas la
découverte d'un secret déjà présent. Écrit comme tel au `README.md` pour que
personne ne la survende.

### La baseline `detect-secrets` — supprimée, et pourquoi

Les deux faits avancés par le pilote sont **confirmés**, et le second est pire
que « non vérifié » : `.secrets.baseline` portait `generated_at`
`2026-04-30T00:15:52Z` (quatre mois) et une seule entrée,
`tests/unit/test_settings.py:44`, `Secret Keyword`, `is_verified: false`.

**Cette entrée était un fantôme.** Elle désignait
une assertion sur `minio_root_password` comparée à une valeur d'essai, ligne
**supprimée le 11 juin 2026** par `b157e84` : le fichier ne compte plus que 36 lignes, il n'a donc pas
de ligne 44, et il ne contient aucun secret (`mesuré`). La baseline mentait
depuis deux mois et demi.

**L'argument accessoire « et elle était `is_verified: false` » est FAUX, et il est
retiré.** En sémantique `detect-secrets`, `is_verified` signifie « vérifié contre
le **service réel** » par un plugin de vérification ; le champ d'audit humain est
`is_secret`. Un `is_verified: false` est donc la valeur normale de presque toute
entrée de baseline, et n'indique aucun pourrissement. **La preuve du fantôme
reste entière** — elle tient à la ligne 44 disparue, mesurée — mais elle tenait
seule, et cet argument-là ne lui ajoutait rien.

Et elle ne mentait pas passivement. Mesuré : `detect-secrets-hook --baseline
.secrets.baseline tests/unit/test_settings.py` **réécrit la baseline** et sort en
**3** — « Please `git add .secrets.baseline` ». Le premier commit touchant ce
fichier aurait donc été refusé pour une raison sans rapport avec lui.

**Tranché : la baseline est supprimée, et `--baseline` retiré des `args`.** Une
baseline est un état que rien ne réconcilie avec le code, indexé par des numéros
de ligne et des hachages qui dérivent tous les deux — c'est la leçon « ne rien
écrire est plus robuste que bien écrire » appliquée à la lettre, et le fantôme en
est la preuve mesurée. Les faux positifs se déclarent désormais **au site**, par
`# pragma: allowlist secret` avec sa justification : un pragma est relu dans le
diff et **meurt avec la ligne qu'il annote**, là où une entrée de baseline lui
survit.

**Ce que `detect-secrets` a trouvé, en tournant vraiment** (`mesuré`, scan de
tous les fichiers versionnés) : **trois** détections, toutes fausses.

| Site | Détection | Verdict |
|---|---|---|
| `.secrets.baseline:130` | `Hex High Entropy String`, `Secret Keyword` | le hachage **de la baseline elle-même**. Ne se produit qu'en scan direct : le hook exclut son propre fichier. Disparu avec la baseline |
| `src/pipeline/reindex.py` | `Secret Keyword` | la constante `API_KEY_HEADER`, dont la valeur est un **nom** d'en-tête HTTP et non une clé. Pragma posé |
| `tests/unit/test_reindex.py` | `Secret Keyword` | l'argument `api_key` d'un test, dont la valeur d'essai est le mot « secret » lui-même. Pragma posé |

**Aucun secret réel n'a été trouvé.** Et les deux faux positifs **bloquaient**
tout commit touchant ces deux fichiers : le garde-fou, tel qu'il était déclaré,
était non seulement inactif mais ininstallable en l'état.

Piège mesuré au passage, et consigné parce qu'il se reproduira : le plugin
`Secret Keyword` **déduplique par hachage** dans un fichier. Poser le pragma sur
la première occurrence a fait apparaître une seconde ligne portant la même
valeur, jusque-là masquée. Un scan après correction n'est pas une politesse.

Second piège : `.secrets.baseline` ne se terminait pas par un saut de ligne, si
bien que `end-of-file-fixer` la réécrivait, ce qui faisait ensuite échouer
`detect-secrets` sur « your baseline file is unstaged ». Deux hooks du même
fichier se défaisaient mutuellement le travail. Sans objet désormais.

### 5.4, première moitié → traitée par le lot 0b — la porte constate au lieu d'écrire

`make all` avait `format` pour première cible, c'est-à-dire `ruff format src/`,
qui **écrit** : la porte réécrivait trois fichiers avant de les contrôler. Une
porte qualité qui écrit dans le dépôt qu'elle contrôle ne contrôle rien — elle
rend vrai ce qu'elle allait vérifier.

Le coût réel n'était pas le reformatage, c'était la **manipulation à se
rappeler** : révoquer `git checkout -- src/` avant chaque commit. Le développeur
de la réparation du lot 0 l'a fait six fois parce qu'il le savait ; le suivant ne
l'aurait pas su et aurait livré du reformatage sans rapport avec son sujet, dans
un commit que personne n'aurait relu pour ça.

`format` (écrit, geste volontaire) et `format-check` (constate) sont désormais
deux cibles distinctes, et c'est `format-check` qui entre dans `all`. Vérifié par
mutation : avec `format` dans `all`, `make all` laisse trois fichiers modifiés
dans l'arbre ; avec `format-check`, l'arbre est intact après la porte
(`mesuré`, 31 août 2026).

`format-check` passe **en dernier** dans `all`. L'ordre ne change pas le verdict
de la porte — elle est rouge dans les deux cas — seulement ce qu'un humain
apprend avant qu'elle ne s'arrête. Placé en premier, il aurait privé tous les
lots à venir du signal de `lint`, `typecheck` et `test` sur `main`, pour les
mois où le rouge de formatage reste ouvert. C'est un écart au « une ligne » que
le registre annonçait, et il est assumé.

Reste ouvert, et détaché : les trois fichiers eux-mêmes (§5.4 ci-dessus, lot 2).

### 4.18 → traité par le lot 0b — les sensors d'ingestion sont livrés armés

`factory.py:335` déclare `default_status=DefaultSensorStatus.RUNNING` sur
**chaque** sensor de source, et rien ne le gardait : retirer la ligne laissait
**535 tests verts** (`mesuré`, 31 août 2026 — suite entière moins la nouvelle
classe, sous mutation). Tout le pipeline était donc livrable à l'arrêt en
silence.

Le garde est décliné de celui du sensor de réindexation
(`bb74750`) dans `tests/unit/test_factory.py::TestLesSensorsDIngestionSontLivresArmes`,
avec les trois précautions qui le rendent non creux :

- il asserte sur l'objet **produit** — `build_source(...).sensor` — et sur celui
  que `definitions.py` **livre** réellement, jamais sur la présence du mot dans
  la source ;
- il boucle sur **toutes** les sources de `sources.yaml`, et **chacun des deux
  tests qui bouclent porte sa propre borne, EN LIGNE**, sur la collection qu'il
  parcourt. C'est la leçon de `3603492` : un harnais qui n'appelle la fabrique
  qu'avec une source laisse les autres sans garde. La borne est **inférieure** et
  non une égalité, pour qu'une quatrième source soit couverte d'office sans
  qu'une phrase d'exhaustivité ne l'interdise ;

  **La livraison initiale avait mis cette borne dans un test à part, et ce test
  ne gardait rien.** Il appelait `load_sources()` de son côté, donc il
  n'observait jamais le harnais des deux autres : `mesuré` le 31 août 2026,
  forcer ce harnais à une liste vide **et** retirer l'assertion en ligne laissait
  **551 tests VERTS**. Il était vert des deux côtés du défaut. Sa couverture
  marginale est de surcroît **nulle** — `mesuré` : `load_sources()` rendu vide
  globalement rougit 10 tests, dont
  `test_sources.py::TestDefaultSourcesFile::test_loads_and_validates`, qui
  asserte exactement les mêmes trois noms. Le test a donc été **retiré** par la
  réparation, et l'assertion en ligne renforcée d'un compte
  (`len(sources) >= 3`) vers un ensemble de **noms**
  (`{s.name for s in sources} >= SOURCES_ATTENDUES`), strictement plus fort : un
  compte reste vert si une source est renommée pendant qu'une autre est ajoutée.
  C'est la leçon « un test qui choisit lui-même son cas doit prouver qu'il l'a
  atteint », et cette preuve doit vivre **là où le cas est choisi**. Le garde du
  lot tient après retrait : la mutation de `default_status` dans `factory.py`
  rougit toujours les deux tests qui bouclent (`mesuré`) ;
- un sensor témoin déclaré **sans** le champ ne doit pas être armé, sans quoi
  les assertions resteraient vraies si Dagster changeait sa valeur par défaut.
  Dagster livre bien `STOPPED` par défaut (`mesuré` sous mutation).

### Les affirmations que le lot 0b avait rendues fausses → corrigées par sa réparation

Le lot 0b a édité en profondeur le `README.md`, le `Makefile`, le mandat et ce
registre. Neuf affirmations en sont sorties fausses ou surdimensionnées. Elles
entrent dans le périmètre de la réparation parce que la règle du dépôt est
« documentation dans le même commit que son code », et parce que ce chantier ne
traque rien d'autre que l'écart entre ce que la documentation affirme et ce que le
code fait.

| Réf | L'affirmation | Ce que la mesure dit | Où c'est corrigé |
|---|---|---|---|
| **D2** | README : les hooks qui écrivent « montrent leur diff » | **faux.** `--show-diff-on-failure` n'est activé nulle part, et **aucune clé** de `.pre-commit-config.yaml` ne peut l'activer — c'est un drapeau de ligne de commande. La sortie se limite à `files were modified by this hook` puis `Fixing <fichier>` (`mesuré`) | README : la phrase est corrigée, et les deux commandes qui montrent le diff sont données |
| **D3** | README : `check-added-large-files` = « aucun fichier > 500 ko » | **faux.** Seuls les fichiers **ajoutés** sont contrôlés ; un fichier déjà suivi qu'on modifie passe quelle que soit sa taille, et le dépôt post-fusion en porte **25** au-dessus du seuil (`mesuré`) | README, tableau des hooks — ligne détachée de `check-yaml` |
| **D4** | Makefile : « `uv sync` … c'est la **seule** étape d'installation de la porte qualité » | **rendue fausse par le lot**, qui en avait fait deux. Redevenue **vraie** : `make install` arme aussi les hooks | Makefile, cible `install` — voir R1 |
| **D7** | README, mandat §2.4, ce registre §5.4 : « trois fichiers » non format-propres | **faux.** Il y en a **quatre** : `tests/unit/test_wipe_stores.py`, préexistant sur `main`, invisible à `make format-check` (borné à `src/`), non réparé par `make format`, mais **bloqué** par le hook `ruff-format --check` | §5.4 ci-dessus, plus README et mandat §2.4. Le geste de sortie y est écrit |
| **D8** | « reformatage massif » qui « noierait le lot 2 » | **surdimensionné.** `mesuré` : **16 lignes** de diff sur **1 213**, à quatre endroits, tous des replis de ligne. La décision de ne pas reformater reste bonne — pour la **lisibilité** du lot 2, pas pour un volume | §5.4 ci-dessus : le récit est remplacé par la mesure |
| **D9** | README : « le dépôt en portait **2** » pragmas | **faux.** Il y en a **3** — `src/pipeline/reindex.py:69`, `tests/unit/test_reindex.py:91` et `:92` (`mesuré`). La troisième est née du piège de déduplication décrit plus haut, et n'avait pas été recomptée | README, avec la commande de comptage |
| **D10** | `is_verified: false` lu comme un signe de pourrissement de la baseline | **faux.** En sémantique `detect-secrets`, `is_verified` signifie « vérifié contre le **service réel** » ; le champ d'audit humain est `is_secret`. C'est la valeur normale de presque toute entrée. **La preuve du fantôme reste entière** — la ligne 44 disparue — mais elle tenait seule | `.pre-commit-config.yaml` et ce registre, aux deux sites |
| **H8** | mandat §6 et §7 : le lot 0b justifié par « un dépôt dont le `.env` porte les mots de passe » | **survente.** Un hook `pre-commit` ne voit que les fichiers **indexés**, `.env` est ignoré et non suivi, donc l'installer ne le fera **jamais** scanner. Le lot avait corrigé cette survente au README et à `SECURITY.md`, **pas au mandat** — le texte le plus copié du chantier | mandat §6 et §7 |
| **H9** | mandat §2.1 : « l'identité, dans chaque arbre de travail » | **faux.** `extensions.worktreeConfig` n'est pas activé, donc `git config user.email` écrit dans `.git/config`, **partagé** : `mesuré`, les quatre arbres de travail lisent le même fichier. Une fois suffit | mandat §2.1, dans le tableau des portées |

### La seconde réparation du lot 0b — les neuf points

La réparation du lot 0b a elle-même été auditée. Les trois bloquants qu'elle
avait fermés — contrôle d'identité inconditionnel, commits de fusion couverts,
corpus soustrait aux hooks — ont été prouvés fermés **deux fois
indépendamment** et ne sont pas rouverts ici. Restaient neuf points, presque tous
documentaires. Ce qui suit les consigne un par un.

#### F3 → le geste canonique du README cassait le jour de la fusion

Le README donnait, pour rescanner tout le dépôt versionné, `git ls-files -z |
xargs -0 … detect-secrets-hook`, précédé de « **Il ne doit rien rendre** ». Deux
sections plus bas, le même fichier annonce que le corpus porte deux faux positifs
`Hex High Entropy String`. **Les deux sections se contredisaient**, et c'est celle
qui promet le vert que le prochain développeur exécute.

`mesuré` le 31 août 2026 **sur le résultat d'une fusion d'essai `--no-ff` avec
`a005172`** (0 conflit) : `rc=123`, deux détections, aux deux emplacements
annoncés — `Datas/htms/MLOps with Databricks/3. MLflow for Traditional ML.html:94`
et `…/4. Model Serving： Architectures and Implementation.html:330`.

La cause est exactement le sujet du lot : `detect-secrets-hook` est appelé **en
direct**, pas par le framework, donc l'`exclude: '^Datas/'` de la racine — que
seul `pre-commit` applique — ne le filtre pas.

**Tranché : c'est la commande qui porte l'exclusion**, `git ls-files -z --
':!Datas/'`, et la phrase « il ne doit rien rendre » est **conservée** parce
qu'elle redevient vraie (`mesuré` sur le même arbre : aucune sortie, `rc=0`, 99
fichiers scannés). L'autre issue — garder la commande et écrire « elle rend deux
détections attendues » — a été écartée : un geste de contrôle dont le vert
attendu est « deux erreurs » ne se relit pas, et la troisième détection réelle
passerait inaperçue.

**Le défaut était invisible depuis la branche**, `Datas/` n'y étant pas versionné :
`git ls-files` ne le nomme pas, et la commande est verte pour une raison qui
disparaît à la fusion. C'est le cas d'école du mandat §7 — deux branches vertes,
un arbre rouge — et il ne se voit qu'en mesurant sur le résultat de la fusion.

**Deux pièges consignés au README au passage**, tous deux mesurés : `xargs`
traduit l'échec du programme qu'il appelle en **123** et jamais en 1, donc un
contrôle écrit `rc = 1` serait vert sur le défaut ; et le code de retour d'un
`cmd | tail` est celui de `tail`.

**Reste ouvert, et petit :** le `:!Datas/` du README et l'`exclude: '^Datas/'` de
`.pre-commit-config.yaml` disent la même chose à deux endroits. Le second est
gardé par `tests/unit/test_hooks_contre_le_corpus.py`, le premier ne l'est pas —
aucun test ne lit le README. C'est la même famille que F7 ci-dessous.

#### F4 et « rien à se rappeler » → le §2.1 du mandat, le texte le plus copié

Deux affirmations fausses vivaient dans la section que le pilote colle dans
**chaque** prompt.

**« Si `uv` manque sur le poste, le script s'exécute seul. »** `mesuré` le
31 août 2026, clone frais,
`env PATH=/usr/bin:/bin sh scripts/installer-les-garde-fous.sh` : **`rc=1`**, et
seules les **deux copies du contrôle d'identité** sont posées —
`.git/hooks/pre-commit` et `.git/hooks/pre-merge-commit`, tous deux le script
d'identité, **aucun** hook du framework, **aucun** `.legacy`. Le script est
honnête sur sa sortie d'erreur (« `uv: not found` », puis « le contrôle
d'identité est copié et actif ; les hooks du framework ne le sont pas ») : c'est
la phrase du mandat qui mentait, pas le code. Un montage à moitié armé est le
pire des trois états — il ressemble au bon.

**« Il n'y a rien d'autre à taper, rien à faire dans un ordre, rien à se
rappeler. »** Il y a **trois** choses, et elles sont désormais numérotées au
§2.1 : `git config user.email` (que la même section demande deux paragraphes plus
bas), relancer `make install` après toute édition de la liste blanche (F13), et
relancer `make install` si le `.venv` visé par le hook disparaît (F6).

C'est la troisième **phrase d'exhaustivité** de ce lot, après « c'est là la SEULE
faiblesse » (qui a caché R1) et « trois fichiers » (D7). Elles se ressemblent
toutes : elles closent une énumération que personne ne rouvre. La liste des trois
gestes est donc écrite **comme ouverte**, avec la consigne d'y ajouter la
quatrième plutôt que de la garder.

#### F6 → l'armement fige un chemin absolu, et rien ne le disait

`pre-commit install` écrit dans le hook généré une ligne
`INSTALL_PYTHON=<interpréteur de l'arbre d'où l'installation a été lancée>`, et
`.git/hooks` est **partagé par tout le clone**. Le dépôt réel pointe aujourd'hui
le `.venv` de l'arbre de travail **temporaire** du lot 0b (`mesuré`,
`grep INSTALL_PYTHON .git/hooks/pre-commit`). Or le mandat §7 prescrit de
supprimer la branche après fusion, donc son arbre.

`mesuré` le 31 août 2026, dans un clone armé par le script livré dont on fait
ensuite disparaître le chemin visé, `PATH` sans `pre-commit` (`which pre-commit`
→ `rc=1` sur ce poste) : le commit est **refusé**, `rc=1`, **HEAD inchangé**, sur
le seul message « `` `pre-commit` not found.  Did you forget to activate your
virtualenv? `` ». Il ne nomme ni le chemin disparu, ni `make install`. Et le
repli `elif command -v pre-commit` du hook généré ne rattrape rien ici, puisque
`pre-commit` n'est pas au PATH.

**C'est fail-closed, donc sans danger pour l'historique** : rien ne part avec une
mauvaise adresse, tout s'arrête. Le défaut était de ne pas être **écrit**. Il
l'est désormais au mandat §2.1 — dans le tableau des portées et dans la liste des
trois gestes — et au `README.md`, section des garde-fous.

**Proposé et NON retenu dans ce diff, à trancher par le pilote.** Rendre le hook
lui-même robuste demanderait d'éditer `.git/hooks/<type>`, un fichier **non
versionné** que `pre-commit install` réécrit : c'est exactement l'anti-patron que
§5.5 avait écarté. La piste qui tient debout est ailleurs, dans
`scripts/installer-les-garde-fous.sh`, qui calcule déjà `--git-common-dir` :
**comparer `git rev-parse --git-dir` et `--git-common-dir`**, et avertir — ou
refuser — quand l'installation est lancée depuis un arbre de travail secondaire,
puisqu'elle grave alors dans le clone entier un chemin qui mourra avec cet arbre.
C'est trois lignes, c'est la doctrine du lot (« un garde-fou qui repose sur la
mémoire du suivant n'est pas un garde-fou »), et le mandat de cette réparation
demandait de le **proposer**, pas de le faire. Il est proposé.

**Conséquence immédiate, pour le pilote :** à l'étape 5 du §7 — « supprimer la
branche, local **et** distant » — s'ajoute « puis relancer `make install` depuis
le clone principal ». Ce n'est pas écrit dans le §7 : ce lot n'y touche pas.

#### F13 → la liste blanche d'adresses a DEUX sites au runtime

Le `README.md` et le mandat §2.1 affirmaient « la liste blanche d'adresses n'a
qu'un site et ne peut pas diverger ». C'est vrai du **dépôt versionné**, et faux
du **montage armé** : `<type>.legacy` est une copie **figée à l'installation**,
là où le hook `repo: local` relit le script versionné à chaque commit.

`mesuré` le 31 août 2026, clone frais armé par le script livré, édition
**commitée** de `ADRESSES_AUTORISEES`, dans les deux sens :

| Édition | Mesure |
|---|---|
| **ajouter** `essai@exemple.test` | commit suivant portant cette adresse : `« Identite d'auteur autorisee ... Passed »` **puis** `pre-commit.legacy` refuse — `rc=1`, HEAD inchangé, et le message énumère l'**ancienne** liste |
| **retirer** `florian_horellou@laposte.net` | commit suivant portant cette adresse : la couche `repo: local` refuse — `rc=1`, HEAD inchangé |

**Fail-closed dans les deux sens** : friction, jamais exposition. C'est pourquoi
le montage n'est **pas** changé — la copie figée **est** la propriété qui rend le
contrôle inconditionnel, et la rendre dynamique la reprendrait. Seule
l'affirmation est corrigée, aux deux sites, avec le geste : **relancer
`make install` après toute édition de `ADRESSES_AUTORISEES`**.

Consigné au passage, parce que c'est ce qu'un lecteur verra : dans ce cas précis,
le message de refus **énumère l'ancienne liste**, donc il paraît contredire le
fichier qu'on vient d'éditer. C'est le seul cas où cela se produit.

### Trouvé par la réparation du lot 0b, et NON traité

- **`git revert`, `git cherry-pick`, `git rebase` créent des commits qu'aucun
  hook ne voit** (`mesuré`, mouchards posés sur chaque hook de `.git/hooks`). Le
  seul point d'accroche commun, `prepare-commit-msg`, est écarté sur mesure : il
  y voit l'identité **locale** et non celle du commit produit — il serait vert
  sur le défaut — et son refus laisse l'arbre sale. **La fermeture honnête est un
  hook `pre-push`, et elle est à trancher par le pilote.** Le §5.5 avait écarté
  `pre-push` au motif qu'il est « trop tard, le commit porte déjà la mauvaise
  adresse ». C'est vrai, mais ce qui est irréversible n'est pas le commit local,
  c'est le **push** : la liste des contributeurs GitHub, une fois constituée, ne
  se défait pas, tandis que réécrire un historique non poussé est gratuit.
  L'argument mérite d'être rouvert. Il ne l'a pas été dans le diff : hors
  périmètre.
- **Quatre fichiers de documentation portent des blancs de fin** —
  `documentation/base_vectorielle.md` (3 lignes),
  `documentation/graphe_connaissances.md` (9), `documentation/orchestration.md`
  (1), `documentation/stockage_objets.md` (1), soit **14 lignes** (`mesuré` sur le
  résultat de la fusion d'essai). Ce n'est **pas** un défaut : le hook
  `trailing-whitespace` les corrigera au premier commit qui les touche, ce qui est
  exactement son travail — contrairement au corpus, un document *doit* être
  normalisé. C'est consigné pour qu'un développeur qui voit ces quatre fichiers
  modifiés dans son `git status` sache d'où ils viennent et ne les révoque pas.
- **`make format-check` est borné à `src/` alors que le hook `ruff-format` voit
  tout ce qui est indexé.** Les deux portées divergent, et c'est la cause de
  l'angle mort D7. Étendre `format-check` à `tests/` rendrait `make all` rouge
  d'un quatrième fichier et **fermerait cet angle mort d'un geste** — mais cela
  demande de reformater `test_wipe_stores.py`, ce que le mandat de la réparation
  interdit explicitement. À trancher avec §5.4.

### 3.6 → traité par `eaa8a8e` — la porte qualité est reproductible

`[dependency-groups] dev` déclaré dans `pyproject.toml`, épinglé comme les
dépendances de production, `ruff` à la version du hook `.pre-commit-config.yaml`.
`requirements-dev.txt` supprimé : il n'était référencé que par une ligne du
README, aucune image ne l'installait, et quatre des douze outils qu'il déclarait
n'avaient aucun utilisateur (`httpx`, `pytest-mock`, `pytest-asyncio` — aucun
test ne les importe — et `pydantic-settings`, déjà dépendance de production).
Le `Makefile` appelle chaque outil derrière `uv run` et gagne une cible
`install` : la séquence tient en `make install && make all`, sans activation
d'environnement.

Restait ouvert et détaché de ce constat : §5.4 et §5.5, tous deux traités par
le lot 0b — sauf les fichiers non format-propres eux-mêmes, qui restent au §5.4
ouvert. Ils sont **quatre**, dont trois pour le lot 2 : voir §5.4, c'est son site
canonique.

### « mypy rouge dès le premier commit » → traité par `98bb20d`

Corrigé **à la source**, sans `type: ignore` ni assouplissement : `main.py` et
`index_report.py` lisent `get_embedding_model` dans `embedding.py`, son module
source, et non par réexport implicite depuis `vectors.py`. La correction est
repliée dans le commit qui introduisait l'erreur — sans quoi la branche aurait
gardé cinq commits rouges. Mutation vérifiée : remettre l'import depuis
`vectors` rend `mypy --strict` rouge.

### 3.1 → traité par `a3ad1f4` — `POST /reindex` une fois par rafale

L'appel quitte `factory._record_metadata`. Il vit dans `src/pipeline/reindex_job.py` :
un asset `agent/lexical_index`, son job `agent_reindex_job`, et un sensor qui
l'arme quand **aucun run d'ingestion n'est en vol** (statuts non terminaux,
`QUEUED` compris) **et** qu'au moins un a réussi depuis la dernière
réindexation, repérée par un curseur. Le nombre d'appels ne suit plus le nombre
de documents : un appel par rafale au lieu d'un appel par partition.

Options écartées, argumentées dans le docstring du module : un asset aval se
matérialiserait dans le run de la partition et ne ferait que déplacer le défaut ;
un `run_status_sensor` sur SUCCESS se déclenche lui aussi une fois par run et
demanderait la même garde pour un harnais de test plus lourd ; un asset check
vérifie, il n'agit pas.

Limite consignée plutôt que découverte : un corpus déposé en goutte-à-goutte
est une suite de rafales, donc une suite de réindexations. C'est le
comportement voulu, ce n'est pas « une seule fois » dans l'absolu.

Le garde qui manquait : les tests assertent un **nombre**, depuis le côté qui
le produit, pour 1, 3 et 12 documents — un test « l'appel a lieu » était vert
des deux côtés du défaut. Treize mutations du code livré ont été vérifiées
rouges. Détail dans `tests/unit/test_reindex_job.py`.

### « 407 tests verts » → traité par `390ce8a`

Le dépôt ne portait **aucun** compte de tests : le 407 vivait hors du dépôt. Il
en a désormais un seul, dans `README.md` section Tests, avec sa commande, sa
date et son étiquette `mesuré` — **508** —, et la consigne d'y renvoyer plutôt
que de le recopier.
