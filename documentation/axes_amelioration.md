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

**Apres la fusion du lot 0b** (fusion `--no-ff` `e998e7d`, 31 aout 2026 :
les 3 commits de livraison, 5 de premiere reparation, 10 de seconde). Verifie
par le pilote **sur le commit de fusion lui-meme** (`mesure`, 31 aout 2026) :
**552 tests verts**, `ruff` propre, `mypy` « no issues found in 36 source
files », `make all` en **2** — le rouge attendu de `format-check` sur les quatre
fichiers plies a la main (§5.4) — et l'arbre **non sali**. La porte sur chacun
des 18 commits pris individuellement, et le balayage de graines, sont aux
rapports des developpeurs ; le pilote a rejoue la porte sur cinq commits et un
sondage de graines, plus la mesure de fusion ci-dessus. Le compte canonique
reste dans `README.md`, section Tests.

**L'etat non versionne a change de perimetre le 31 aout 2026.** Le corpus
**est desormais versionne** (`a005172`, 25 fichiers, 57 381 999 octets
`mesure`) : il voyage avec le clone. Ne restent hors du depot que le `.env`, les
stores et la pile Docker. Sur le poste verifie le 31 aout 2026, **aucun** des
trois n'etait present : ni `.env`, ni conteneur du projet, ni reseau
`rag_network`, ni volume — et le hook d'identite n'etait pas installe. Le poste
avait ete annonce comme le poste d'origine du chantier. **Mesure le poste,
jamais ce paragraphe.**

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

Corpus en place : 24 fichiers HTML (2 ouvrages × 12) + 1 PDF de **71** pages
(`mesuré` le 31 août 2026 par trois voies concordantes — `/Count` de l'arbre de
pages lu dans les octets bruts, `fitz.open()` et `Document.total_pages` écrit à
l'ingestion. **Le chiffre 73 qui vivait ici sous l'étiquette `mesuré` était
faux** : c'est le pire cas, un chiffre erroné sous la plus forte des trois
étiquettes. Un comptage régex brut de `/Type /Page` rend 72 — il compte des
objets, pas des pages, et ne tranche rien.) Parmi les 12 fichiers de chaque ouvrage, `Index.html` est écarté par
le capteur (`matter.py:40`) ; **`Preface.html` ne l'est pas** — « preface » n'est
pas dans `FRONT_BACK_MATTER_TITLES`. Il sera donc ingéré depuis les deux
ouvrages, ce qui est le cas d'école de l'exigence 3.

---

## 3. Ouvert — bloque une mesure

Un défaut qui bloque une mesure passe devant un défaut plus grave mais inerte.

### 3.2 → MESURÉ par le lot 1 et son audit — le graphe n'est PAS plat, et la correction proposée était un no-op

**Fermé le 31 août 2026.** Deux conversations indépendantes ont mesuré, la
seconde sur les **22** chapitres retenus par le capteur et non sur deux.

**Ce que le constat annonçait.** `ranking.docling_parent_rank` (`ranking.py:56-71`)
rend `0`, et non `None`, dès que le premier parent est `#/body` ;
`ranking.flat_rank` (`ranking.py:130-148` — l'ancien renvoi
`extraction.py:370-373` était **périmé**) ne bascule sur `docling_level_rank`
que si le premier signal rend `None`. Donc, *si* Docling n'imbriquait pas les
titres d'une capture SingleFile, tous les titres seraient frères sous le
document. L'antécédent était `supposé`. **Il est faux, sauf sur un chapitre.**

**Mesuré, sur les 22 chapitres HTML, en mémoire et sans écrire dans aucun
store** : **659 titres**, dont **146 de rang 0** et **513 imbriqués** ;
`docling_parent_rank` répond sur **659 sur 659**, et le signal `level` n'est
donc **jamais** consulté. **21 chapitres imbriquent, 1 est plat.**

**Le périmètre exact, à ne pas élargir.** Le chapitre plat est
`Datas/htms/Practical MLflow for Generative AI on Databricks/10. Unifying GenAI Systems with MLflow.html`
— 8 titres, tous `label=title`, tous `parent.cref == "#/body"`, tous `level`
absent, tous de rang 0. **La cause est dans la capture, pas dans le code** :
c'est le seul chapitre retenu qui n'a **aucune balise `<h2>`** (`h1=8`, `h6=1`,
rien entre). Le pilote l'a recompté sur le corpus versionné : le nombre de
titres de rang 0 **égale le nombre de `<h1>`** dans **22 chapitres sur 22**.

**Deux réserves de lecture, mesurées.** Les deux `Preface.html` n'imbriquent
que par des libellés d'admonition (`Tip`, `Note`, `Warning` — des `<h6>`) et
n'ont aucun `<h2>` : leur hiérarchie n'est pas éditoriale. Sur tout le corpus
HTML, **55 des 513 titres imbriqués (10,7 %) sont des admonitions**.

**Le graphe réellement écrit** (3 documents ingérés par le lot 1, `mesuré`,
rejoué par le pilote) : 2 288 sommets, 2 285 arêtes `PARENT_OF`, **159 arêtes
`SectionHeader → SectionHeader` contre 29 `Document → SectionHeader`**, des
chaînes jusqu'à **5 sauts**, et des fils d'Ariane réels — `Getting from Raw
Data to Chunks > Chunking > Embedding window considerations`. L'audit a prouvé
en plus ce que le compte ne disait pas : **0 sommet à deux parents, graphe
acyclique, exactement 3 racines toutes `Document`** — donc aucun sous-arbre
flottant, et l'arbre est bien un arbre.

**Et la correction que ce constat proposait est un NO-OP.** Faire rendre `None`
à `#/body` fait retomber `flat_rank` sur `docling_level_rank`, qui rend `None`
puisque `level` est absent sur **tous** les `label=title` concernés ; puis
`elements.py:272` fait `place(element_id, heading_rank or 0)` → **rang 0, à
l'identique**. Vérifié dans le code par le pilote. La correction n'aurait rien
changé, et l'avertissement de §3.3 demandait de casser deux tests justes pour
l'appliquer.

**Le signal `level` aurait produit un arbre PIRE**, et la phrase d'origine
— « l'attribut `level`, signal fiable du HTML, est écrasé par un signal moins
fiable » — est **inversée** : le signal parent répond sur 100 % des titres et
reproduit exactement l'imbrication des balises `h`, tandis que `level` est
absent sur les 146 titres de tête et vaut **5** sur les 55 admonitions, qui
seraient enfoncées à cinq niveaux.

**Les deux chiffres qui s'opposaient.** `CHANGEMENTS.md:78-83` annonce 759
arêtes titre → titre « sur le corpus de référence », qui n'existe plus ; le
contrat côté agent annonce 0 et 0. Sur 3 documents d'ici : **159**. Aucun des
deux n'est réfuté — ils portent sur d'autres corpus — mais **le 0/0 côté agent
mesure un graphe produit par autre chose que ce code.**

**Conséquence de plan : le lot 2 disparaît.** Pas parce que « le graphe est
imbriqué » — il l'est à 21/22 — mais parce que **sa correction ne fait rien**.
Ce qui en sort vivant est §4.11, qui n'a jamais dépendu de la platitude.

