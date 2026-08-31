# Piloter le chantier d'audit et de refonte

Ce fichier est le **mandat du pilote**. Il est écrit pour être lu par une
conversation qui n'a aucun historique : elle arrive, elle lit ceci, elle sait
où on en est, ce qui a été décidé, pourquoi, et quelle est l'action suivante.

Il est autosuffisant. Rien de ce qui suit ne suppose d'avoir vu une
conversation précédente.

Son compagnon obligatoire est [`axes_amelioration.md`](axes_amelioration.md),
le registre : ce fichier-ci dit **comment on travaille**, le registre dit **ce
qu'il reste à faire**. Les deux se tiennent à jour lot par lot.

> **Dernière mise à jour : 29 août 2026, après la fusion du lot 0.**
> Toute valeur chiffrée ci-dessous porte son étiquette `mesuré`, `calculé` ou
> `supposé`. Une valeur non remesurée ne se recopie pas : on renvoie à son site
> canonique.

---

## 0. Reprendre le chantier en une manipulation

Ce fichier vit sur `main`. Un clone frais le contient : il n'y a **aucune
branche à checkouter** pour reprendre.

Le prompt à coller dans une conversation neuve, tel quel :

> Tu es le pilote d'un chantier d'audit et de refonte sur
> `rag-ingestion-pipeline`. Le dépôt est le clone local de
> `git@github.com-perso:floSa/rag-ingestion-pipeline.git`, sur `main`.
>
> Lis ces deux fichiers EN ENTIER avant de dire quoi que ce soit :
> `documentation/pilotage_du_chantier.md` (ton mandat : ton rôle, l'état du
> chantier, le plan de lots, les conventions, les leçons, et en annexe A le
> prompt prêt à distribuer) et `documentation/axes_amelioration.md` (le
> registre : le contrat avec `rag-agent-chat` en tête, puis les constats
> ouverts et traités).
>
> Ils sont autosuffisants : tu n'as aucun historique de conversation, et tu
> n'en as pas besoin. Vérifie ensuite l'état de tes mains plutôt que de me
> croire — les branches, l'état non versionné du poste (corpus et stores), et
> `make install && make all` sur `main` — puis dis-moi où on en est et quelle
> est la prochaine action. Un prompt à la fois, et numérote les conversations
> auxquelles tu me demandes de coller un prompt.

Puis les trois choses qui ne voyagent pas avec un clone : §2.

---

## 1. Ce que tu es, et ce que tu ne fais pas

Tu es le **pilote**. Tu n'écris pas le code.

Tu écris des **prompts** que l'utilisateur colle dans d'autres conversations,
et il te rapporte leurs sorties. Tu audites, tu décides, tu tranches les
fusions, tu tiens le registre.

Ce que tu fais toi-même, sans le déléguer :

- **lire le code avant d'affirmer ce qu'il fait.** Avec
  `git show <branche>:<fichier>`, pas avec `cat` : sur ce dépôt, un arbre de
  travail a longtemps porté le contenu de juin sur un `HEAD` d'août ;
- **faire tourner la porte qualité de tes mains** avant toute fusion, et lire
  le diff complet ;
- **tenir le registre** : ce qui est corrigé, ce qui reste ouvert, et pourquoi.

Ce que tu ne fais pas :

- écrire ou modifier du code de production ;
- fusionner un lot avant son audit indépendant. Le pilote initial l'a fait une
  fois et l'a reconnu prématuré ; sur les six lots du dépôt jumeau, l'audit
  indépendant a trouvé **chaque fois** quelque chose de matériel que ni le
  développeur ni le pilote n'avaient vu ;
- fusionner un lot qui introduit une régression, même petite.

---

## 2. Reprendre sur un poste de travail neuf

Trois choses **ne voyagent pas** avec un `git clone`. Les oublier a déjà coûté
un dépôt entier.

### 2.1 Le garde-fou d'identité Git — À FAIRE EN PREMIER

Sept commits sont partis avec une adresse **professionnelle** `@aosis.net` sur
un dépôt **personnel**. Il a fallu réécrire 165 commits **puis détruire et
recréer** le dépôt GitHub : la liste des contributeurs, une fois constituée, ne
se défait pas.

Deux identités portent le même **nom**, « Florian Horellou ». **Ne vérifie
jamais une identité sur le nom, toujours sur l'adresse.**

**Un seul geste, et il vérifie son propre résultat :**

```bash
make install
```

`make install` fait `uv sync`, puis `sh scripts/installer-les-garde-fous.sh`,
qui arme les hooks git **et sort en erreur si le montage n'est pas celui qu'il
annonce**. Il n'y a rien d'autre à taper, rien à faire dans un ordre, rien à se
rappeler. Si `uv` manque sur le poste, le script s'exécute seul :
`sh scripts/installer-les-garde-fous.sh`.

**Ce que le script monte, et pourquoi l'ordre compte.** Le contrôle d'identité
vit sous `scripts/git-hooks/pre-commit`, versionné, donc il arrive avec le clone
— mais **inactif tant qu'il n'est pas installé**, git n'exécutant jamais ce qui
arrive avec un dépôt. Le script le copie d'abord dans le répertoire des hooks,
**puis** lance `pre-commit install`, qui déplace cette copie en
`pre-commit.legacy`, continue de l'exécuter **avant** ses propres hooks, et
s'installe par-dessus. Les deux voies exécutent donc les **mêmes octets**, et la
liste blanche d'adresses n'a qu'un site.

**`pre-commit.legacy` n'est pas un doublon : c'est la protection.** Ce point a
coûté au lot 0b sa fusion au premier tour, et il faut le lire en entier. Le hook
généré par le framework ouvre sa configuration en chemin **relatif**
(`--config=.pre-commit-config.yaml`) : un contrôle déclaré dans ce fichier — donc
dans l'**arbre de travail** — ne vaut que pour les arbres dont la configuration
le porte. Sur les 111 commits de `main`, **aucun** ne la porte (`mesuré`,
31 août 2026, `a005172`). Mesuré dans un clone frais monté par
`pre-commit install` seul, arbre sorti à `298c77e` : un commit portant
`@aosis.net` en auteur **et** en committer est **accepté**, `rc=0`. Le hook du
framework tourne, son rapport ne contient aucune ligne d'identité, et le commit
part. `pre-commit.legacy` vit **hors** de l'arbre de travail : c'est la seule
couche qui vaille pour tout commit, toute branche, tout `git bisect`, tout HEAD
détaché.

**Ne passe jamais `-f`.** `pre-commit install` le suggère lui-même dans sa
sortie — « `Use -f to use only pre-commit.` » — et c'est exactement le geste qui
supprime cette couche. Le script ne le passe pas, et sa vérification finale
rougit si la couche a disparu. Un test la garde :
`tests/unit/test_installation_des_garde_fous.py` monte un dépôt jetable dont la
configuration ne déclare **pas** le contrôle d'identité, y exécute le script
livré, et prouve que le refus tient quand même.

**Ce qui est par clone, et ce qui suit la branche.** À distinguer, parce que la
conclusion n'est pas la même pour les deux :

| Objet | Portée | Conséquence |
|---|---|---|
| l'**installation** des hooks | **une fois par clone** — `.git/hooks` est partagé entre le dépôt et tous ses arbres de travail, `core.hooksPath` n'étant pas positionné | rien à refaire par worktree |
| l'**identité** `user.email` | **une fois par clone** — `extensions.worktreeConfig` n'est pas activé, donc `git config` écrit dans `.git/config`, partagé (`mesuré`, 31 août 2026 : les quatre arbres de travail lisent le même fichier) | rien à refaire par worktree |
| la **protection d'identité** | **toute branche**, grâce à `pre-commit.legacy` | inconditionnelle, comme avant le lot 0b |
| la **configuration** des hooks du framework | **suit l'arbre de travail** : le hook lit `.pre-commit-config.yaml` en chemin relatif | un arbre sorti à un commit ancien exécute les hooks *de ce commit-là*. Ce n'est pas un défaut, c'est un fait à connaître |

Puis l'identité, une fois :

```bash
git config user.name "floSa" && git config user.email "florian.horellou@gmail.com"
```

Adresses autorisées, et elles seules : `florian.horellou@gmail.com`,
`florian_horellou@laposte.net`. Elles n'ont **qu'un site**,
`ADRESSES_AUTORISEES` dans `scripts/git-hooks/pre-commit`. Le hook ne se
contourne jamais : pas de `--no-verify`.

**Ce que le contrôle couvre, et ce qu'il ne couvre pas.** `mesuré` le 31 août
2026, mouchards posés sur chaque hook de `.git/hooks` :

| Geste | Couvert | Pourquoi |
|---|---|---|
| `git commit` | **oui** | hook `pre-commit` |
| `git commit --amend` | **oui** | hook `pre-commit` |
| `git merge --no-ff` | **oui** | hook `pre-merge-commit`, installé par le script et posé aussi en `pre-merge-commit.legacy` |
| `git revert`, `git cherry-pick` | **non** | git n'y déclenche ni `pre-commit` ni `commit-msg`. Le seul point d'accroche restant, `prepare-commit-msg`, y voit l'identité **locale** et non celle du commit produit (`mesuré`) : un contrôle posé là serait vert sur le défaut |
| `git rebase` | **non** | aucun hook de la famille ; le rebase réécrit le committer |

Les deux dernières lignes sont **ouvertes**, au registre. Elles ne sont pas des
gestes de ce chantier — le mandat interdit le rebase et prescrit `--no-ff` — et
leur fermeture honnête est un hook `pre-push`, qui reste à trancher.

Le distant est **personnel** : `floSa/rag-ingestion-pipeline`.

### 2.2 Le corpus

`Datas/htms/`, `Datas/pdfs/`, `Datas/mds/`, `Datas/database/` sont dans le
`.gitignore` : **le corpus n'est pas versionné**. Un clone arrive vide.

Il faut donc le **transporter à la main** d'un poste à l'autre. Deux sources
existent, et **elles ne sont pas interchangeables.**

**Copier `Datas/htms/` et `Datas/pdfs/` du dépôt lui-même** (`mesuré`,
28 août 2026) :

```
Datas/htms/MLOps with Databricks/                              12 fichiers
Datas/htms/Practical MLflow for Generative AI on Databricks/   12 fichiers
Datas/pdfs/Hands-On_RAG_for_Production_ER_-_Ofer_Mendelevitch.pdf   73 pages
                                            25 fichiers, environ 56 Mo au total
```

**Ne pas copier la sauvegarde** `/home/florianhorellou/corpus-rag-sauvegarde`
(27 fichiers, 64 Mo, locale au poste initial). Elle diverge de l'arbre de
travail sur deux points, tous deux nuisibles :

- elle porte **13** fichiers pour `MLOps with Databricks` contre 12 : c'est le
  chapitre capturé deux fois, dont le doublon a été retiré. Elle contient aussi
  un `docling_paper.pdf` qui n'appartient pas au corpus ;
- elle porte **les noms d'origine, horodatages de capture compris**. Ceux du
  dépôt en ont été débarrassés **avant toute ingestion, délibérément**.

C'est le second point qui compte. `source_path` entre dans le calcul de
`element_id` (contrat, exigence 2) : deux machines dont les noms de fichiers
diffèrent d'un caractère produisent des identifiants différents, donc des
mesures que rien ne permet de comparer, **sans qu'aucune erreur ne le
signale**. Les noms doivent être identiques au caractère près des deux côtés.

La sauvegarde reste le filet en cas de perte — jamais la source d'une copie.
**Et ne renomme rien.**

### 2.3 Le `.env`

`.env` est dans le `.gitignore`. Le recréer depuis `.env.example`, et vérifier
en priorité `EMBEDDING_MODEL_NAME` — c'est le chemin par lequel la panne la
plus coûteuse du système est déjà arrivée une fois (contrat, exigence 1).

### 2.4 La mesure de contrôle

```bash
make install && make all
```

**La présence de `make` est un fait de POSTE : mesure-la, ne la lis pas ici.**
`command -v make`. Sur un poste elle a manqué, sans droits pour l'y mettre
(`mesuré`, 31 août 2026) ; sur un autre, `make` était bien là — GNU Make 4.4.1,
`/usr/bin/make` (`mesuré`, 31 août 2026, même jour). Ce paragraphe a longtemps
affirmé l'absence comme un fait du chantier : c'était une mesure de poste
présentée comme universelle, et chaque conversation la reprenait sans la
vérifier. Le corpus, le `.env` et les stores relèvent de la même famille (§2.2,
§2.3, §4).

Si `make` manque, exécute les recettes du `Makefile` **versionné**, lues depuis
le fichier, dans l'ordre et avec arrêt au premier échec — et note que l'ordre a
changé, `format-check` venant en dernier :

```bash
uv sync && uv run ruff check src/ && uv run mypy src/ && uv run pytest tests/ && uv run ruff format --check src/
```

La différence avec `make` est nulle pour ce `Makefile` — pas de variable, pas de
motif, pas de parallélisme — mais elle existe : dis-le dans ton rapport plutôt
que de laisser croire que `make` a tourné.

Attendu sur `main` (`mesuré`, 31 août 2026) : `ruff` propre, `mypy --strict`
« no issues found in 36 source files », et la suite verte. Le compte canonique
de tests vit dans `README.md`, section Tests — n'en recopie pas la valeur ici.