**Projection au corpus complet** (`calculé` — somme des 22 chapitres mesurés en
mémoire et du PDF mesuré dans le graphe, ce n'est **pas** un état du graphe) :
**163** arêtes `Document → SectionHeader` contre **583** titre → titre.

### 3.3 → RETOURNÉ par le lot 1 et son audit — les deux tests assertent le comportement JUSTE

**Fermé le 31 août 2026, dans l'autre sens que celui où il avait été ouvert.**

Ce constat reprochait à `test_hierarchie_bout_en_bout.py::test_un_titre_racine_a_le_rang_zero`
d'« exercer exactement le cas de production (parent `#/body`) et d'asserter
`== 0`, lisant le symptôme comme un succès ». **`== 0` est le comportement
juste** : mesuré, **146 titres sur 659** ont `parent.cref == "#/body"` dans les
22 chapitres, et leur compte égale le nombre de `<h1>` dans 22 chapitres sur 22.
Un titre accroché à `#/body` **est** un titre racine.

L'« avertissement pour le lot 2 » — « appliquer la correction fait tomber
**deux** tests, les deux sites sont à amender ensemble » — instruisait donc de
casser deux tests corrects, **et pour rien**, la correction étant un no-op
(§3.2). Il est **retiré**.

**Ce qui reste vrai, et qui ne se referme pas.** Le fichier **fabrique** bien
l'arbre qu'il prétend vérifier (`enchaine()` pose `item.parent = Ref("#/texts/0", parent)`),
donc son docstring — « des items tels que Docling les rend » — est **faux** :
c'est une prétention à corriger, pas une couverture absente. L'audit du lot 0
avait mesuré sa valeur marginale à **3 mutations sur 7 que lui seul voit**.

### 3.4 L'instrument de troncature tokenise le mauvais texte — mesuré, il sous-compte de moitié

`index_report.py:75-84` tokenise `documents`, c'est-à-dire le texte **stocké**.
Or `vectors.py:186-192` encode `contextualize(texte, section_title)` (l'ancien
renvoi `vectors.py:199-203` désignait la boucle d'`upsert` : **périmé**),
c'est-à-dire le texte **préfixé du titre de section**.

**MESURÉ le 31 août 2026, par le lot 1 puis rejoué par son audit et par le
pilote — trois fois les mêmes chiffres**, sur 773 chunks, fenêtre 128 :

| | médiane | maximum | au-dessus de 128 |
|---|---|---|---|
| texte **stocké**, ce que l'instrument tokenise | 87 | **140** | **8 (1,0 %)** |
| texte **encodé**, ce que le modèle reçoit | 93 | **149** | **16 (2,1 %)** |

**L'instrument sous-compte d'un facteur 2 exactement : 8 annoncés, 16 réels.**
**8 chunks franchissent la fenêtre par le seul préfixe de titre** — l'aggravant
annoncé ci-dessous, désormais mesuré.

**Le registre se trompait en écrivant « 0 % ».** L'instrument annonce **1,0 %**,
et ce 1,0 % est ce qui cachait le défaut : un lecteur y voit un bruit d'arrondi
autour de zéro. Le défaut n'est pas qu'il annonce zéro, c'est qu'il annonce **la
moitié**. Le prompt du lot 1 reprenait ce « 0 % » et affaiblissait la mesure
qu'il commandait — leçon de pilotage, §11 du mandat.

**La mesure porte bien sur le PRODUCTEUR**, et l'audit l'a prouvé depuis le
code : `vectors.py:203-209` écrit dans ChromaDB **le même** `texts` et **les
mêmes** `metadatas` que ceux passés à `contextualize`, et
`settings.embed_section_context` vaut `True` (`mesuré`). Relire `documents` et
`metadatas.section_title` reconstruit donc la chaîne encodée au caractère près.

Aggravant : `HybridChunker` compte ses tokens sur **sa propre** sérialisation
contextualisée (titres compris). Préfixer un second titre par-dessus peut
refranchir la fenêtre de 128 tokens — exactement la troncature silencieuse que
le passage à `HybridChunker` prétendait supprimer. `supposé`, à mesurer.

**Coût de l'attente** : on croirait avoir supprimé la troncature sans l'avoir
vérifiée, sur l'instrument même censé la voir.

### 3.5 La chaîne d'images HTML est ROMPUE — mesuré, 199 images sans `minio_url`

**CONFIRMÉ le 31 août 2026, et la cause est plus radicale que ce constat ne
l'écrivait.**

`cleaning.py:422-423` réécrit `img src` avec l'URL MinIO ;
`extraction.py:335-337` ne propage cette URL que si `item.image.uri` commence
par `http`. **Cette description du code est exacte et trompeuse comme cause** :
le test du préfixe n'est **jamais atteint**, parce que `item.image` vaut `None`.

Mesuré sur le **producteur**, sur les **22** chapitres retenus, en convertissant
en mémoire sans écrire dans aucun store :

| | |
|---|---|
| `<img src="http…">` dans le HTML nettoyé | **199** |
| items `label=picture` rendus par Docling | **199** |
| dont `item.image` non `None` | **0 / 199** |
| dont `item.image.uri` commençant par `http` | **0 / 199** |

`item.source`, `item.references` et `item.meta` sont vides aussi : **l'URL
n'atterrit nulle part d'exploitable**. Seul `captions` est renseigné.

Dans le graphe réellement écrit (3 documents) : **26 images de capture HTML sur
26 sans `minio_url`**, et **26 objets MinIO orphelins sur 39** — téléversés,
payés en place et en temps, et **inatteignables par l'agent**, qui ne sert que
ce que le graphe référence (`RESTRICT_MEDIA_TO_GRAPH=true`). Les 13 seuls
sommets porteurs d'une URL sont **10 `Picture` et 3 `Table`** issus du **PDF**,
produits par un tout autre chemin (`images.py`) : la chaîne PDF fonctionne, la
chaîne HTML non.

**Le périmètre réel est 199 images, pas 26** : 26 est ce que le lot 1 a ingéré,
199 est ce que le corpus porte.

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
**Inerte, et sa condition d'activation annoncée était fausse — mesuré le
31 août 2026.** Le registre disait « le devient au moment même où 3.2 est
corrigé. À traiter dans le même lot ». Faux : sur ce corpus, le signal 1 répond
sur **659 titres sur 659**, le signal 2 n'est **jamais** consulté, et il est
indisponible (`level` absent) **précisément** sur les 146 titres où le signal 1
rend 0. Corriger §3.2 n'active donc rien — et §3.2 est de toute façon un no-op.

**Le vrai déclencheur, à surveiller** : le jour où un document **Markdown**
entre dans le corpus. `sources.yaml` déclare une source `markdown`
(`glob: mds/**/*.md`), `Datas/mds/` est vide et ignoré, et c'est sur le Markdown
que `docling_level_rank` est le signal annoncé. Reste ouvert, inerte, avec cette
condition — pas avec l'ancienne.

**Une seconde protection, non consignée jusqu'ici** : `HeadingStack.place`
dérive la profondeur du **parent** et jamais du rang brut, ce qui absorbe le
mélange d'échelles. Un rang trop **grand** est donc inoffensif ; un rang trop
**petit** dépilerait des ancêtres, et ce cas n'a pas été observé.

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

### 4.21 PDF : 46 % des titres reçoivent un rang de REPLI, pas un rang mesuré

`mesuré` le 31 août 2026, sur le seul PDF du corpus, 86 des 87 titres du graphe
retrouvés par leur taille de police réelle :

| taille | rang | origine | titres |
|---|---|---|---|
| 27,5 pt | 0 | rang **mesuré** | 17 |
| 21,2 pt | 1 | rang **mesuré** | 21 |
| 16,9 pt | 2 | rang **mesuré** | 8 |
| 15,0 pt (= corps) | 3 | **repli `inclassable`** | 39 |
| 11,2 pt (< corps) | 3 | **repli `inclassable`** | 1 |

**40 titres sur 86 (46 %) tombent sur le repli.** Le PDF ne mesure donc que
**trois** niveaux (17 / 21 / 8), et les profondeurs `17/34/30/6` relevées dans le
graphe mélangent trois niveaux mesurés et un niveau d'empilement par défaut :
22 des 30 titres de profondeur 3 et **les 6** de profondeur 4 viennent du repli.
Aucun compteur ne dit combien de titres sont tombés au repli.

Le garde-fou `exceeds_body_size` **fonctionne** — il empêche « OceanofPDF.com »,
à 15,0 pt, de remettre l'arbre à zéro, ce que `ranking.py:192-194` promet — et
son effet de bord est que presque la moitié de l'arbre PDF est un empilement par
défaut, invisible dans le chiffre de profondeur.

**Et le mécanisme n'est pas robuste, argumenté depuis le code.** Ce PDF est une
re-fabrication `calibre 7.4.0` depuis un EPUB : ses tailles sont des multiples
CSS exacts d'un `em` de 15 pt, donc *un niveau = une valeur*. Un PDF composé à
la main rend 16,94 / 16,96 / 17,02 pour un seul niveau logique — trois rangs.
`_pdf_font_profile` (`extraction.py:565-598`) prend **toute** taille arrondie
supérieure au corps comme un niveau, quel que soit ce qui la porte : numéros de
chapitre, lettrines, titres courants, en-têtes de tableau, formules. Chaque
taille parasite consomme un rang et décale tous les vrais niveaux en dessous
d'elle, en silence. `pdf_heading_rank` ne borne jamais le rang ; seul `MAX_DEPTH`
borne la **profondeur**, pas le **rattachement**.

Corollaire éditorial : `27,5 pt` porte à la fois la couverture, la « Revision
History », le « Brief Table of Contents », les titres de chapitre **et** des
sections de premier rang. Le niveau 0 du PDF est mélangé.

### 4.22 Six pages du PDF n'ont aucun élément — leur texte est attribué à la page précédente

`mesuré` : les pages **8, 18, 19, 25, 68, 69** sur 71 n'ont **aucun** élément
dans le graphe, alors que PyMuPDF y lit 1 181 à 1 472 caractères. **Le texte
n'est pas perdu** — 72 316 caractères écrits sur 72 326, soit 100,0 % — il est
**attribué à la page précédente** : le début de la page 8 se retrouve dans un
élément de la page 7, celui de la page 18 dans la 17, 25 → 24, 68 et 69 → 67.

Cause : `page_no` vient de la **première** provenance de l'item, et Docling
fusionne un paragraphe qui enjambe une page. Conséquence : toute citation « page
7 » couvre en réalité 7 **et** 8. Run vert, aucun compteur, aucun signal.

### 4.23 `graph_text_max_chars = 2000` coupe quatre éléments sans le dire

`mesuré` : 4 éléments `text` du PDF font **exactement** 2 000 caractères dans le
graphe. Aucun journal, aucune métrique. ChromaDB n'est pas touché — le découpeur
repart du document Docling — donc **graphe et vecteurs divergent en silence** sur
ces quatre éléments.

### 4.24 → traité par le lot 3 — le plafond est retiré, les deux échelles sont écrites au contrat

**Le constat, tel qu'il était ouvert.** `HeadingStack.place` (`hierarchy.py:91`)
plafonnait la profondeur d'un **titre** à `MAX_DEPTH = 3`, mais **`parent_id`
n'était pas plafonné** : l'arête `PARENT_OF` pointe le vrai parent, donc la
chaîne était plus longue que `depth`. `add_item` donne aux **non-titres**
`depth = profondeur_du_titre + 1`, sans plafond. Résultat dans ChromaDB :
`depth ∈ {1: 92, 2: 238, 3: 345, 4: 98}` — il ne vaut **jamais 0**, et
**`depth = 4` recouvrait les vraies profondeurs 4 ET 5** (`mesuré` par le lot 1
sur 773 chunks).

**Ce qui a été tranché, et pourquoi le plafond ne se défendait pas.** Il ne
bornait aucune structure : `parent_id` n'a jamais été plafonné, donc les arêtes
écrites dans le graphe étaient **les mêmes** avec ou sans lui. Son motif écrit
— « au-delà, un RAG n'y gagne rien : l'objectif est de reconstruire un bloc avec
ses titres parents, pas de reproduire une arborescence complète » — décrivait
une limitation de l'arbre qui n'a jamais existé. **Et il ne tenait pas sa propre
promesse** : un non-titre recevait `profondeur_du_titre + 1` sans plafond, donc
la valeur 4 existait déjà dans ChromaDB alors que le maximum annoncé était 3.
Son seul effet mesurable était de rendre `depth` **non injectif**.

Le plafond est retiré, et `MAX_DEPTH` avec lui. `depth` est désormais le nombre
d'arêtes `PARENT_OF` qui séparent l'élément de la racine de son document.

**Les deux échelles subsistent, et elles sont désormais ÉCRITES.** Retirer le
plafond ne les fusionne pas : sur un titre, `depth` compte les titres au-dessus ;
sur tout autre élément, il vaut celui de son titre + 1. Un paragraphe sous un
titre de premier niveau vaut donc 1, comme un sous-titre. **La valeur seule ne
dit pas quelle échelle on lit — c'est `label` qui le dit**, et c'est écrit au
site du contrat, `schemas.py`, sur `ChunkMetadata.depth`.

Aggravant décisif, toujours vrai : **aucun `section_header` n'est jamais un
chunk** — labels ChromaDB mesurés : `text` 502, `code` 157, `list_item` 79,
`table` 25, `caption` 10, **`section_header` 0**. L'agent ne peut donc **jamais**
lire le niveau d'un titre par ChromaDB. C'est la charge utile de §4.11, que le
même lot ferme en écrivant `depth` sur le sommet du graphe.

**Ce que l'agent doit lire : la chaîne `PARENT_OF`, et le `depth` du sommet.**
La chaîne reste le signal exact — 0 double parent, acyclique, 3 racines
`Document`, tout `SectionHeader` atteignable (`mesuré` par l'audit du lot 1) —
et `depth` en donne désormais la longueur sans avoir à la parcourir.

### 4.25 Les URL du graphe rendent 403 en GET anonyme

`mesuré` : les 13 URL portées par le graphe existent bien comme objets
(`stat_object` avec un client S3 authentifié — 0 URL morte sur 13), et rendent
**`403` en `GET` anonyme**. La forme stockée est `http://minio:9000/documents/…` :
inutilisable sans identifiants et **hors du réseau Docker**. « 0 URL morte »
dépend donc entièrement de la méthode de lecture. À trancher avec l'agent, qui
« ne sert que ce que le graphe référence ».

### 4.26 La pile entière et le seul `.env` du poste vivent dans un arbre de travail — À TRAITER AVANT TOUT

**`mesuré` le 31 août 2026, et c'est un piège armé par le geste que le mandat
prescrit.** Le lot 1 a monté la pile depuis son arbre de travail. Tous les
stores sont des **bind mounts** de cet arbre :

```
projet compose : lot-1-observation-b12761
graphd / metad / storaged  -> <arbre>/Datas/database/nebula/{meta,storage}
chromadb                   -> <arbre>/Datas/database/chromadb
minio                      -> <arbre>/Datas/database/minio
postgres-dagster           -> <arbre>/Datas/database/postgres
docling-service            -> <arbre>/src  et  <arbre>/Datas
.env                       -> n'existe QUE dans cet arbre
```

**Supprimer cet arbre de travail — l'étape 5 du §7 du mandat — détruirait le
graphe, les vecteurs, les objets et le Postgres de Dagster**, et
`Datas/database/` étant dans le `.gitignore`, **aucun garde-fou git ne s'y
oppose**. `git worktree remove` n'y verrait rien à protéger.

Second effet, mesuré : **`docker compose ps` depuis le clone principal ne voit
rien** — 20 avertissements de variables vides et aucune ligne de service. Un
pilote qui interroge la pile depuis le dépôt principal la croit éteinte.

**Le §7 du mandat gagne donc une étape avant toute suppression d'arbre** :
vérifier qu'aucun projet Compose ni bind mount ne l'ancre. Et le §4 doit dire
qu'une pile montée depuis un arbre de travail est invisible depuis le clone
principal.

### 4.27 Piège de mesure : `SHOW STATS` rend 0 sur un space peuplé

`mesuré` : `SHOW STATS` rend 0 partout sur `rag_space`, faute de
`SUBMIT JOB STATS`. Un space qui porte **2 288 sommets** y ressemble à un space
vide. À ranger avec les autres pièges de mesure : les stores s'interrogent par
`MATCH`, `collection.count()` et `list_objects`, jamais par une statistique
qu'aucun job n'a calculée — ni par une taille de dossier.

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
2026, remesuré sur cette révision : `uv run ruff format --check src/ tests/` →
« 4 files would be reformatted, 58 files already formatted »). Il n'a rien à voir avec le lot 2. Son angle mort
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
(`git diff --numstat -- src`) — sur **1 221** lignes dans les trois fichiers
(`wc -l` **avant** le reformatage : le « 1 213 » écrit ici comptait l'arbre
**d'après**, et un diff ne se rapporte pas au tas qu'il a produit), à **cinq**
endroits, dont **quatre** replis de ligne faits à la main et un doublon de ligne
vide dans `_extract_pdf` (`mesuré`, 31 août 2026, `ruff` 0.11.8 avec le
`pyproject.toml` du dépôt — sans lui, `ruff` retombe sur 88 colonnes et le chiffre
n'a plus rien à voir). **Ce n'est pas un « reformatage massif ».** La phrase
qui l'affirmait était surdimensionnée, et le mandat instruisait chaque
conversation à venir de l'accepter sans remesurer. **La décision de ne pas
reformater reste la bonne, pour une autre raison :** trois des cinq endroits
sont dans `extraction.py`, que le lot 2 devait réécrire — lot supprimé, voir §3.2 —, et un diff de formatage mêlé à
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
| 6.9 | `README.md:365` (l'ancien renvoi `:317` était **périmé**) : volumétrie « mesurée » sur 42 documents (1 PDF de 280 pages, 35 HTML, 6 Markdown) | ce corpus n'existe plus ; l'actuel est 24 HTML + 1 PDF de **71** pages, 0 Markdown | moyenne |
| 6.10 | `extraction_donnees.md:276-280`, `CHANGEMENTS.md:107-113` : chiffres de découpage et de bruit | mesurés sur le corpus disparu, sans réserve ni date | moyenne |
| 6.11 | `CHANGEMENTS.md:78-83` : 759 arêtes `SectionHeader → SectionHeader`, 13 220 chemins de longueur 3 | contredit par le 0 / 0 mesuré côté agent ; cf. §3.2 | haute |
| 6.12 | `Dockerfile.docling:14-18` installe torch depuis l'index CUDA 12.1 | chaîne prévue pour le processeur ; image inutilement lourde | moyenne |
| 6.13 | `docker-compose.yml:167,207` monte `docling_models` | ni `rag_hf_cache` ni `rag_models_cache` — divergence de nommage à consigner, sans conséquence fonctionnelle | basse |
| 6.14 | `README.md:231` : « le modèle qui transforme le texte en vecteurs n'est entraîné que sur de l'anglais » | faux depuis `7b72854` ; c'est le **vestige d'`all-MiniLM-L6-v2`** contre lequel le contrat met explicitement en garde. Le lien qui suit pointe de surcroît vers une ancre disparue (`#limite-mesurée--le-modèle-dembedding-ne-parle-quanglais`) | haute |
| 6.15 | `src/pipeline/schemas.py:87-88` : « le modèle d'embedding actuel n'étant entraîné que sur de l'anglais » | même vestige, **dans le fichier qui est le contrat de référence** | haute |

---

### 6.16 `sequence` : trois réserves à écrire au contrat

L'exigence 4 est **tenue** sur l'échantillon du lot 1 : `sequence` est présente
sur **2 285 arêtes sur 2 285**, et l'ordre de lecture est prouvé — trié par
`sequence`, `page_no` ne décroît **jamais**, 0 inversion sur les trois documents
(`mesuré`). C'est un compteur global au document (`DocumentAccumulator._global_order`),
et il **survit aux lots de pages** du PDF.

Trois réserves qu'aucun document ne porte, et dont l'agent peut se tromper :

1. **`sequence` repart à 0 dans chaque document.** Elle n'est donc pas
   globalement monotone : tout « avant / après » doit être **borné au document**.
2. **Elle n'est pas contiguë sous un parent, par construction.** Mesuré : 44 des
   185 parents ont des `sequence` non contiguës, et **44 sur 44** sont exactement
   expliqués par la taille du sous-arbre du frère précédent. Ce n'est pas un
   défaut — c'est un ordre de lecture global, pas un rang sous le parent.
3. **Le plus grand trou entre deux enfants consécutifs d'un même parent vaut
   993.** Un agent qui implémente « la fenêtre d'éléments » comme « les enfants
   de P dont `sequence ∈ [s−k, s+k]` » rendra **silencieusement moins**
   d'éléments que demandé ; un agent qui lit la contiguïté comme un indice
   d'intégrité conclura à une perte.

**Le garde manque aussi.** La propriété que le lot 1 avait d'abord conclue —
« aucun parent ne porte deux fois la même valeur » — est l'**unicité sous un
parent**, et non l'ordre exigé : une numérotation aléatoire distincte par parent
passerait ce test. Le garde à écrire est le second : `page_no` ne décroît pas
dans l'ordre des `sequence`. À traiter avec §4.4.

### 6.17 Chiffres et renvois faux, relevés le 31 août 2026

| Site | Ce qu'il dit | Mesuré |
|---|---|---|
| §2 de ce registre | « 1 PDF de 73 pages » sous l'étiquette **`mesuré`** | **71** — corrigé |
| §6.9 de ce registre | renvoi `README.md:317` | ligne réelle **365** — corrigé |
| §3.2 (avant fermeture) | renvoi `extraction.py:370-373` | c'est la fin de `_detect_document_language` ; le vrai site est `extraction.py:320` + `ranking.py:130-148` — corrigé |
| §3.4 | renvoi `vectors.py:199-203` | c'est la boucle d'`upsert` ; le vrai site est `vectors.py:186-192` — corrigé |
| `pilotage_du_chantier.md` §2.2 | « 73 pages », deux sites | **71** |
| `pilotage_du_chantier.md` §5.1 | `main = 528748d` | la pointe bouge à chaque commit : le SHA a été retiré au profit de la commande qui la mesure |

Vérifiés **justes** et laissés tels quels : `ranking.py:56-71`,
`extraction.py:335-337`, `index_report.py:75-84`, `nebula.py:49`,
`nebula.py:160`, `schemas.py:94-95`, `ranking.py:83-84`.

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
à 100 Mo — sans objet aujourd'hui, mais **pas de loin** : voir F1 plus bas, le
« moins de 1 Mo » écrit ici était faux d'un facteur 6).

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
| **D8** | « reformatage massif » qui « noierait le lot 2 » | **surdimensionné.** `mesuré` : **16 lignes** de diff sur **1 221**, à cinq endroits, dont quatre replis de ligne (le « 1 213, quatre endroits » de cette ligne était lui-même imprécis — voir F2 plus bas). La décision de ne pas reformater reste bonne — pour la **lisibilité** du lot 2, pas pour un volume | §5.4 ci-dessus : le récit est remplacé par la mesure |
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

#### F8 → l'installeur pouvait annoncer un montage qu'il n'avait pas fait

**C'est la forme exacte du défaut que ce lot traque, dans le garde-fou de ce
lot.** La boucle de **vérification** de `scripts/installer-les-garde-fous.sh`
itérait la **même** variable `TYPES` que la boucle d'**armement**. Une boucle sur
une liste vide vérifie zéro chose, et la vérification est vraie.

`mesuré` le 31 août 2026, sur le script tel qu'il était livré, avec `TYPES=""` :
`rc=0`, le message « `Garde-fous armes dans …/.git/hooks :` » suivi d'une liste
**vide**, et **zéro** fichier `.legacy` créé. Le framework, lui, restait
installé : sans `--hook-type`, `pre-commit install` retombe sur
`default_install_hook_types` de la configuration. Le montage avait donc
**exactement l'air du bon**, amputé de la seule couche indépendante de l'arbre de
travail — celle dont la perte a coûté au lot 0b sa fusion au premier tour (R1).

**Fermé par quatre lignes de shell** — un refus si `TYPES` est vide, avant tout
armement — **et gardé par un test**, `TestLeScriptConstateSonPropreResultat::
test_un_installeur_dont_la_liste_de_types_est_vide_est_refuse`, qui mute le
script **livré**. Avant ce test, la propriété n'était attrapée que **par effet de
bord** : la suite rougissait parce que les commits d'essai passaient, pas parce
que quoi que ce soit observait le montage.

Le test porte sa propre borne, en ligne : il asserte que la substitution a
**changé** le texte du script. Sans elle, une réécriture de la ligne `TYPES`
rendrait la mutation inopérante et le test resterait vert sans rien garder — la
leçon « un test qui choisit lui-même son cas doit prouver qu'il l'a atteint ».

##### La même forme, dans le hook d'identité — consignée, NON corrigée

`scripts/git-hooks/pre-commit` porte `for adresse in $email $committer`, non
protégé. Si les deux variables sont vides, la boucle ne tourne pas, et le hook
est vert. `mesuré` le 31 août 2026, clone armé par le script livré :

```bash
env GIT_AUTHOR_EMAIL= GIT_COMMITTER_EMAIL= EMAIL= git commit -m "essai adresse vide"
```

→ `rc=0`, **commit créé**, `auteur=<>` et `committer=<>`, les deux couches du
contrôle affichant « Passed ».

**Non corrigé, délibérément**, et c'est la décision du pilote : vider
`GIT_AUTHOR_EMAIL` est un geste volontaire, au même rang que `--no-verify`, que
le hook documente lui-même comme la sortie de secours. La réserve à garder, pour
que la ligne ne soit pas lue plus grave qu'elle n'est : une adresse **vide** n'est
pas une adresse **professionnelle**, donc ce chemin ne rejoue pas le sinistre
d'origine — il produit un commit que GitHub n'attribue à personne. C'est la
**forme** qui est la leçon, pas la conséquence.

**La leçon, écrite ici pour être relue :** en `sh`, `for x in $liste` sur une
liste vide est un no-op silencieux, et tout compteur d'erreurs initialisé à `0`
en sort inchangé. Chaque fois qu'une boucle **vérifie**, il faut se demander ce
qu'elle rend quand elle ne tourne pas — et le rendre impossible, pas le
documenter.

#### F11 / C6 → le garde du corpus n'avait aucun modèle de la clé `files`

`tests/unit/test_hooks_contre_le_corpus.py` ne lisait que `config.get("exclude")`.
Or `pre-commit` filtre la liste de fichiers par **deux** clés de racine : un
fichier est vu par les hooks si `re.search(files, chemin)` **et** non
`re.search(exclude, chemin)`. `files` vaut `''` par défaut, ce qui matche tout —
et c'est pour cela que son absence ne se remarquait pas.

**Deux mutations survivaient** (`mesuré` le 31 août 2026, suite entière **verte à
550** dans les deux cas) :

| Mutation | Ce qu'elle fait | Ce que les tests disaient |
|---|---|---|
| ajouter `files: '^Datas/'` à la **racine** | les **sept** hooks basés fichiers rendent « no files to check » et un `.py` volontairement sale se commite en `rc=0` (`mesuré` bout en bout, mutation **commitée**, dans un clone armé) | **550 verts** |
| `exclude: '^Datas/\|^scripts/'` | soustrait à **tous** les hooks `scripts/git-hooks/pre-commit` — le contrôle d'identité lui-même — et `scripts/installer-les-garde-fous.sh` | **550 verts** |

La première est la plus large : elle désarme la porte **entière** en laissant
l'`exclude` livré intact, donc en restant verte sur les trois tests du fichier.
La seconde passait parce que la liste témoin `CHEMINS_A_GARDER_SOUS_CONTROLE` ne
portait **aucun** chemin sous `scripts/`.

**Fermées par assertion, et chacune prouvée par mutation :**

- le témoin — renommé `test_le_reste_du_depot_reste_sous_controle`, son ancien nom
  `test_l_exclusion_n_emporte_rien_d_autre` ne décrivant plus ce qu'il fait —
  modélise désormais **les deux clés**, par un `_sous_controle(chemin)` qui
  reproduit le filtrage réel de `pre-commit` ;
- la liste témoin gagne `scripts/git-hooks/pre-commit` et
  `scripts/installer-les-garde-fous.sh`.

Mutation A rougit sur `files « ^Datas/ », exclude « ^Datas/ »` avec sept chemins
nommés ; mutation B sur les deux scripts, et eux seuls. La configuration remise,
les trois tests repassent au vert.

**Relevé au passage, et il compte :** sous la mutation A, `identite-auteur` est le
seul hook qui **tourne encore** — « Passed » — parce qu'il porte `always_run:
true`. Ce réglage, posé pour le commit sans fichier éligible (registre §5.5), se
révèle être aussi ce qui met le contrôle d'identité hors de portée d'un `files`
mal posé. Ce n'est pas une raison de relâcher le témoin : les sept autres hooks,
eux, tombent.

#### F5 → le harnais de test écrivait des hooks hors de son bac à sable

`tests/unit/test_installation_des_garde_fous.py` purge explicitement `GIT_DIR` et
`GIT_WORK_TREE` dans `_git()`, pour que les commits d'essai aillent bien au dépôt
jetable. Mais le **seul** sous-processus du fichier qui **écrit** des hooks —
celui qui exécute l'installeur — ne les purgeait pas.

`mesuré` le 31 août 2026, avec un `GIT_DIR` dans l'environnement de `pytest` :
l'installeur résout `--git-common-dir` sur le dépôt **désigné**, et **quatre**
fichiers y partent — `pre-commit`, `pre-commit.legacy`, `pre-merge-commit`,
`pre-merge-commit.legacy`. Les tests **rougissent** (6 échecs sur 9) : ce n'était
donc pas un faux vert, et c'est la seule raison pour laquelle ce défaut n'a rien
cassé. Mais le harnais écrivait dans **la ressource même que ce lot protège**, et
il l'aurait trouvée déjà armée.

**Fermé par deux `pop`**, les mêmes que `_git()` — et **gardé** par
`TestLeHarnaisResteDansSonBacASable::
test_git_dir_dans_l_environnement_ne_deporte_pas_les_hooks`, qui **désigne** un
dépôt par `GIT_DIR`, lance le harnais, et exige que ce dépôt ressorte intact. Il
porte son témoin — le harnais doit avoir armé **son** bac à sable — sans quoi il
serait vert d'un harnais qui n'installe rien nulle part.

Contrôle d'après-correction, `mesuré` : la même suite, relancée sous `GIT_DIR`,
passe **11/11** et le dépôt désigné ne reçoit **aucun** hook. Avant, elle rendait
6 échecs et 4 fichiers.

**La leçon, et elle est déjà au mandat sous une autre forme :** un harnais de test
peut effacer ce qu'il doit observer — celui-ci pouvait *armer* ce qu'il doit
observer. Quand un fichier de test purge une variable d'environnement à un
endroit, la question n'est pas « pourquoi ici », c'est « **où encore** ».

#### F1 et F2 → deux chiffres faux dans le commit qui corrigeait les chiffres

**F1 — « le plus gros fichier du corpus pèse aujourd'hui moins de 1 Mo ».** C'est
la phrase sur laquelle le `README.md` concluait que les bornes de GitHub ne se
posent pas. Elle est **fausse d'un facteur 6**. `mesuré` le 31 août 2026 **sur le
résultat de la fusion d'essai `--no-ff` avec `a005172`** :

```bash
git ls-files -z -- Datas | xargs -0 stat -c '%s %n'
```

*(Pas de `-I{}` : `xargs -I` change le découpage et n'insère rien si le motif
n'est pas répété en argument — cette commande, écrite d'abord avec `-I{}` sans
`"{}"` derrière, rendait « `stat: missing operand` » vingt-cinq fois. Le `-0`
seul suffit, et il est ce qui compte : les noms du corpus portent des espaces et
un deux-points pleine chasse.)*

| Mesure | Valeur |
|---|---|
| plus gros fichier | **6 362 475 o** — `Datas/htms/Practical MLflow for Generative AI on Databricks/8. Deploying a GenAI Application with MLflow.html` |
| plus petit fichier | **671 707 o** — `…/Practical MLflow…/Preface.html` |
| au-dessus de 1 Mo | **19 sur 25** |
| total | 57 381 999 o, soit les 55 Mo de `du -sh Datas` |

**La conclusion survit, la marge non.** Elle est de **8×** face à l'avertissement
à 50 Mo, pas de 50×. Corrigé aux **deux** sites : `README.md` et ce registre.

**F2 — « 4 files would be reformatted, 56 files already formatted ».** Le chiffre
est **58**, remesuré sur cette révision. Et son défaut n'est pas d'être faux :
**56 est une valeur juste, mesurée sur le mauvais arbre.** `mesuré` par
`git archive 6f554e8 | tar -x` puis `ruff format --check src/ tests/` :
« 4 files would be reformatted, **56** files already formatted » à `6f554e8` —
c'est-à-dire **avant** que la réparation n'ajoute ses deux fichiers de tests.
Le commit qui modifie l'arbre citait donc une mesure prise sur l'arbre d'avant.
Corrigé aux **trois** sites : `README.md`, mandat §2.4, ce registre §5.4.

**C'est cette forme-là qu'il faut retenir**, pas les deux chiffres : *un nombre
mesuré, exact, et périmé par le commit qui le porte*. Un `mesuré` n'est pas une
étiquette de véracité, c'est une étiquette de **provenance** — et une provenance
comprend l'arbre, pas seulement la commande et la date.

##### Le balayage de provenance qu'ils ont rendu nécessaire

Tous les chiffres du diff `6f554e8..566eb35` ont été repassés. Deux autres
portaient le même défaut, plus petits, et ils sont corrigés dans le même commit :

| Chiffre | Écrit | Mesuré | Ce qui n'allait pas |
|---|---|---|---|
| dénominateur du coût de reformatage | « 16 lignes sur **1 213** » | **1 221** | `wc -l` pris sur l'arbre **d'après** `ruff format`. Un diff ne se rapporte pas au tas qu'il a produit : 1 221 − 8 = 1 213, et l'écart **est** le diff lui-même |
| nombre d'endroits reformatés | « **quatre**, tous des replis de ligne » | **cinq**, dont quatre replis | le cinquième est un doublon de ligne vide dans `_extract_pdf`, sans addition en face. `git diff -U0 \| grep -c '^@@'` → 5 |

Les six autres ont été **reconfirmés justes**, avec leur commande :
`3 files would be reformatted, 33 files already formatted` (`src/` seul) ;
**111** commits de `main` dont **aucun** ne déclare `identite-auteur` (boucle
`git show <c>:.pre-commit-config.yaml`) ; **3** pragmas porteurs
(`grep -rn 'pragma: allowlist secret' --include='*.py'`) ; **25** fichiers et
**55 Mo** de corpus (`git ls-files -- Datas`, `du -sh Datas`) ; **16 lignes** de
diff, 4 ajoutées et 12 supprimées (`git diff --numstat`) ; et le compte de tests
du `README.md`, remesuré à chaque commit de cette réparation.

**Un piège de mesure trouvé au passage, et consigné parce qu'il se reproduira :**
`ruff format` sur un arbre extrait **sans** le `pyproject.toml` du dépôt retombe
sur ses 88 colonnes par défaut au lieu des 100 déclarées, et rend alors
« 2 files reformatted » et **66** lignes de diff au lieu de 3 et 16. Un
reformatage se mesure **avec la configuration du dépôt**, jamais sur les fichiers
seuls.

#### Le « `make` n'est pas installé » du §2.4 — supprimé par `9f5a78c`, et pourquoi

Le mandat portait, en §2.4 : « **`make` n'est pas installé sur le poste de
développement, et il n'y a pas les droits pour l'y mettre** (`mesuré`, 31 août
2026). Chaque conversation le redécouvre ; ce n'est pas un incident, c'est
l'environnement. » C'est **faux sur ce poste** : GNU Make 4.4.1, `/usr/bin/make`,
et `make install` rend 0 dans un clone frais (`mesuré`, 31 août 2026,
indépendamment par le pilote et par l'auditeur). Le deuxième commit du lot 0b,
`9f5a78c`, l'a remplacé par « la présence de `make` est un fait de POSTE :
mesure-la, ne la lis pas ici », avec les deux mesures contradictoires citées côte
à côte. **C'était le bon geste**, et la suppression survit à la fusion sans
conflit — `main` n'a pas retouché ce paragraphe depuis.

**Deux réserves du mandat de cette réparation sont ici renversées, et il faut le
dire :**

- « **sans le déclarer** » — non. `9f5a78c` le déclare dans son message de
  commit, en propres termes : « Corrige aussi le mandat §2.4 sur un point qui
  n'est pas le mien mais qui vivait dans le même paragraphe : "make n'est pas
  installé" y était écrit comme un fait du chantier alors que c'est un fait de
  POSTE. » Et le paragraphe de remplacement le déclare **au site**. Ce qui
  manquait n'était pas la déclaration : c'était **la trace au registre**, qui ne
  porte cette correction dans aucune de ses tables — ni dans « Les affirmations
  que le lot 0b avait rendues fausses » (D2 à H9), ni ailleurs. Elle y est
  désormais ;
- « **le §2.4 fusionné n'aura plus de repli pour un poste sans `make`** » — non
  plus. `9f5a78c` a conservé le repli, et l'a même corrigé du défaut qui aurait
  compté : la recette de `main` commençait par `uv run ruff format src/`, qui
  **écrit** dans l'arbre — c'est précisément ce que ce lot interdit à la cible
  `all` — et la recette de la branche est passée à `ruff format --check src/`,
  en dernière position.

**Mais le repli était faux pour une autre raison, et celle-là est réelle.** Il
prétend remplacer `make install && make all` ; il ne portait que `uv sync`,
c'est-à-dire la **première** ligne de la cible `install`, pas la **seconde** —
`sh scripts/installer-les-garde-fous.sh`, celle que ce lot ajoute et sur laquelle
tout le reste repose. Un poste sans `make` suivant cette recette se retrouvait
donc **sans aucun garde-fou** : ni contrôle d'identité, ni `detect-secrets`.
C'est le défaut du lot 0b tout entier, revenu par la porte de sa propre recette
de repli.

`mesuré` le 31 août 2026, clone frais : après le repli tel qu'il était écrit,
`.git/hooks` ne porte **aucun** hook ; après le repli corrigé, les **quatre**
attendus, et la suite rend `lint=0`, `typecheck=0`, `test=0`, `format-check=1`
— le rouge connu. Le repli du §2.4 porte désormais la ligne manquante.

#### Trouvé par la SECONDE réparation, et NON traité — consigné, pas corrigé

Décision du pilote, pour arrêter la spirale : ce qui suit est **consigné et non
corrigé**. Le périmètre de cette réparation était les neuf points ci-dessus.

##### F7 — la seule ligne dont dépend tout le reste n'est gardée par aucun test

`Makefile`, cible `install`, seconde ligne :
`sh scripts/installer-les-garde-fous.sh`. **Tout ce que ce lot installe dépend
d'elle, et rien ne la garde.** `mesuré` le 31 août 2026 : la retirer laisse
**552 tests verts** ; et `grep -rn Makefile tests/` ne rend **rien** — aucun test
de ce dépôt ne lit le `Makefile`.

C'est la forme la plus pure du défaut que le lot traque : la porte est prouvée,
le garde est prouvé, et **l'interrupteur qui les allume ne l'est pas**. Les onze
tests de `test_installation_des_garde_fous.py` exécutent le script **directement**,
jamais par la cible qui le lance en vrai.

**Pourquoi ce n'est pas fermé ici.** Le garde honnête serait un test qui lance
`make install` dans un dépôt jetable et constate les quatre hooks. Il coûte un
`uv sync` complet par exécution — plusieurs minutes, et le réseau — dans une
suite qui tourne en 9 secondes et ne sort jamais du disque. La variante bon
marché — lire le `Makefile` et asserter que la cible `install` contient la chaîne
`installer-les-garde-fous.sh` — est un test sur du **texte**, pas sur un
comportement : il resterait vert si le script était renommé, déplacé, ou rendu
non exécutable. Aucune des deux ne vaut d'être livrée sans que le pilote
tranche ce qu'il accepte de payer. **À trancher.**

##### Douze mutations survivantes sur l'installeur et la configuration

Le mandat de cette réparation demandait de consigner « **I5, I8, C8, C9, C10** —
cinq mutations survivantes relevées par l'auditeur ». **Ces références n'ont pas
pu être retrouvées** : le rapport d'audit ne vit ni dans le dépôt, ni dans aucun
arbre de travail (`mesuré`). Recopier cinq étiquettes dont j'ignore le contenu
aurait produit un registre qui cite des preuves qu'il n'a pas. **Le balayage a
donc été refait de zéro**, et il en rend douze, chacune `mesuré`e — suite entière
verte, **552** — sur le code **livré**, mutation appliquée puis révoquée :

| Réf | Mutation | Ce qu'elle emporte |
|---|---|---|
| **C-a** | retirer `always_run: true` du hook `identite-auteur` | le contrôle d'identité redevient **sauté** sur un commit sans fichier éligible. C'est le sinistre d'origine à un réglage près — et le §5.5 en donne une table « prouvé par mutation » qui est une **mesure à la main**, pas un garde |
| **C-b** | retirer `stages: [pre-commit, pre-merge-commit]` | la couche framework cesse de couvrir les commits de fusion |
| **C-c** | retirer `pass_filenames: false` | le script reçoit une liste de fichiers qu'il ne lit pas — inerte aujourd'hui, piège demain |
| **C-d** | `ruff-format` sans `--check` | **le hook se remet à RÉÉCRIRE l'arbre indexé.** C'est le défaut que le lot ferme dans `make all`, revenu par la porte du hook — et rien ne le voit |
| **C-e** | retirer `default_install_hook_types` | un `pre-commit install` tapé à la main n'installe plus que `pre-commit` |
| **C-f** | retirer **entièrement** le dépôt `detect-secrets` | le garde-fou dont l'absence justifiait le lot entier disparaît |
| **C-g** | `check-added-large-files` sans `--maxkb=500` | le seuil retombe au défaut de l'outil |
| **I-a** | affaiblir `grep -q 'generated by pre-commit'` en `grep -q ''` | la moitié « le framework est bien là » de la vérification devient décorative |
| **I-b** | `erreurs=1` → `erreurs=0` dans cette même branche | idem, par l'autre bout |
| **I-c** | `--git-common-dir` → `--git-dir` | l'installation depuis un **arbre de travail secondaire** arme `.git/worktrees/<nom>/hooks`, que git n'exécute pas. **Le harnais ne peut pas l'atteindre** : il monte un dépôt simple, où les deux valeurs sont identiques |
| **I-d** | retirer le `chmod +x` | la copie manuelle n'est plus exécutable ; `pre-commit install` ne la déplace donc plus en `.legacy` |
| **I-e** | retirer la garde « `$identite` introuvable » | le message de cause probable disparaît (`set -eu` arrête quand même) |

**Trois d'entre elles méritent d'être lues comme des constats, pas comme des
trous de couverture :** C-a et C-d rouvrent chacune un défaut que ce lot a fermé
ailleurs, et I-c est un cas que le harnais actuel **ne peut pas** atteindre —
fermer celui-là demande un dépôt d'essai avec un `git worktree`, ce qui est un
harnais différent, pas une assertion de plus.

##### Six affirmations imprécises — références non retrouvées

Le mandat listait « **F15, F16, F17, F18, F19, F20** — six affirmations
imprécises ou périmées ». Comme ci-dessus, **leur contenu n'a pas pu être
retrouvé**. Ce qui a été trouvé par relecture est corrigé plus haut (F1, F2, plus
les deux du balayage de provenance) ou consigné ici. **Si le pilote détient ces
six références, elles restent à verser.**

##### Les gestes que rien ne couvre — et un qui l'est, contre l'attente

`git revert`, `git cherry-pick` et `git rebase` créent des commits qu'aucun hook
ne voit. Rien de neuf : c'est écrit sous R1 et D1, avec le motif du rejet de
`prepare-commit-msg` (il y voit l'identité **locale**, il serait donc vert sur le
défaut ; et son refus laisse l'arbre sale).

**Ce qui est neuf, et mesuré le 31 août 2026 :** une **fusion AVEC conflit** est
**couverte**, et pas par le hook qu'on croit. `pre-merge-commit` ne tourne que
lorsque la fusion s'auto-commite ; une fusion conflictuelle s'achève par un
`git commit`, donc par le hook **`pre-commit`**. Mesuré dans un clone armé, sur
un vrai conflit de contenu résolu à la main, avec `@aosis.net` en auteur et en
committer : `rc=1`, **HEAD inchangé**, les **deux** couches refusant —
`pre-commit.legacy` d'abord, puis `identite-auteur` du framework (« Failed »).

À noter au passage, parce que cela vaudra pour d'autres hooks : `pre-commit`
affiche alors « `[INFO] Checking merge-conflict files only.` » et restreint sa
liste de fichiers. Le contrôle d'identité y survit **grâce à `always_run: true`**
— voir C-a ci-dessus.

Les tables de couverture du `README.md` et du mandat §2.1 ne sont pas **fausses**
(« `git merge --no-ff` → oui ») mais elles nomment un seul mécanisme là où il y
en a deux. Non corrigé : hors périmètre.

##### `pre-push` — le constat est rouvert, et il n'est pas tranché

L'auditeur rouvre l'argument contre le §5.5, et **le pilote le trouve fort sans
le trancher dans ce lot** : « ce qui est irréversible n'est pas le commit local,
c'est le **push** ». Le §5.5 avait écarté `pre-push` au motif qu'il est « trop
tard, le commit porte déjà la mauvaise adresse » — c'est vrai, mais réécrire un
historique **non poussé** est gratuit, là où la liste des contributeurs GitHub,
une fois constituée, ne se défait pas. C'est exactement le sinistre d'origine.

**La réserve de l'auditeur, à ne pas perdre :** `pre-push` **ne couvrirait pas**
les commits locaux d'un `git bisect`, ni ceux qu'on ne pousse jamais. Ce serait
donc un filet **en plus** des deux couches, pas à leur place.

**Constat ouvert.** Il ferme d'un geste les trois lignes « non » des tables de
couverture — `revert`, `cherry-pick`, `rebase` — ce qu'aucune autre piste ne
fait.

##### `_run_legacy` et la montée de git — à surveiller

Toute la réparation R1 tient sur la couche `<type>.legacy`, c'est-à-dire sur
`_run_legacy` de `pre-commit`. Ce code porte, dans la version installée
(`pre-commit` 4.6.2,
`.venv/…/pre_commit/commands/hook_impl.py:36`) :

```python
    if hook_dir is None:  # git 2.54+ hooks
        return 0, stdin
```

Autrement dit : **quand `hook_dir` est absent, la couche `.legacy` n'est pas
exécutée du tout**, et `_run_legacy` rend 0. Le hook généré aujourd'hui passe
toujours `--hook-dir "$HERE"`, et le poste est en **git 2.53.0** (`mesuré`), donc
la branche est morte ici. Elle ne le restera pas : son commentaire annonce
explicitement le mécanisme de hooks de git 2.54+.

**Ce qu'il faut faire, et quand.** À la première montée de git ou de
`pre-commit`, remesurer que le commit `@aosis.net` est toujours refusé **dans un
arbre dont `.pre-commit-config.yaml` ne déclare pas le contrôle** — c'est
exactement ce que `tests/unit/test_installation_des_garde_fous.py` fait, donc la
suite le dira. C'est le seul endroit du dépôt où une montée de version d'outil
peut désarmer R1 en silence.

##### `test_wipe_stores.py` — le quatrième fichier, dans son angle mort

Rappel, sans changement : `tests/unit/test_wipe_stores.py` n'est pas
format-propre, `make format-check` est borné à `src/` et ne le voit pas,
`make format` ne le répare pas, et le hook `ruff-format --check` **bloque** tout
commit qui le touche. Le geste reste
`uv run ruff format tests/unit/test_wipe_stores.py`, dans le commit qui touche ce
fichier et nulle part ailleurs. Détail complet au §5.4.

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