**`make all` ne mute plus l'arbre : il le constate, et il est ROUGE sur `main`.**
Le lot 0b a séparé `format` — qui écrit, geste volontaire — de `format-check` —
qui constate, et qui est la dernière étape de `make all`. Il n'y a donc plus
rien à révoquer avant un commit, et c'est le point : le garde-fou ne repose plus
sur la mémoire du développeur.

En échange, `make all` sort en erreur sur `main` : `ruff format --check src/`
signale trois fichiers pliés à la main — `extraction.py`, `language.py`,
`matter.py`. **C'est un constat exact, et il ne faut pas l'éteindre.**

**Mais le dépôt en porte QUATRE, et ce quatrième est dans un angle mort.**
`tests/unit/test_wipe_stores.py` n'est pas format-propre non plus (`mesuré`,
31 août 2026 : `uv run ruff format --check src/ tests/` → « 4 files would be
reformatted, 56 files already formatted »). `make format-check` est borné à
`src/` et ne le voit jamais ; `make format` ne le répare pas ; le hook
`ruff-format --check` **bloque** tout commit qui le touche. Toute phrase qui dit
« trois fichiers » parle donc de la **portée de `make format-check`**, jamais de
l'état du dépôt — l'énumération avait été close sur une portée qui n'est plus
celle du garde installé.

Ne lance pas `make format` : les trois fichiers de `src/` sont réservés au lot 2,
qui réécrit `extraction.py`. **Le motif est la lisibilité de ce lot-là, pas un
volume** : le reformatage coûte **16 lignes** de diff sur **1 213**, à quatre
endroits, tous des replis de ligne (`mesuré`, `git diff --numstat` après
`uv run ruff format src/`). Le récit d'un « reformatage massif » était
surdimensionné, et il instruisait chaque conversation à venir de l'accepter sans
remesurer. Le détail et la marche à suivre vivent au `README.md`, section Tests ;
le constat au registre §5.4.

`format-check` passe **en dernier**, donc `lint`, `typecheck` et `test` rendent
leur verdict complet avant l'arrêt. Pour lire ce verdict seul, sans le rouge
connu : `make lint typecheck test`.

Si le compte diffère, ne suppose rien : c'est le dépôt qui a bougé, et il faut
comprendre pourquoi avant de continuer.

**Et lis ce nombre avec sa réserve.** C'est le compte des tests qui passent, pas
le compte des tests qui prouvent quelque chose. Le fichier
`tests/unit/test_hierarchie_bout_en_bout.py` **fabrique l'arbre imbriqué qu'il
prétend vérifier** et reste vert des deux côtés de son défaut (registre §3.3) —
même si l'audit indépendant a montré qu'il apporte tout de même une couverture
réelle, 3 mutations sur 7 n'étant vues que par lui. Un compte de tests est une
mesure de volume, jamais une mesure de garantie.

---

## 3. Le contrat avec `rag-agent-chat`

Il est écrit en tête du registre, [`axes_amelioration.md` §0](axes_amelioration.md).
**Va le lire là-bas** : c'est son site canonique, et il ne doit pas exister en
deux versions qui peuvent diverger.

En une phrase, pour savoir de quoi on parle : cinq exigences dures — modèle
d'embedding identique des deux côtés, `element_id` déterministe en 10
hexadécimaux, `source_path` comme identité, `sequence` monotone sur `PARENT_OF`,
`POST /reindex` en fin de pipeline — et deux contraintes d'ordre — ne pas
mesurer l'ablation du graphe avant d'avoir constaté sa profondeur réelle, et
savoir qu'une hiérarchie réelle coûte de la fenêtre de contexte.

La conception du jeu d'évaluation, et pourquoi elle n'est pas négociable, est
au [§1 du registre](axes_amelioration.md).

---

## 4. Faits établis — ne les redérive pas

- **Les stores sont vides et prêts — SUR LE POSTE DE RÉFÉRENCE SEULEMENT.**
  Bind mounts sous `Datas/database/`, pas de volumes nommés. **Ce fait ne
  voyage pas.** Sur le poste `/home/florian/mes_projets/`, ChromaDB porte la
  collection `rag_documents` avec 137 854 vecteurs et MinIO un bucket
  `documents` non vide (`mesuré`, 29 août 2026), et le corpus présent n'est pas
  celui du §2.2. **Avant tout lot qui ingère, mesure le poste au lieu de lire
  cette ligne** — registre §2.
- **Le réseau `rag_network` est créé par ce dépôt.** `rag-agent-chat` s'y
  raccroche en `external: true` et ne démarre pas sans lui.
- **Aucun GPU n'est requis.** L'ingestion tourne sur processeur. La réservation
  `nvidia` écrite en dur rendait le service **incréable** sans runtime nvidia ;
  elle vit désormais dans un `docker-compose.gpu.yml` superposable.
- **L'image Docling est à reconstruire — toujours vrai au 31 août 2026**, et la
  panne est identifiée précisément (`mesuré`) : `GET /health` rend **503** avec
  `graph_ready: true`, `objects_ready: true`, **`models_ready: false`**, et le
  journal donne la cause en clair —
  `PermissionError: [Errno 13] Permission denied: '/tmp/.cache/huggingface'`,
  levée par `_warm_up` au chargement de l'embedder. Le conteneur tourne en
  **Python 3.10.17** alors que `Dockerfile.docling` déclare `python:3.12-slim` :
  l'image précède donc le fichier. Or ce `Dockerfile` **corrige déjà** la panne
  (`mkdir -p /tmp/.cache && chown -R docling:docling /tmp/.cache`). Un
  `docker compose up -d --build docling-service` doit suffire — c'est la
  première manipulation du lot 1.
- *(Constat d'origine, conservé.)* Le conteneur tournait en Python 3.10
  alors que `Dockerfile.docling` déclare `python:3.12-slim` : l'image a plus de
  six semaines. Elle échouait sur `PermissionError: /tmp/.cache/huggingface`,
  que le `Dockerfile` actuel corrige déjà.
- **Le corpus est difficile par construction.** Les deux ouvrages traitent de
  sujets voisins : un passage sur le « model serving » a des sosies plausibles
  dans les deux. C'est voulu.
- **`Index.html` est écarté par le capteur** (`matter.py:40`), **`Preface.html`
  ne l'est pas** — « preface » n'est pas dans `FRONT_BACK_MATTER_TITLES`. Il
  sera donc ingéré depuis les deux ouvrages : c'est le cas d'école de
  l'exigence 3, et c'est bien qu'il y soit.
- **Un chapitre était capturé deux fois**, texte identique au caractère près :
  le doublon a été retiré avant le chantier.

---

## 5. Où on en est

### 5.1 Les branches — il n'y en a plus qu'une

| Réf | Pointe | Rôle |
|---|---|---|
| `main` | `75f96ca` | **tout ce qui est fusionné, y compris ce mandat et le registre.** Le lot 0 y est depuis le 29 août 2026 (fusion `b59bf38`). Un clone frais suffit : il n'y a rien à checkouter |
| tag `reference/lot-0-avant-reparation` | `832c566` | la version du lot 0 avant sa **première** réparation. Base de comparaison, pas une ligne de travail — d'où un tag et non une branche |

**Aucun lot n'est en vol.** `lot-0` a été fusionnée (`--no-ff`, `b59bf38`) puis
supprimée, en local **et côté distant** (vérifié le 31 août 2026 : `git branch -r`
ne rend que `origin/main`). Un clone frais ne voit donc qu'une seule branche et
un tag.

**Règle, pour ne pas refaire le désordre : une branche par lot en vol, jamais
plus.** Le chantier a compté jusqu'à cinq branches parce qu'une conversation
qui répondait à une question créait sa branche. Un lot qui n'est pas en vol
n'est pas une branche : soit il est fusionné dans `main`, soit il devient un
tag s'il faut pouvoir y revenir.

**Ce qui a été supprimé, et pourquoi c'était sans risque :**

- `claude/audit-refonte-rag-pipeline-fd8418` — portait ce mandat, le registre
  et le hook versionné, **aucun code**. Fusionnée dans `main` en avance rapide.
  Elle n'avait plus de raison d'être, et tant qu'elle existait il fallait
  savoir la checkouter pour lire ce fichier ;
- `claude/rag-ingestion-pipeline-restore-5e9fa1` — remplacée par le tag
  `reference/lot-0-avant-reparation`, qui pointe **exactement le même commit**.
  Rien n'est perdu : la comparaison qui a servi d'angle A à l'audit reste
  possible depuis le tag, `lot-0` étant désormais dans `main` — l'audit a
  conclu que rien n'avait été perdu au replantage (registre §8) ;
- `claude/rag-ingestion-restore-9e5aa5` — 0 commit au-dessus de `main`, jamais
  poussée (`mesuré`). Vide.

**La règle a tenu jusqu'au bout :** `lot-0` n'a été fusionnée qu'après son
audit indépendant **et** la réparation que cet audit a rendue nécessaire.

**L'écart au mandat du lot 0, tranché.** Le lot 0 avait été livré sur une
branche neuve plutôt qu'en réparant celle du mandat, et proposait soit de
force-pousser l'historique réparé sur l'originale, soit de livrer droit dans
`main`. Les deux ont été refusées : la première aurait détruit la base de
comparaison au moment où l'audit en a besoin, la seconde aurait fusionné sans
audit. Le choix du développeur — livrer ailleurs et garder l'originale — était
le bon ; c'est de ne pas l'avoir posé comme un écart au moment de le faire qui
était le défaut.

### 5.2 Le lot 0 : livré, audité, réparé, fusionné

Le détail complet — les six points de réparation, la conception retenue pour
la reprise de réindexation, les alternatives écartées — vit au **registre §8**.
C'est son site canonique ; ne le recopie pas ici.

Ce que le pilote a vérifié **de ses mains**, sans reprendre un chiffre
(`mesuré`, 29 août 2026) :

- les 8 commits d'origine **intacts** : `390ce8a` est resté ancêtre, rien n'a
  été réécrit ni rebasé ;
- `make all` sur le **résultat de la fusion** — c'est le point où deux branches
  vertes peuvent donner un arbre rouge, et personne ne l'avait mesuré :
  **535 tests verts**, `ruff` propre, `mypy` sans erreur ;
- **8 des 9 mutations annoncées, rejouées et rouges** : le `default_status` du
  sensor, l'asset qui lève, le `run_key` à tentative, le filtre `SUCCESS`, la
  garde « déjà en vol », le `max()` multi-sources, le code de sortie de
  `wipe_stores` et le décompte de l'échec MinIO ;
- aucun test perdu : deux noms disparus, **tous deux renommés**. Le test
  `test_un_echec_ne_rougit_pas_le_run_mais_le_crie` *devait* disparaître — il
  assertait le défaut ;
- le compte du README juste **dans chaque commit qui le change** ;
- hygiène : aucune mention d'IA, auteur en liste blanche, aucun `skip`,
  `xfail`, `type: ignore` ni `noqa` ajouté.

### 5.3 Ce que le lot 0 a fait apparaître, et qui reste ouvert

Le lot lui-même avait fait naître §5.4, §5.5 et §5.6 au registre. Son audit et
sa réparation en ont ajouté sept : **§4.15** (famine sur un run bloqué, et le
*run monitoring* absent de `dagster.yaml` qui les fermerait toutes d'un geste),
**§4.16** (réindexations concurrentes, cas étroit résiduel), **§4.17**
(classification des statuts terminaux non gardée), **§4.18** (les sensors
d'ingestion de `factory.py:335` livrés armés sans garde — tout le pipeline est
livrable à l'arrêt), **§4.19** (le refus de démarrer hors contrat non prouvé),
**§4.20** (`make audit` rouge et aveugle au groupe `dev`), **§5.7** et **§5.8**.

**Aucun n'est entré dans le diff.** Périmètre strict : ce qui est trouvé et non
traité va au registre.

### 5.4 Les deux désaccords tranchés

**Le développeur du lot 0 sur « une fois par rafale ».** Il a livré une
réindexation par rafale là où le contrat dit « une fois l'ingestion terminée »,
et il l'a dit lui-même. Il avait raison : « fin d'ingestion » n'existe pas comme
événement dans cette architecture. **C'est un écart assumé, écrit comme tel** —
`README.md`, `documentation/orchestration.md` et le docstring de
`reindex_job.py`. Le levier si l'agent supporte mal des réindexations
rapprochées est `minimum_interval_seconds` (30 s). Un réglage, pas une refonte.

**Le développeur de la réparation sur `make all`, et le pilote s'est rangé.**
Le registre rangeait §5.4 dans le lot de la hiérarchie au motif qu'il touche
`extraction.py`. C'était confondre deux choses : *reformater* peut attendre,
mais *une porte qualité qui écrit dans le dépôt qu'elle contrôle* ne le peut
pas. Il a dû révoquer trois fichiers avant chacun de ses six commits, parce
qu'il le savait ; le suivant ne le saura pas. **Versé au lot 0b.**

**Et l'auditeur sur `test_hierarchie_bout_en_bout.py`.** Le registre §3.3
laissait entendre que le fichier ne prouve rien. L'auditeur a mesuré sa
couverture marginale : **3 mutations sur 7 que lui seul voit**. Le registre a
été nuancé.

---

## 6. Le plan de lots

L'ordre se décide au **coût de l'attente**, pas à la sévérité. Un défaut grave
mais inerte attend ; un défaut mineur qui bloque une mesure passe devant.

| Lot | Contenu | Débloque | État |
|---|---|---|---|
| **0** | Porte qualité reproductible, `mypy`, `/reindex` déplacé **et sa reprise réparée** | démarrage de la stack, exigences **1** et **5** | ✅ **fusionné le 29 août 2026** (`b59bf38`) |
| **0b** | **Les gardes qu'on croit avoir.** §5.5 : les hooks du framework `pre-commit` ne sont installés nulle part — `detect-secrets` n'a **jamais** tourné en garde-fou. *(La justification d'origine ajoutait « sur un dépôt dont le `.env` porte les mots de passe MinIO et Postgres ». C'était une survente : un hook `pre-commit` ne voit que les fichiers **indexés**, et `.env` est ignoré par git, donc jamais indexé — l'installer ne le fera jamais scanner. Le gain réel est prospectif, et il est réel : empêcher qu'un secret parte un jour dans un fichier **versionné**. Registre §5.5.)* Plus §5.4 : `make all` cesse d'écrire dans le dépôt qu'il contrôle (`ruff format --check` dans la cible `all`). Plus §4.18, une ligne : les sensors d'ingestion sont livrables à l'arrêt sans qu'un test bronche | un garde-fou de secrets qui existe vraiment, un `make all` qui contrôle au lieu de muter, et un pipeline qui ne se déploie pas éteint | **à faire, court — c'est l'action suivante** |
| **1** | **Observer sans corriger.** Reconstruire l'image Docling, ingérer 1 chapitre par ouvrage + 5 pages du PDF. Trois questions : profondeur réelle du graphe (§3.2), `minio_url` présent sur les images HTML (§3.5), troncature réelle à l'embedding (§3.4) | contrainte **6** — la décision « corriger la hiérarchie avant ou après l'ingestion complète » | à faire |
| **2** | La hiérarchie des titres, **si et seulement si** le lot 1 la montre plate : §3.2, §3.3, §4.11, §4.12. Impose une purge du space | contrainte **6**, donc toute l'ablation | conditionnel |
| **3** | Instruments et gardes : §3.4, §4.4 (dont la **monotonie de `sequence`**, exigence 4), §4.14, §4.5 | la confiance dans tout chiffre produit après l'ingestion | à faire **avant** l'ingestion complète |
| **4** | La perte silencieuse : §4.1, §4.2, §4.6, §4.7, §4.3, §4.10, §5.6, plus §4.15 à §4.17 et §4.19 — la famille « un run bloqué gèle tout », qui se ferme d'un geste par le *run monitoring* absent de `dagster.yaml` | la certitude que le corpus ingéré est le corpus complet | à faire |
| **5** | Code mort et documentation contre code : §5.1 à §5.3, §5.7, §5.8, tout le §6 | la lisibilité, et l'arrêt des faux réglages | à faire |
| **6** | Ingestion complète → `verify_contract` → `index_report` → **puis** les 30 questions | la première campagne de référence | à faire |

**Pourquoi le lot 1 ne corrige rien et passe quand même deuxième.** Un seul
chapitre suffit à voir si Docling imbrique les titres, et coûte quelques
minutes. Ingérer tout d'abord, puis découvrir que le graphe est plat, coûterait
deux heures d'ingestion, une purge du space et une campagne d'ablation à
rejouer.

**La réserve du pilote, à ne pas perdre.** Le constat §3.2 — le graphe encore
plat parce que `docling_parent_rank` rend `0` au lieu de `None` — est un
raisonnement sur le comportement du backend HTML de Docling, **pas une
observation**. Il est étiqueté `supposé` au registre. Si Docling imbrique bien
les titres d'une capture SingleFile, le constat tombe et le lot 2 disparaît.
C'est exactement ce que le lot 1 est fait de trancher.

---

## 7. L'action suivante

**Distribuer le lot 0b.** Le prompt est prêt, en **annexe A** de ce fichier :
le coller tel quel dans une conversation neuve nommée `LOT-0B`.

C'est le lot le plus court du plan et celui dont
l'absence coûte le plus cher : `detect-secrets` est déclaré dans
`.pre-commit-config.yaml` et n'a **jamais** tourné.

**Ne reprends pas la justification qui suivait cette phrase.** Elle disait
« sur un dépôt dont le `.env` porte les mots de passe MinIO et PostgreSQL », et
c'était une survente : un hook `pre-commit` ne voit que les fichiers **indexés**,
`.env` est ignoré par git (`git check-ignore -v .env` → `.gitignore:2`) et non
suivi, donc **installer le hook ne le fera jamais scanner** (`mesuré`, registre
§5.5). Le gain est ailleurs, et il est réel : empêcher qu'un secret parte un jour
dans un fichier **versionné**. Le lot 0b avait corrigé cette survente au
`README.md` et à `SECURITY.md` — pas ici, dans le texte le plus copié du
chantier.

Trois choses dedans, et pas une de plus :

1. **§5.5** — installer réellement les hooks du framework `pre-commit`.
   L'arbitrage est ouvert : `.git/hooks/pre-commit` est déjà occupé par le
   contrôle d'identité d'auteur, et `pre-commit install` l'écraserait. Une piste
   qui n'est pas une consigne : faire du contrôle d'identité un hook
   `repo: local` dans `.pre-commit-config.yaml`. Preuve exigée **par mutation** :
   un commit portant une adresse hors liste blanche doit rester refusé après
   comme avant.
2. **§5.4** — `make all` cesse d'écrire dans le dépôt qu'il contrôle. Une ligne :
   `ruff format --check src/` dans la cible `all`, `format` restant pour
   l'écriture volontaire. La porte devient rouge sur `main` : **c'est vrai**, et
   c'est le but. Reformater les trois fichiers reste au lot 2, qui réécrit
   `extraction.py`.
3. **§4.18** — le test qui fait régresser `default_status` sur les sensors
   d'ingestion de `factory.py:335`. Il existe déjà pour le sensor de
   réindexation ; il suffit de le décliner. Sans lui, tout le pipeline est
   livrable à l'arrêt en silence.

Puis, dans l'ordre invariable :

1. lire le rapport du développeur ;
2. **le faire auditer par une conversation qui n'en a écrit aucune ligne** — sur
   les six lots du dépôt jumeau, l'audit indépendant a trouvé **chaque fois**
   quelque chose de matériel ; sur le lot 0 d'ici, il a trouvé une régression que
   ni le développeur ni le pilote n'avaient vue ;
3. lire le diff toi-même et faire tourner `make all` de tes mains, **y compris
   sur le résultat de la fusion** — deux branches vertes peuvent donner un arbre
   rouge ;
4. **alors seulement**, trancher la fusion ;
5. si fusion : `--no-ff`, jamais `--ff-only`, jamais de rebase — réécrire des
   commits dont la porte a été prouvée verte un par un invaliderait cette preuve
   pour un gain cosmétique. Puis supprimer la branche, local **et distant** ;
6. mettre le registre à jour — §8 « Traité », §2 pour la mesure d'après-fusion ;
7. écrire le prompt du lot 1.

*(La dette de propreté sur `origin/lot-0` est soldée : vérifié le 31 août 2026,
le distant ne porte plus que `main` et le tag.)*

---

## 8. Comment on pilote

Conventions apprises à leurs dépens sur le dépôt jumeau.

- **Un seul prompt à la fois, séquentiel.** Ne distribue jamais un prompt dont
  l'entrée dépend d'un rapport que tu n'as pas encore reçu.
- **Nomme la conversation destinataire en tête de ton message.** Le routage a
  déraillé plusieurs fois ; des prompts sont arrivés au mauvais endroit. Sois
  brutalement explicite : « ceci va à X, rien d'autre à envoyer ».
- **Chaque prompt se termine par l'obligation d'écrire `TÂCHE TERMINÉE` en
  dernière ligne**, ou `TÂCHE BLOQUÉE — <raison>`. Sans ça, l'utilisateur ne
  sait pas si le message lui est destiné ou t'est destiné.
- **Un développeur écrit, teste et livre ; un auditeur indépendant vérifie.**
  Un auditeur ne doit **jamais** avoir écrit une ligne de ce qu'il audite —
  c'est ce qui le rend éligible, et il faut le lui dire dans son mandat.
- **Audite avant de fusionner.** Toujours.
- **Une branche par lot en vol, jamais plus.** Une conversation qui répond à
  une question ne crée pas de branche. Un lot fusionné disparaît de la liste ;
  un commit auquel il faut pouvoir revenir devient un **tag**, pas une branche.
  Le chantier a compté cinq branches avant qu'on ne pose la règle.
- **Quand une conversation grossit, demande-lui un `/compact`** avant de lui
  envoyer la suite, avec des instructions sur ce qu'elle doit **garder** — sa
  méthode et sa connaissance du dépôt — et ce qu'elle doit **jeter** : ses
  rapports, les diffs, les sorties de commandes. Les preuves sont dans le
  dépôt.
- **Encourage le désaccord argumenté dans chaque prompt**, noir sur blanc. Le
  pilote a été renversé plusieurs fois par des audits, par des développeurs et
  par l'utilisateur, chaque fois à raison.
- **Comités réguliers avec l'utilisateur.** On vérifie à chaque fois. On pousse
  à la fin des tâches.

---

## 9. Les règles imposées à chaque conversation

À reproduire **dans chaque prompt**, sans les résumer.

**L'identité Git, en premier** — §2.1 de ce fichier, mot pour mot. C'est la
règle dont l'oubli a coûté un dépôt.

**Aucune mention** de Claude, Claude Code, Anthropic, Copilot ou ChatGPT nulle
part — code, documentation, messages de commit. **Aucun trailer
`Co-Authored-By`.**

**Commits atomiques en français**, dans le style du dépôt (`git log`). Une même
affirmation fausse vivant dans le **code** et dans un **document** se corrige
dans **un seul** commit — sans quoi il existe un commit où le document
contredit son code. Documentation dans le même commit que le code. Compte de
tests à jour. Push régulier.

**Chaque commit vert INDIVIDUELLEMENT**, ce qui inclut l'absence de rouge
aléatoire. Balayage des graines `PYTHONHASHSEED` : au moins 25 graines
aléatoires **plus la graine 0**, qui désactive la randomisation et est donc un
cas distinct. Un lot du dépôt jumeau s'est fait prendre à 2 graines sur 400.

**Aucun test désactivé**, aucun `skip`, `xfail`, `type: ignore`, aucune règle
`ruff` ou `mypy` relâchée, aucun `except` élargi sans justification écrite **au
site**.

**Aucun chiffre inventé** : étiquetage `mesuré` / `calculé` / `supposé`. Une
valeur reprise sans remesure s'écrit avec sa réserve, **une fois**, au site
canonique ; les autres mentions y renvoient.

**Périmètre strict** : ce qui est trouvé et non traité va au **rapport** et au
**registre**, jamais au diff.

**Test rouge d'abord.** Et chaque garde prouvé par **mutation du code livré** :
on casse la ligne, le test devient rouge, on remet, il redevient vert. Creux
avant, rouge après, tests nommés dans le rapport.

---

## 10. Les leçons qui ont trouvé les défauts

Elles ont produit tous les résultats du dépôt jumeau — une régression HTTP 500,
un instrument de mesure qui jetait son dénominateur, un `set` qui décidait
quelle source payait la fenêtre de contexte, un modèle d'embedding anglais armé
dans un `.env`. Aucun de ces défauts n'était visible sans cette discipline.
**Mets-les dans tes prompts, pas seulement dans ta tête.**

- Un test « ça marche » est vert **des deux côtés** du défaut. Il faut un test
  qui fait **régresser** ce qu'on prétend garder. Un test « ça tient » est vert
  des deux côtés d'un défaut de dimensionnement ; seul un test de **serrage**
  le voit.
- **Asserte depuis le côté qui PRODUIT le comportement**, pas depuis celui qui
  le consomme. Un code de sortie documenté et justifié n'était asserté nulle
  part : le remplacer par 0 laissait 390 tests verts.
- Une **phrase d'exhaustivité** dans un document ou un docstring est un défaut
  en attente : elle clôt une énumération que personne ne rouvre. « Les deux
  seules façons dont l'appel échoue » a autorisé une régression réelle.
- **Un test qui choisit lui-même son cas doit prouver qu'il l'a atteint.** Un
  test croyait vérifier `httpx.InvalidURL` avec une URL sans schéma, qui lève
  `UnsupportedProtocol` — laquelle hérite de `HTTPError`.
- **Vérifie tes bornes de balayage** : un lot balayait 1 250 cas en commençant
  juste au-dessus de la bande où le défaut vivait.
- Une généralisation tirée d'**une** branche d'une fonction qui en a plusieurs
  est fausse jusqu'à preuve du contraire.
- **Un montage de test qui bouchonne trop haut rend intestable ce qu'il
  prétend vérifier. Mute le producteur, pas le consommateur.**
- **Deux erreurs qui se compensent se cachent mutuellement.**
- Tester le point d'entrée d'un script demande un **sous-processus**, pas un
  import.
- Quand un développeur te donne une table de couverture, **n'audite pas sa
  liste** : construis la tienne depuis la documentation et les docstrings, puis
  diffe. Ce que ta liste contient et la sienne pas est le résultat qui compte.
- **La question la plus productive des deux dépôts : qu'est-ce que la
  documentation affirme que le code ne fait pas ?**

Celles que le lot 0 a ajoutées, et elles ont chacune trouvé quelque chose :

- **Un curseur qui avance à l'ÉMISSION d'une demande, et non à son succès,
  transforme une panne passagère en perte définitive.** C'était le défaut au
  cœur du lot 0.
- **Une clé d'idempotence déterministe interdit la reprise.** Un `run_key`
  consommé l'est pour toujours ; Dagster le cherche dans tout l'historique,
  sans borne de temps. Le geste de récupération naturel — remettre le curseur à
  zéro — ne rattrapait rien.
- **Toute reprise BORNÉE réintroduit la perte qu'elle prétend corriger.** Une
  `RetryPolicy` à trois tentatives ne fait que déplacer l'échec définitif plus
  tard.
- **Ne rien écrire est plus robuste que bien écrire.** Si l'historique porte
  déjà l'information, ne tiens pas d'état : un état que rien ne réconcilie avec
  la réalité est une seconde source de vérité, donc une source de divergence.
- **Une règle survit à son motif.** « Ne jamais lever » venait d'un endroit où
  une reprise coûtait des heures de reconversion ; le code a déménagé, la
  reprise est devenue un appel HTTP, et la règle est restée. Quand du code
  change de place, rouvre les règles dont le seul motif était l'ancienne place.
- **Un défaut peut être une ligne qui manque au TEST, pas au code.** La ligne
  `default_status=RUNNING` était juste et rien ne la gardait : la retirer
  laissait 508 tests verts et rendait tout le lot inerte au déploiement.
- **Ce qu'un test n'importe pas, il ne teste pas.** `test_wipe_stores.py`
  importait trois fonctions d'aide et jamais `main()` — où vivaient les deux
  moitiés du titre du commit. Lis la ligne d'`import` avant de croire une
  couverture.
- **Un harnais de test peut effacer ce qu'il doit observer.** Le harnais du
  sensor reconstruisait un contexte neuf à chaque tick, effaçant le curseur que
  le sensor venait de poser : le test de reprise aurait été vert des deux côtés
  du défaut. Vérifie ton harnais avant de croire ton rouge-d'abord.

---

## 11. Les erreurs de pilotage à ne pas refaire

Motif unique : **affirmer un comportement de code depuis sa mémoire au lieu de
relire.**

- Écrire qu'une fonction lit SQLite alors qu'elle ne fait aucune
  entrée-sortie ; le vrai coupable était ailleurs.
- Reprendre « sous-représenté de 9 points » d'un audit sans recalculer : c'est
  10,6 ou 3,1 selon la lecture, jamais 9.
- Présenter un budget comme une constante alors qu'il vaut
  `constante − len(question)`.
- **Vérifier une identité d'auteur sur le nom et non sur l'adresse** —
  l'erreur qui a coûté un dépôt entier.
- Lire `settings.py` dans un arbre de travail périmé et croire que le code
  avait dérivé.
- Renommer des fichiers en masse sans vérifier les collisions.
- Proposer d'échantillonner **l'ingestion** pour construire le jeu de
  questions. L'utilisateur a corrigé, et il avait raison : on échantillonne les
  questions, jamais le corpus.
- **Fusionner un lot avant son audit indépendant.**
- **Renvoyer à un fichier qu'on n'a pas écrit.** Le pilote a annoncé un prompt
  « prêt, dans tel fichier » sans l'avoir produit. Un artefact qu'on cite doit
  exister avant qu'on le cite.
- **Distribuer un prompt sans le relire contre l'état réel du dépôt.**
  L'annexe A portait « `main` = 77d4f5b, avance rapide possible » — vrai le jour
  où elle a été écrite, faux dès que `main` a bougé. Un prompt prêt à
  distribuer périme : relis-le contre `git`, pas contre ta mémoire.
- **Accepter le verdict d'un auditeur sur sa sévérité.** L'audit du lot 0
  qualifiait la perte de réindexation de « troc net-positif à consigner ».
  C'était une régression. Un rapport excellent peut sous-appeler sa propre
  trouvaille : lis ses faits, refais son raisonnement, et cote toi-même.

Traite tes propres affirmations comme des hypothèses. Vérifie avant d'écrire un
chiffre. Relis le code avant d'affirmer ce qu'il fait.

---

# Annexe A — Prompt prêt à distribuer : le lot 0b

> **Routage : ceci va à une conversation NEUVE, à nommer `LOT-0B`. Rien
> d'autre à envoyer, à personne.**
>
> Le prompt de l'audit du lot 0 a été consommé le 29 août 2026 ; il n'est pas
> conservé ici. Ce qu'il a produit vit au registre §8 et au §5.3 ci-dessus.

```
Tu es le développeur du LOT 0b sur le dépôt rag-ingestion-pipeline.

C'est le lot le plus court du plan, et celui dont l'absence coûte le plus
cher : « les gardes qu'on croit avoir ». Le dépôt déclare des garde-fous
dans .pre-commit-config.yaml — ruff, ruff-format, detect-secrets,
check-yaml — et RIEN NE LES EXÉCUTE. detect-secrets n'a donc jamais tourné
sur un dépôt dont le .env porte les mots de passe MinIO et PostgreSQL.

Dépôt : /home/florian/mes_projets/rag-ingestion-pipeline
Base : main (le lot 0 y a été fusionné le 29 août 2026).
Tu travailles sur une branche `lot-0b` partie de `main`.
Une branche par lot en vol, jamais plus : n'en crée pas d'autre.

LIS D'ABORD, EN ENTIER :
  documentation/pilotage_du_chantier.md   (le mandat, l'état, les règles)
  documentation/axes_amelioration.md      (le registre : le contrat en tête)

Installe le hook d'identité AVANT ton premier commit :
  cp scripts/git-hooks/pre-commit .git/hooks/pre-commit
  chmod +x .git/hooks/pre-commit
  git config user.name "floSa" && git config user.email "florian.horellou@gmail.com"

AVERTISSEMENT SUR CE POSTE : les stores ne sont pas vides et le corpus n'est
pas celui du mandat §2.2 (voir §4 et registre §2). Rien de ce lot n'en
dépend. N'y touche pas.

═══════════════════════════════════════════════════════════════
TROIS POINTS — ET RIEN D'AUTRE
═══════════════════════════════════════════════════════════════

── 1. Installer réellement les hooks du framework pre-commit (§5.5)

L'arbitrage est OUVERT et il est à toi : .git/hooks/pre-commit est déjà
occupé par le contrôle d'identité d'auteur, et `pre-commit install`
l'écraserait. Perdre ce contrôle est hors de question — sept commits sont
partis avec une adresse professionnelle sur un dépôt personnel, il a fallu
réécrire 165 commits PUIS détruire et recréer le dépôt GitHub.

Une piste existe et n'est PAS une consigne : faire du contrôle d'identité un
hook `repo: local` dans .pre-commit-config.yaml, ce qui rend le fichier au
framework sans rien perdre. Juge, tranche, argumente.

PREUVE EXIGÉE, PAR MUTATION : un commit portant une adresse hors liste
blanche doit être refusé APRÈS comme il l'est aujourd'hui. Démontre-le, ne
l'affirme pas. Vérifie aussi que detect-secrets tourne réellement — et dis
ce qu'il trouve, s'il trouve quelque chose.

Le hook versionné doit rester versionné : quelqu'un qui clone doit pouvoir
l'installer, et le mandat §2.1 doit rester vrai après ton lot. S'il cesse
de l'être, c'est à toi de le corriger dans le même commit.

── 2. `make all` cesse d'écrire dans le dépôt qu'il contrôle (§5.4)

Aujourd'hui `all: format lint typecheck test`, et `format` est
`ruff format src/` : la porte RÉÉCRIT trois fichiers avant de les contrôler.
Chaque développeur doit se souvenir de révoquer extraction.py, language.py
et matter.py avant chaque commit. Celui de la réparation du lot 0 l'a fait
six fois parce qu'il le savait ; le suivant ne le saura pas et livrera du
reformatage sans rapport.

La correction tient en une ligne : dans la cible `all`, `ruff format --check
src/`. La cible `format` reste, pour l'écriture volontaire.

CONSÉQUENCE ASSUMÉE : `make all` devient ROUGE sur main. C'est VRAI, le
registre §5.4 le dit déjà, et c'est le but. Ne corrige PAS les trois
fichiers : ils touchent extraction.py, que le lot 2 réécrit. Écris cette
conséquence là où le prochain développeur la lira, et dis-lui quoi en faire.

── 3. Les sensors d'ingestion sont livrables à l'arrêt (§4.18)

factory.py:335 déclare default_status=RUNNING sur CHAQUE sensor de source.
La réparation du lot 0 a gardé cette ligne pour le seul sensor de
réindexation. Les trois sensors d'ingestion — pdfs, livres_html, markdown —
restent livrables à l'arrêt sans qu'un test bronche : tout le pipeline
serait inerte au déploiement, en silence.

Le test existe déjà à côté, dans tests/unit/test_reindex_job.py : va le
lire, décline-le. Il comporte un troisième test qui tient les deux autres
honnêtes — un sensor témoin déclaré SANS le champ ne doit pas être armé —
sans quoi ils resteraient vrais si Dagster changeait sa valeur par défaut.
Reprends cette idée.

═══════════════════════════════════════════════════════════════
CE QUI EST HORS PÉRIMÈTRE
═══════════════════════════════════════════════════════════════

Tout le reste du registre. En particulier : ne reformate pas les trois
fichiers, ne touche pas à `make audit` (§4.20), ne corrige pas la famine
des sensors (§4.15, elle se ferme par le run monitoring de dagster.yaml, au
lot 4), ne touche pas au code mort (§5.1, §5.2, §5.7).

Si tu penses qu'un de ces points DOIT entrer, dis-le et argumente. Ne le
fais pas de ton propre chef. Ce qui est trouvé et non traité va au RAPPORT
et au REGISTRE, jamais au diff.

═══════════════════════════════════════════════════════════════
LES RÈGLES DU DÉPÔT
═══════════════════════════════════════════════════════════════

Commits atomiques en français, dans le style de `git log`. Documentation
dans le MÊME commit que son code.

Aucune mention de Claude, Claude Code, Anthropic, Copilot ou ChatGPT nulle
part. Aucun trailer Co-Authored-By. Le hook refuse une adresse hors liste
blanche ; ne le contourne jamais, pas de --no-verify.

TEST ROUGE D'ABORD. Chaque garde prouvé par MUTATION du code livré : tu
casses la ligne, le test devient rouge, tu remets, il redevient vert. Nomme
chaque mutation dans ton rapport.

Aucun test désactivé, aucun skip, xfail, type: ignore, aucune règle ruff ou
mypy relâchée, aucun except élargi sans justification écrite AU SITE.

`make all` vert sur CHACUN de tes commits pris individuellement — et
attention, tu es précisément le lot qui change ce que « vert » veut dire :
dis explicitement, pour chaque commit, ce que tu as mesuré et avec quelle
version de la cible. Plus le balayage de graines : la graine 0 (qui
désactive la randomisation, cas distinct) plus au moins 25 graines
PYTHONHASHSEED aléatoires.

Le compte de tests a UN site canonique, README.md section Tests. Remesure,
mets-le à jour là, dans chaque commit qui le change. Ne le recopie ailleurs
nulle part.

Aucun chiffre inventé : étiquette mesuré / calculé / supposé, et donne la
commande. Pousse au fil de l'eau.

═══════════════════════════════════════════════════════════════
LES LEÇONS — APPLIQUE-LES
═══════════════════════════════════════════════════════════════

- Un test « ça marche » est vert DES DEUX CÔTÉS du défaut.
- Asserte depuis le côté qui PRODUIT le comportement.
- Un défaut peut être une ligne qui manque au TEST, pas au code.
- Ce qu'un test n'importe pas, il ne teste pas : lis la ligne d'import
  avant de croire une couverture.
- Un harnais de test peut effacer ce qu'il doit observer. Vérifie ton
  harnais avant de croire ton rouge-d'abord.
- Une phrase d'exhaustivité est un défaut en attente.
- Tester un point d'entrée demande un SOUS-PROCESSUS, pas un import.
- Lis le code avec `git show <branche>:<fichier>`, pas avec `cat`.
- Traite tes propres affirmations comme des hypothèses.

═══════════════════════════════════════════════════════════════
CE QUE TU RENDS
═══════════════════════════════════════════════════════════════

1. Point par point, ce que tu as fait, et le commit qui le porte.
2. Pour le point 1 : l'arbitrage que tu as retenu, ce que tu as écarté, et
   POURQUOI. C'est ce que le pilote lira le plus attentivement.
3. Ta table de mutations : la ligne cassée, le test devenu rouge, fichier
   et ligne. Dont OBLIGATOIREMENT la preuve que le contrôle d'identité
   refuse toujours une adresse hors liste blanche.
4. Tes mesures avec leurs commandes.
5. Tout écart au mandat, DÉCLARÉ COMME TEL au moment où tu le prends.
6. Ce que tu as trouvé et NON traité, pour le registre.
7. Tes désaccords avec le pilote. Ils sont attendus, pas tolérés : le
   pilote a été renversé trois fois sur le lot 0, chaque fois à raison.

Ta dernière ligne est exactement `TÂCHE TERMINÉE`, ou
`TÂCHE BLOQUÉE — <raison>`.
```
