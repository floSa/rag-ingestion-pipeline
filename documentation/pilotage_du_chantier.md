# Piloter le chantier d'audit et de refonte

Ce fichier est le **mandat du pilote**. Il est écrit pour être lu par une
conversation qui n'a aucun historique : elle arrive, elle lit ceci, elle sait
où on en est, ce qui a été décidé, pourquoi, et quelle est l'action suivante.

Il est autosuffisant. Rien de ce qui suit ne suppose d'avoir vu une
conversation précédente.

Son compagnon obligatoire est [`axes_amelioration.md`](axes_amelioration.md),
le registre : ce fichier-ci dit **comment on travaille**, le registre dit **ce
qu'il reste à faire**. Les deux se tiennent à jour lot par lot.

> **Dernière mise à jour : 31 août 2026, après la fusion du lot 0b** (`e998e7d`).
> Toute valeur chiffrée ci-dessous porte son étiquette `mesuré`, `calculé` ou
> `supposé`. Une valeur non remesurée ne se recopie pas : on renvoie à son site
> canonique.

---

## 0. Reprendre le chantier en une manipulation

Ce fichier vit sur `main`. Un clone frais le contient : il n'y a **aucune
branche à checkouter** pour reprendre.

Le prompt à coller dans une conversation neuve, tel quel :

> Tu es le pilote d'un chantier d'audit et de refonte sur
> `rag-ingestion-pipeline`. Le dépôt est un clone local de
> `floSa/rag-ingestion-pipeline`, dépôt **personnel**, sur `main`. **Le chemin
> du dépôt et l'URL du distant sont des faits de POSTE : mesure-les.**
> `git rev-parse --show-toplevel` et `git remote -v`. Un poste portait un
> distant SSH `git@github.com-perso:`, un autre le même dépôt en HTTPS
> (`mesuré`, 31 août 2026).
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
> croire — les branches, l'état non versionné du poste (`.env`, stores, pile
> Docker ; le corpus, lui, est versionné depuis le 31 août 2026, §2.2), et
> `make install && make all` sur `main` — puis dis-moi où on en est et quelle
> est la prochaine action. Un prompt à la fois, et **nomme ET numérote**
> chaque conversation destinataire : `Conv' <n> <RÔLE-LOT>`.

Puis ce qui ne voyage pas avec un clone : §2.

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

Ce qui **ne voyage pas** avec un `git clone`. Les oublier a déjà coûté un dépôt
entier.

**Le corpus, lui, voyage désormais** — il a été versionné le 31 août 2026, et
le §2.2 dit à quel prix. Restent le garde-fou d'identité (§2.1), le `.env`
(§2.3), et les stores et la pile Docker (§4). **Vérifie chacun sur le poste, ne
lis pas cette liste comme un état.** Un poste a été repris en croyant sur
parole qu'il était le poste d'origine : il n'avait ni `.env`, ni hook installé,
ni conteneur, ni corpus (`mesuré`, 31 août 2026).

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
annonce**. Il n'y a rien à faire dans un ordre : c'est le script qui tient
l'ordre, et il constate son résultat.

**`uv` est requis, et ce paragraphe a affirmé le contraire.** Il disait « si
`uv` manque sur le poste, le script s'exécute seul ». C'est **faux** : `mesuré`
le 31 août 2026 dans un clone frais,
`env PATH=/usr/bin:/bin sh scripts/installer-les-garde-fous.sh` rend **`rc=1`**,
et il ne pose que les **deux copies du contrôle d'identité** — aucun hook du
framework, aucun `.legacy`. Le script, lui, est honnête : il l'écrit sur sa
sortie d'erreur (« `uv: not found` », puis « le contrôle d'identité est copié et
actif ; les hooks du framework ne le sont pas »). C'était la phrase qui mentait,
pas le code. Un montage à moitié armé est le pire des trois états, parce qu'il
ressemble au bon.

**Et il y a trois choses à se rappeler.** Ce paragraphe disait « rien à se
rappeler » : c'est une phrase d'exhaustivité, et c'est la famille de phrase qui
a caché la régression R1 pendant tout le lot 0b. Les voici — les deux dernières
`mesuré`es, la première étant une consigne de cette section :

1. **`git config user.email`** — deux paragraphes plus bas, dans cette même
   section. Le script n'y touche pas ;
2. **relancer `make install` après toute édition de `ADRESSES_AUTORISEES`** —
   voir « la liste blanche a DEUX sites au runtime » ci-dessous ;
3. **relancer `make install` depuis le clone principal si le `.venv` visé par le
   hook disparaît** — voir « Le hook fige aussi un chemin absolu » ci-dessous.

Cette liste-ci n'est pas fermée non plus. Si tu en trouves une quatrième,
écris-la ici plutôt que de la garder.

**Ce que le script monte, et pourquoi l'ordre compte.** Le contrôle d'identité
vit sous `scripts/git-hooks/pre-commit`, versionné, donc il arrive avec le clone
— mais **inactif tant qu'il n'est pas installé**, git n'exécutant jamais ce qui
arrive avec un dépôt. Le script le copie d'abord dans le répertoire des hooks,
**puis** lance `pre-commit install`, qui déplace cette copie en
`pre-commit.legacy`, continue de l'exécuter **avant** ses propres hooks, et
s'installe par-dessus. Les deux voies exécutent donc les mêmes octets **au
moment de l'installation**.

**Mais la liste blanche a DEUX sites au runtime, et ils peuvent diverger.** Ce
paragraphe affirmait « la liste blanche d'adresses n'a qu'un site » : c'est vrai
du dépôt versionné, **faux du montage armé**. `<type>.legacy` est une **copie
figée à l'installation** ; le hook `repo: local`, lui, relit le script versionné
à chaque commit. `mesuré` le 31 août 2026 dans un clone frais armé par le script
livré, dans les deux sens :

| Édition de `ADRESSES_AUTORISEES`, **commitée** | Effet au commit suivant |
|---|---|
| **ajouter** une adresse | **sans effet** : la couche `repo: local` l'accepte (« Passed »), puis `pre-commit.legacy` refuse, `rc=1`, en affichant l'**ancienne** liste. HEAD ne bouge pas |
| **retirer** une adresse | **appliqué** : la couche `repo: local` refuse, `rc=1`, HEAD ne bouge pas |

C'est donc **fail-closed dans les deux sens** — de la friction, jamais une
exposition — et c'est pour cela que le montage n'est pas changé : la copie figée
**est** la propriété qui rend le contrôle indépendant de l'arbre de travail.
**Le geste :** après toute édition de `ADRESSES_AUTORISEES`, relancer
`make install`, qui réécrit la copie.

Le message de refus est trompeur dans ce cas précis, et il faut le savoir : il
énumère la liste de la copie figée, pas celle du fichier qu'on vient d'éditer.
Si une adresse fraîchement ajoutée est refusée en n'apparaissant pas dans la
liste affichée, c'est ce cas-là, et rien d'autre.

**Le hook fige aussi un chemin absolu, et personne ne le savait.**
`pre-commit install` écrit dans `.git/hooks/<type>` une ligne
`INSTALL_PYTHON=<interpréteur de l'arbre qui a lancé l'installation>`. Un
`make install` lancé depuis un **arbre de travail temporaire** y grave donc le
`.venv` de cet arbre-là — et `.git/hooks` est **partagé par tout le clone**.
`mesuré` le 31 août 2026 : quand ce chemin disparaît et qu'aucun `pre-commit`
n'est au PATH (`which pre-commit` → `rc=1` sur ce poste), **tout commit du dépôt
et de tous ses arbres de travail** est refusé — `rc=1`, HEAD inchangé, sur le
seul message « `` `pre-commit` not found.  Did you forget to activate your
virtualenv? `` », qui ne nomme ni la cause ni `make install`.

C'est **fail-closed**, donc sans danger pour l'historique. Mais le §7 prescrit de
supprimer une branche fusionnée, donc son arbre de travail : **relance
`make install` depuis le clone principal après avoir supprimé un arbre de
travail**, et fais-le de préférence depuis lui dès le départ. Une piste pour ne
plus avoir à s'en souvenir est au registre, non retenue dans ce lot.

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
| l'**installation** des hooks | **une fois par clone** — `.git/hooks` est partagé entre le dépôt et tous ses arbres de travail, `core.hooksPath` n'étant pas positionné | rien à refaire par worktree — mais l'installation grave l'interpréteur de l'arbre **d'où on la lance**, donc lance-la depuis le clone principal |
| la **liste blanche** d'adresses | **un site versionné, deux sites armés** : `<type>.legacy` est une copie figée à l'installation | relancer `make install` après toute édition de `ADRESSES_AUTORISEES` |
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

### 2.2 Le corpus — versionné depuis le 31 août 2026

**`Datas/htms/` et `Datas/pdfs/` sont VERSIONNÉS.** Le commit `a005172` les a
sortis du `.gitignore` : 25 fichiers, **57 381 999 octets** (`mesuré`,
31 août 2026, `git ls-files -z -- Datas | xargs -0 stat -c '%s %n'`). Un clone
frais les porte. **Il n'y a plus rien à transporter à la main.**

Restent ignorés `Datas/database/` (volumes écrits par Docker), `Datas/.cleaned/`
(sortie du nettoyage, régénérée) et `Datas/mds/`.

**Pourquoi c'est la bonne décision, et ce qu'elle a coûté.** Le corpus non
versionné imposait un transport manuel, et `source_path` entre dans le calcul
d'`element_id` (contrat, exigence 2) : deux postes dont les noms de fichiers
diffèrent d'un caractère produisent des identifiants différents, donc des
mesures que rien ne permet de comparer, **sans qu'aucune erreur ne le signale**.
Versionner supprime ce chemin de divergence — les deux côtés lisent les mêmes
octets sous les mêmes noms. Le prix : 55 Mo dans l'historique de chaque clone,
et deux effets qu'il faut connaître, tous deux mesurés et traités par le lot 0b
(registre, constat **C**) — les hooks du framework `pre-commit` **réécrivaient**
le corpus, et `check-added-large-files` en **interdisait l'extension**. Le
corpus est désormais soustrait à tous les hooks par un `exclude: '^Datas/'`
racine, et le geste pour l'étendre est écrit au `README.md`.

**Le procédé, en revanche, n'était pas bon, et c'est consigné.** `a005172` a été
poussé droit dans `main` par une conversation sans mandat, pendant qu'un lot
travaillait, sans audit — la règle centrale du chantier. Le fond se défend, le
procédé non. Les deux sont vrais et ne s'annulent pas.

**Ce que le corpus contient** (`mesuré`, 31 août 2026) :

```
Datas/htms/MLOps with Databricks/                              12 fichiers
Datas/htms/Practical MLflow for Generative AI on Databricks/   12 fichiers
Datas/pdfs/Hands-On_RAG_for_Production_ER_-_Ofer_Mendelevitch.pdf   73 pages
                     25 fichiers, 57 381 999 octets ; le plus gros 6 362 475,
                     le plus petit 671 707 — 19 des 25 dépassent 1 Mo
```

*(Historique, conservé parce qu'il explique les noms de fichiers.)* Deux sources
ont existé et **n'étaient pas interchangeables** :

```
Datas/htms/MLOps with Databricks/                              12 fichiers
Datas/htms/Practical MLflow for Generative AI on Databricks/   12 fichiers
Datas/pdfs/Hands-On_RAG_for_Production_ER_-_Ofer_Mendelevitch.pdf   73 pages
                                            25 fichiers, environ 56 Mo au total
```

**La sauvegarde** `corpus-rag-sauvegarde` (27 fichiers, 64 Mo, locale au poste
initial ; **absente des postes vérifiés depuis**) divergeait de l'arbre de
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

C'est ce second point qui a rendu le versionnement nécessaire. **Et ne renomme
rien** : un renommage après ingestion tue le jeu de questions de l'agent.

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
le fichier, dans l'ordre et avec arrêt au premier échec — et note **deux**
changements : `install` arme désormais les garde-fous, et `format-check` vient en
dernier.

```bash
uv sync && sh scripts/installer-les-garde-fous.sh \
  && uv run ruff check src/ && uv run mypy src/ && uv run pytest tests/ \
  && uv run ruff format --check src/
```

**Le `sh scripts/installer-les-garde-fous.sh` de la deuxième position n'était pas
là, et son absence était le trou.** Ce repli prétend remplacer
`make install && make all` ; il ne portait que `uv sync`, c'est-à-dire la
**première** ligne de la cible `install` — celle qui installe les dépendances —
et pas la **seconde**, celle qui arme les hooks. Un poste sans `make` suivant
cette recette n'avait donc **aucun garde-fou** : ni contrôle d'identité, ni
`detect-secrets`, exactement l'état que ce lot ferme. `mesuré` le 31 août 2026,
clone frais : après le repli tel qu'il était écrit, `.git/hooks` ne porte **aucun**
hook ; après celui-ci, les quatre attendus, et la suite rend `lint=0`,
`typecheck=0`, `test=0`, `format-check=1` — le rouge connu.

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
31 août 2026, remesuré sur cette révision : `uv run ruff format --check src/
tests/` → « 4 files would be reformatted, 58 files already formatted »). `make format-check` est borné à
`src/` et ne le voit jamais ; `make format` ne le répare pas ; le hook
`ruff-format --check` **bloque** tout commit qui le touche. Toute phrase qui dit
« trois fichiers » parle donc de la **portée de `make format-check`**, jamais de
l'état du dépôt — l'énumération avait été close sur une portée qui n'est plus
celle du garde installé.

Ne lance pas `make format` : les trois fichiers de `src/` sont réservés au lot 2,
qui réécrit `extraction.py`. **Le motif est la lisibilité de ce lot-là, pas un
volume** : le reformatage coûte **16 lignes** de diff sur **1 221**, à cinq
endroits — quatre replis de ligne et un doublon de ligne vide (`mesuré`,
`git diff --numstat` après `uv run ruff format src/`). Le récit d'un « reformatage massif » était
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
| `main` | `528748d` | **tout ce qui est fusionné, y compris ce mandat, le registre et le corpus.** Le lot 0 y est depuis le 29 août 2026 (fusion `b59bf38`), le lot 0b depuis le 31 (fusion `e998e7d`). Un clone frais suffit : il n'y a rien à checkouter. **Remesure cette pointe avant de t'en servir** — elle a périmé deux fois en trois jours |
| tag `reference/lot-0-avant-reparation` | `832c566` | la version du lot 0 avant sa **première** réparation. Base de comparaison, pas une ligne de travail — d'où un tag et non une branche |

**Aucun lot n'est en vol.** `lot-0` a été fusionnée (`--no-ff`, `b59bf38`) puis
supprimée, en local **et côté distant**. Le lot 0b l'a été le 31 août 2026
(`--no-ff`, `e998e7d`), branche supprimée des deux côtés dans le même geste.
Un clone frais ne voit qu'une seule branche et un tag (`mesuré`, 31 août 2026).

**Cinq branches vides ont été supprimées avec elle**, toutes à 0 commit hors
`main` (`mesuré`) : le harnais crée un arbre de travail et une branche pour
chaque conversation, y compris celles qui n'écrivent rien — un auditeur, un
pilote. **Ce n'est pas une infraction à la règle « une branche par lot en
vol »**, mais ça la rend illisible : à la fin d'un lot, compte les branches qui
portent des commits, pas les branches.

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

### 5.1 bis Le lot 0b : livré, audité, réparé, réaudité, réparé, fusionné

Cinq conversations, dix commits, deux tours d'audit indépendant. Le détail vit
au **registre §8** — les constats **R1**, **D1**, **C**, les neuf affirmations
fausses et les neuf points de la seconde réparation. C'est son site canonique.

Ce que le pilote a vérifié **de ses mains**, sur le contenu final et sur le
résultat de la fusion (`mesuré`, 31 août 2026) :

- **la porte sur le commit de fusion** : `ruff` propre, `mypy` « no issues found
  in 36 source files », **552 tests verts**, `make all` en **2** — le rouge
  attendu de `format-check` sur les quatre fichiers pliés à la main, réservés au
  lot 2 — et l'arbre **non sali** ;
- **le contrôle d'identité, depuis un clone frais, après `make install` seul,
  dans un arbre sorti à un commit dont la configuration ne porte pas le hook** :
  auteur interdit → refusé ; committer interdit → refusé ; auteur seul interdit
  → refusé ; liste blanche → accepté. Puis la même chose sur un **vrai**
  `git merge --no-ff` : refusé, `HEAD` inchangé ;
- **le corpus intouché** : `pre-commit run --all-files` laisse `Datas/` à
  **0 fichier modifié**, `detect-secrets` passe sur tout l'arbre, une édition
  de 26 octets entre à +26 octets, et un chapitre neuf de 4 Mo est accepté ;
- **le geste canonique de scan du `README`** rend `rc=0` et **0 octet** sur le
  résultat de la fusion — il rendait `rc=123` avant la seconde réparation ;
- **les mutations rejouées** : `default_status` retiré → deux tests rouges et la
  suite privée de la classe neuve **verte**, ce qui EST le constat §4.18 ;
  `TYPES=""` dans l'installeur → il sortait en **0** en annonçant un montage
  qu'il n'avait pas fait ; `files: '^Datas/'` ajouté à la racine → sept hooks
  « no files to check », un fichier sale commité en `rc=0`, **550 tests verts** ;
- **`make install` relancé depuis le clone principal** après la fusion :
  `INSTALL_PYTHON` pointe désormais le `.venv` du dépôt et non celui d'un arbre
  de travail temporaire. Sans ce geste, supprimer la branche fusionnée aurait
  rendu **tout commit impossible** dans le dépôt et tous ses arbres — échec
  fermé, donc sans danger, mais bloquant. **C'est désormais l'étape 5 bis du
  §7.**

**La leçon de pilotage du lot 0b, et elle est chère.** La première livraison a
**fermé une porte et en a entrouvert une autre sans l'écrire** : en déménageant
le contrôle d'identité de `.git/hooks` vers `.pre-commit-config.yaml`, elle l'a
rendu **conditionnel à l'arbre de travail** — et aucun des 111 commits de `main`
ne portait le hook. Trois arbres sur quatre acceptaient l'adresse
professionnelle, et le pilote l'a mesuré en créant le commit. Ni le développeur
ni le pilote ne l'avaient vu ; l'audit indépendant l'a trouvé. **Une règle
survit à son motif : quand du code change de place, rouvre les propriétés dont
le seul motif était l'ancienne place.**

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

**Trois d'entre eux sont désormais fermés par le lot 0b** — §4.18, §5.4
(première moitié : la porte constate au lieu d'écrire) et §5.5. Le titre de
cette section reste au passé : elle dit ce que le lot 0 a fait APPARAÎTRE, pas
ce qui reste ouvert aujourd'hui. **L'état ouvert/fermé a un seul site canonique,
le registre §3 à §7 contre son §8.** Ne le déduis pas d'ici.

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
| **0b** | **Les gardes qu'on croit avoir.** §5.5 : les hooks du framework `pre-commit` ne sont installés nulle part — `detect-secrets` n'a **jamais** tourné en garde-fou. *(La justification d'origine ajoutait « sur un dépôt dont le `.env` porte les mots de passe MinIO et Postgres ». C'était une survente : un hook `pre-commit` ne voit que les fichiers **indexés**, et `.env` est ignoré par git, donc jamais indexé — l'installer ne le fera jamais scanner. Le gain réel est prospectif, et il est réel : empêcher qu'un secret parte un jour dans un fichier **versionné**. Registre §5.5.)* Plus §5.4 : `make all` cesse d'écrire dans le dépôt qu'il contrôle (`ruff format --check` dans la cible `all`). Plus §4.18, une ligne : les sensors d'ingestion sont livrables à l'arrêt sans qu'un test bronche | un garde-fou de secrets qui existe vraiment, un `make all` qui contrôle au lieu de muter, et un pipeline qui ne se déploie pas éteint | ✅ **fusionné le 31 août 2026** (`e998e7d`) — et ce ne fut pas court : cinq conversations, dix commits, **deux** tours d'audit indépendant. Il a livré en plus un contrôle d'identité **inconditionnel**, les commits de **fusion** couverts, le corpus versionné **soustrait aux hooks**, et une installation en **un geste** — `make install` — qui vérifie son propre résultat |
| **1** | **Observer sans corriger.** Monter la pile, reconstruire l'image Docling, ingérer 1 chapitre par ouvrage + 5 pages du PDF. Trois questions : profondeur réelle du graphe (§3.2), `minio_url` présent sur les images HTML (§3.5), troncature réelle à l'embedding (§3.4) | contrainte **6** — la décision « corriger la hiérarchie avant ou après l'ingestion complète » | **à faire — c'est l'action suivante.** Le corpus est désormais versionné (§2.2), donc plus rien ne le bloque côté données ; il reste le `.env` (§2.3) et la pile Docker (§4), qui sont des faits de poste |
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

**Distribuer le lot 1.** Le prompt est prêt, en **annexe A** de ce fichier : le
coller tel quel dans une conversation neuve nommée `Conv' <n> LOT-1`.

**Le lot 1 observe et ne corrige rien.** C'est sa raison d'être : trancher, par
la mesure, si le lot 2 existe. Un seul chapitre par ouvrage suffit à voir si
Docling imbrique les titres, et coûte quelques minutes ; ingérer tout d'abord
puis découvrir le graphe plat coûterait deux heures d'ingestion, une purge du
space et une campagne d'ablation à rejouer.

Trois questions, et pas une de plus :

1. **§3.2 — la profondeur RÉELLE du graphe.** `ranking.docling_parent_rank`
   rend `0` et non `None` quand le premier parent est `#/body`, et
   `extraction._flat_rank` ne bascule sur `docling_level_rank` que si le premier
   signal rend `None`. **Si** Docling n'imbrique pas les titres d'une capture
   SingleFile, tous les titres reçoivent le rang 0 et le graphe est plat. **Ce
   n'est PAS prouvé** — c'est un raisonnement sur le backend HTML de Docling,
   étiqueté `supposé` au registre. Si Docling imbrique bien, le constat tombe et
   **le lot 2 disparaît**. C'est la contrainte d'ordre **6** du contrat.
2. **§3.5 — `minio_url` sur les images HTML.** `cleaning.py` réécrit `img src`
   avec l'URL MinIO ; `extraction.py` ne propage cette URL que si
   `item.image.uri` commence par `http`. Que le backend HTML de Docling
   renseigne `image.uri` depuis l'attribut `src` n'est vérifié par aucun test.
   Si c'est faux, **aucune image de capture HTML ne porte de `minio_url`**, donc
   aucune n'est servie par l'agent.
3. **§3.4 — la troncature réelle à l'embedding.** `index_report.py` tokenise le
   texte **stocké** ; `vectors.py` encode le texte **préfixé du titre de
   section**. Le rapport annoncera donc 0 % de troncature alors que le texte
   réellement embarqué peut dépasser la fenêtre de 128 tokens.

**Avant les trois questions, deux préalables de poste**, à mesurer et non à
lire : le `.env` (§2.3 — vérifier `EMBEDDING_MODEL_NAME` en priorité, exigence 1
du contrat) et la pile Docker (§4 — sur le poste vérifié le 31 août 2026, il n'y
avait **aucun** conteneur du projet, **aucun** réseau `rag_network`, **aucun**
volume). L'image Docling est à reconstruire, et la panne est identifiée :
`GET /health` rend 503 avec `models_ready: false`, cause
`PermissionError: '/tmp/.cache/huggingface'`, que le `Dockerfile.docling`
**corrige déjà**. Un `docker compose up -d --build docling-service` doit suffire.

**Le lot 1 n'ingère qu'un échantillon, et c'est le seul endroit du chantier où
c'est permis.** On échantillonne pour observer un mécanisme ; on n'échantillonne
**jamais** l'ingestion pour construire le jeu de questions (registre §1) — ce
serait rendre le rappel trivial et la mesure creuse.

Puis, dans l'ordre invariable :

1. lire le rapport ;
2. **faire auditer par une conversation qui n'en a écrit aucune ligne** — sur le
   lot 0, l'audit indépendant a trouvé une régression ; sur le lot 0b, il en a
   trouvé une autre, vivante, que ni le développeur ni le pilote n'avaient vue.
   Il n'a **jamais** rien manqué en huit passages ;
3. lire le diff toi-même et faire tourner `make all` de tes mains, **y compris
   sur le résultat de la fusion** ;
4. **alors seulement**, trancher la fusion ;
5. si fusion : `--no-ff`, jamais `--ff-only`, jamais de rebase. Puis
   **relancer `make install` depuis le clone principal** — avant toute
   suppression d'arbre de travail — et alors seulement supprimer la branche,
   local **et distant**. Le hook généré fige un chemin **absolu** vers
   le `.venv` de l'arbre qui a lancé l'installation : si cet arbre disparaît,
   tout commit devient impossible dans le dépôt et tous ses arbres. Échec fermé,
   donc sans danger — mais bloquant, et le message d'erreur ne nomme pas
   `make install` (`mesuré`, lot 0b) ;
6. mettre le registre à jour ;
7. écrire le prompt du lot suivant — et **relire l'annexe A contre `git`**, pas
   contre ta mémoire : elle a périmé deux fois en trois jours.

**Un lot 1 qui ne trouve rien est un résultat.** Si Docling imbrique, si
`minio_url` est là, si la troncature est nulle, le lot 2 disparaît et le lot 3
avance. Ne cherche pas à ce qu'il trouve quelque chose.

## 8. Comment on pilote

Conventions apprises à leurs dépens sur le dépôt jumeau.

- **Un seul prompt à la fois, séquentiel.** Ne distribue jamais un prompt dont
  l'entrée dépend d'un rapport que tu n'as pas encore reçu.
- **Nomme ET NUMÉROTE la conversation destinataire en tête de ton message**,
  sous la forme `Conv' <n> <RÔLE-LOT>` — par exemple `Conv' 1 LOT-0B`,
  `Conv' 2 AUDIT-0B`. Le numéro est obligatoire et ne se réutilise **jamais**,
  même pour le même lot : le lot 0b a consommé cinq conversations, dont deux
  audits et deux réparations, et un nom seul les aurait confondues. Le routage a
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

# Annexe A — Prompt prêt à distribuer : le lot 1

> **Routage : ceci va à une conversation NEUVE, à nommer `Conv' <n> LOT-1`.
> Rien d'autre à envoyer, à personne.**
>
> Les prompts du lot 0 et du lot 0b ont été consommés ; ils ne sont pas
> conservés ici. Ce qu'ils ont produit vit au registre §8 et aux §5.1 bis, 5.2
> et 5.3 ci-dessus.
>
> **Relis cette annexe contre `git` avant de la coller.** Elle a périmé deux
> fois en trois jours : la pointe de `main`, le chemin du dépôt et la présence
> de `make` sont des faits de poste, pas des faits du chantier.

```
Tu es le développeur du LOT 1 sur le dépôt rag-ingestion-pipeline.

LE LOT 1 OBSERVE ET NE CORRIGE RIEN. C'est sa raison d'être : trancher par la
mesure si le lot 2 existe. Tu vas trouver des choses à corriger — tu les
écris dans ton rapport, tu ne les mets pas au diff. Si tu corriges, tu
détruis la mesure que le chantier attend.

Dépôt : mesure-le, ne le lis pas — `git rev-parse --show-toplevel`. Sur le
poste vérifié le 31 août 2026 : /home/ubuntu/RAG/rag-ingestion-pipeline.
Base : main. Mesure sa pointe : `git rev-parse --short main`. Elle était à
`528748d` le 31 août 2026 — la fusion du lot 0b est `e998e7d`, plus un commit de
documentation du pilote. **Ce chiffre périme vite** : il a bougé deux fois en
trois jours, et une fois dans l'heure qui a suivi l'écriture de ce prompt.
Tu travailles sur une branche `lot-1` partie de `main`.
Une branche par lot en vol, jamais plus : n'en crée pas d'autre.

LIS D'ABORD, EN ENTIER :
  documentation/pilotage_du_chantier.md   (le mandat, l'état, les règles)
  documentation/axes_amelioration.md      (le registre : le contrat en tête)

═══════════════════════════════════════════════════════════════
LES GARDE-FOUS, EN PREMIER — UN SEUL GESTE
═══════════════════════════════════════════════════════════════

  make install

Il fait `uv sync` PUIS arme les hooks git — le contrôle d'identité d'auteur
et les hooks de .pre-commit-config.yaml — et il VÉRIFIE qu'ils sont armés.
S'il sort en erreur, ne commite pas avant d'avoir corrigé : c'est le
garde-fou dont l'oubli a coûté un dépôt entier. Sept commits sont partis
avec une adresse professionnelle @aosis.net sur un dépôt personnel, il a
fallu réécrire 165 commits PUIS détruire et recréer le dépôt GitHub — la
liste des contributeurs, une fois constituée, ne se défait pas.

Puis l'identité, UNE FOIS par clone (`.git/config` est partagé entre le
dépôt et tous ses arbres de travail) :
  git config user.name "floSa"
  git config user.email "florian.horellou@gmail.com"

Adresses autorisées, et elles seules : florian.horellou@gmail.com,
florian_horellou@laposte.net. Jamais de --no-verify.

Si `make` manque sur ton poste — c'est un fait de POSTE, mesure-le par
`command -v make` — lis les recettes du Makefile VERSIONNÉ et exécute-les
dans l'ordre, avec arrêt au premier échec. Attention : `install` arme les
garde-fous, et `format-check` (qui CONSTATE) vient en dernier dans `all`.

`make all` SORT EN 2 SUR MAIN, ET C'EST NORMAL. Quatre fichiers ne sont pas
format-propres — trois réservés au lot 2, plus tests/unit/test_wipe_stores.py
(registre §5.4). Ne les reformate pas. Vérifie que `lint`, `typecheck` et
`test` rendent 0 avant l'arrêt : c'est ça, ton vert.

═══════════════════════════════════════════════════════════════
DEUX PRÉALABLES DE POSTE — MESURE, NE LIS PAS
═══════════════════════════════════════════════════════════════

Le corpus, lui, EST versionné depuis le 31 août 2026 (mandat §2.2) : 25
fichiers sous Datas/htms/ et Datas/pdfs/, ils arrivent avec le clone. Ne le
transporte pas, ne le renomme pas — `source_path` entre dans le calcul
d'`element_id`, un caractère de différence rend toute mesure incomparable
sans qu'aucune erreur ne le signale.

── 1. Le `.env` (mandat §2.3)

Il est dans le .gitignore. Sur le poste vérifié le 31 août 2026, il était
ABSENT. Recrée-le depuis .env.example, et vérifie EN PRIORITÉ
`EMBEDDING_MODEL_NAME` : il doit valoir exactement
`paraphrase-multilingual-MiniLM-L12-v2`.

C'EST LA PANNE LA PLUS COÛTEUSE DU SYSTÈME, ET ELLE EST SILENCIEUSE. Les
deux modèles candidats rendent 384 dimensions, donc ChromaDB accepte sans
broncher, aucune sonde ne voit rien, et la recherche rend des passages
plausibles et faux. Un .env de juin portait `all-MiniLM-L6-v2`, un modèle
anglais, face à un agent multilingue. Vérifier la dimension ne protège de
rien : c'est le NOM qui discrimine. Contrat, exigence 1.

── 2. La pile Docker (mandat §4)

Sur le poste vérifié le 31 août 2026 : AUCUN conteneur du projet, AUCUN
réseau `rag_network`, AUCUN volume. Mesure d'abord — `docker ps -a`,
`docker network ls`, `docker volume ls` — puis monte ce qu'il faut.

Et LES STORES S'INTERROGENT, ILS NE SE MESURENT PAS EN TAILLE DE DOSSIER :
un ChromaDB vide pèse quand même 250 Mo. Demande à ChromaDB son
`collection.count()`, à MinIO la liste de son bucket, à Nebula son compte de
sommets. Sur un autre poste, les stores portaient 137 854 vecteurs et un
corpus qui n'était pas celui-ci — un lot qui a ingéré là-dessus aurait
mélangé deux corpus sans qu'aucune erreur ne paraisse.

L'IMAGE DOCLING EST À RECONSTRUIRE, et la panne est identifiée
précisément : `GET /health` rend 503 avec `graph_ready: true`,
`objects_ready: true`, `models_ready: false`, et le journal donne la cause en
clair — `PermissionError: [Errno 13] Permission denied:
'/tmp/.cache/huggingface'`, levée par `_warm_up` au chargement de
l'embedder. Le conteneur tournait en Python 3.10.17 alors que
Dockerfile.docling déclare python:3.12-slim : l'image précède le fichier. Or
ce Dockerfile CORRIGE DÉJÀ la panne (`mkdir -p /tmp/.cache && chown -R
docling:docling /tmp/.cache`). Un `docker compose up -d --build
docling-service` doit suffire. AUCUN GPU N'EST REQUIS : l'ingestion tourne
sur processeur, et la réservation nvidia vit dans un docker-compose.gpu.yml
superposable.

Si un préalable te bloque durablement, dis-le et arrête-toi : mieux vaut
`TÂCHE BLOQUÉE — <raison>` qu'une mesure faite sur une pile bancale.

═══════════════════════════════════════════════════════════════
CE QUE TU INGÈRES — UN ÉCHANTILLON, ET C'EST VOULU
═══════════════════════════════════════════════════════════════

UN chapitre par ouvrage, plus environ CINQ pages du PDF. Pas plus.

C'est le seul endroit du chantier où échantillonner est permis, parce qu'on
observe un MÉCANISME. On n'échantillonne JAMAIS l'ingestion pour construire
le jeu de questions (registre §1) : ce serait rendre le rappel trivial et la
mesure creuse. Le lot 6 ingérera tout.

Pourquoi si peu : un seul chapitre suffit à voir si Docling imbrique les
titres, et coûte quelques minutes. Ingérer tout d'abord, puis découvrir que
le graphe est plat, coûterait deux heures d'ingestion, une purge du space et
une campagne d'ablation à rejouer.

DIS QUELS FICHIERS TU AS CHOISIS, ET POURQUOI. Un chapitre de milieu
d'ouvrage, avec des sous-sections et des images, dit plus qu'une préface.
Note que `Index.html` est écarté par le capteur (matter.py) mais que
`Preface.html` ne l'est PAS — « preface » n'est pas dans
FRONT_BACK_MATTER_TITLES — et qu'il existe dans les DEUX ouvrages : c'est le
cas d'école de l'exigence 3 du contrat, `source_path` comme identité.

═══════════════════════════════════════════════════════════════
LES TROIS QUESTIONS — ET RIEN D'AUTRE
═══════════════════════════════════════════════════════════════

── 1. LA PROFONDEUR RÉELLE DU GRAPHE (§3.2) — la question qui décide du lot 2

`ranking.docling_parent_rank` rend 0, et non None, dès que le premier parent
rencontré est `#/body`. `extraction._flat_rank` ne bascule sur
`docling_level_rank` que si le premier signal rend None. DONC si Docling
n'imbrique pas les titres d'une capture SingleFile, tous les titres
reçoivent le rang 0, deviennent frères sous le document, et le graphe est
plat.

CE N'EST PAS PROUVÉ. C'est un raisonnement sur le comportement du backend
HTML de Docling, étiqueté `supposé` au registre, et ce code n'a JAMAIS
tourné sur ce corpus. Deux chiffres s'opposent et aucun n'est daté :
documentation/CHANGEMENTS.md annonce 759 arêtes SectionHeader→SectionHeader
sur « le corpus de référence » — un corpus qui n'existe plus ; le contrat
côté agent annonce 0 et 0 sur le graphe de production.

Ce qu'on attend de toi : la profondeur MESURÉE, dans NebulaGraph, sur ce que
tu viens d'ingérer. Compte des arêtes PARENT_OF, distribution des
profondeurs, longueur des chemins, et un exemple lisible — un vrai fil
d'Ariane du type `Chapitre 3 > 3.2 > 3.2.1`, ou la preuve qu'il n'y en a pas.

SI DOCLING IMBRIQUE BIEN LES TITRES, LE CONSTAT §3.2 TOMBE ET LE LOT 2
DISPARAÎT. C'est un excellent résultat. Ne cherche pas à confirmer le
constat : cherche à savoir.

Regarde aussi, tant que tu y es et sans corriger : la propriété `sequence`
des arêtes PARENT_OF est-elle présente, et MONOTONE ? C'est l'exigence 4 du
contrat, et son absence casse la reconstruction sans erreur visible. Le
garde appartient au lot 3 ; l'observation est gratuite ici.

── 2. `minio_url` SUR LES IMAGES HTML (§3.5)

`cleaning.py` réécrit `img src` avec l'URL MinIO ; `extraction.py` ne
propage cette URL que si `item.image.uri` commence par `http`. Que le
backend HTML de Docling renseigne `image.uri` depuis l'attribut `src` n'est
vérifié par AUCUN test ni aucune mesure.

Si c'est faux, aucune image de capture HTML ne porte de `minio_url` — donc
aucune n'est servie par l'agent, qui ne sert que ce que le graphe référence
(RESTRICT_MEDIA_TO_GRAPH=true). `supposé`, à prouver sur ton chapitre.

Mesure : combien d'images dans le chapitre, combien de sommets porteurs d'un
`minio_url` non vide, et l'URL pointe-t-elle un objet qui EXISTE dans MinIO.
Les trois, séparément — une URL présente et morte est un faux vert.

── 3. LA TRONCATURE RÉELLE À L'EMBEDDING (§3.4)

`index_report.py` tokenise `documents`, c'est-à-dire le texte STOCKÉ. Or
`vectors.py` encode `contextualize(texte, section_title)`, c'est-à-dire le
texte PRÉFIXÉ du titre de section. Le rapport annoncera donc 0 % de
troncature alors que le texte réellement embarqué peut dépasser la fenêtre.

Aggravant : `HybridChunker` compte ses tokens sur SA PROPRE sérialisation
contextualisée, titres compris. Préfixer un second titre par-dessus peut
refranchir la fenêtre de 128 tokens — exactement la troncature silencieuse
que le passage à `HybridChunker` prétendait supprimer. `supposé`, à mesurer.

Mesure la troncature sur le texte RÉELLEMENT ENCODÉ, pas sur le texte
stocké. Donne la distribution des longueurs en tokens, le maximum, et le
nombre de chunks au-dessus de la fenêtre. Et dis ce que l'instrument
`index_report` annonce à côté : l'écart entre les deux EST le résultat.

═══════════════════════════════════════════════════════════════
CE QUI EST HORS PÉRIMÈTRE
═══════════════════════════════════════════════════════════════

TOUT LE RESTE DU REGISTRE. Tu ne corriges ni §3.2, ni §3.4, ni §3.5 — les
corriger appartient aux lots 2 et 3, et les corriger MAINTENANT détruirait
la mesure qu'on te demande.

En particulier : ne touche pas à la hiérarchie (§3.2, §3.3, §4.11, §4.12),
ni aux instruments (§3.4, §4.4, §4.14), ni à la perte silencieuse (§4.1 à
§4.10, §4.15 à §4.19), ni au code mort (§5.1 à §5.3, §5.7), ni aux quatre
fichiers non format-propres (§5.4), ni à `make audit` (§4.20), ni au run
monitoring de dagster.yaml (§4.15).

Tu peux avoir besoin d'écrire un script de MESURE. S'il est jetable, il ne
va pas au diff : sa sortie va au rapport. S'il mérite de rester, dis-le,
argumente, et donne-lui des tests — mais le défaut par défaut est de ne rien
livrer d'autre que des mesures.

Si tu penses qu'une correction DOIT entrer, dis-le et argumente. Ne le fais
pas de ton propre chef. Ce qui est trouvé et non traité va au RAPPORT et au
REGISTRE, jamais au diff.

═══════════════════════════════════════════════════════════════
LES RÈGLES DU DÉPÔT
═══════════════════════════════════════════════════════════════

Commits atomiques en français, dans le style de `git log`. Documentation
dans le MÊME commit que son code : une même affirmation fausse vivant dans
le code et dans un document se corrige dans UN SEUL commit.

Aucune mention de Claude, Claude Code, Anthropic, Copilot ou ChatGPT nulle
part — code, documentation, messages de commit. Aucun trailer
Co-Authored-By.

Aucun test désactivé, aucun skip, xfail, type: ignore, aucune règle ruff ou
mypy relâchée, aucun except élargi sans justification écrite AU SITE.

Si tu livres du code, TEST ROUGE D'ABORD, et chaque garde prouvé par
MUTATION du code livré : tu casses la ligne, le test devient rouge, tu
remets, il redevient vert. Nomme chaque mutation dans ton rapport.

`make all` vert sur CHACUN de tes commits pris individuellement — au sens
défini plus haut : rc=2 attendu, `lint`/`typecheck`/`test` à 0. Plus le
balayage de graines : la graine 0 (qui désactive la randomisation, cas
distinct) plus au moins 25 graines PYTHONHASHSEED aléatoires.

Le compte de tests a UN site canonique, README.md section Tests. Il est à
552 (`mesuré`, 31 août 2026). Remesure et mets-le à jour là, dans chaque
commit qui le change. Ne le recopie nulle part ailleurs.

AUCUN CHIFFRE INVENTÉ. Étiquette `mesuré` / `calculé` / `supposé`, et donne
la COMMANDE qui l'a produit. Et vérifie la PROVENANCE de chaque chiffre que
tu cites : sur le lot 0b, un chiffre juste a été cité pour le mauvais arbre,
trois fois. Un chiffre mesuré avant ton changement n'est pas un chiffre
mesuré.

MESURE `rc` SANS PIPE : `cmd 2>&1 | tail` rend le code de retour de `tail`.
Quatre personnes s'y sont fait prendre sur le lot 0b, dont le pilote.

Les noms de fichiers du corpus contiennent des ESPACES et un deux-points
PLEINE CHASSE (« 4. Model Serving： … »). Ne boucle jamais sur une liste non
protégée : un développeur du lot 0b s'est fabriqué un faux vert avec
`tr '\n' ' '`.

Pousse au fil de l'eau.

═══════════════════════════════════════════════════════════════
LES LEÇONS — APPLIQUE-LES
═══════════════════════════════════════════════════════════════

- La question la plus productive des deux dépôts : QU'EST-CE QUE LA
  DOCUMENTATION AFFIRME QUE LE CODE NE FAIT PAS ? Tes trois questions sont
  exactement de cette forme.
- Un test « ça marche » est vert DES DEUX CÔTÉS du défaut. Un test « ça
  tient » est vert des deux côtés d'un défaut de dimensionnement ; seul un
  test de SERRAGE le voit.
- Asserte depuis le côté qui PRODUIT le comportement, pas depuis celui qui
  le consomme.
- Une PHRASE D'EXHAUSTIVITÉ est un défaut en attente : elle clôt une
  énumération que personne ne rouvre. N'en écris pas dans ton rapport.
- UN TEST QUI CHOISIT LUI-MÊME SON CAS DOIT PROUVER QU'IL L'A ATTEINT. Vaut
  pour une mesure : si tu mesures un chapitre, prouve que c'est bien celui
  que tu crois, et qu'il a bien été ingéré.
- Une généralisation tirée d'UNE branche d'une fonction qui en a plusieurs
  est fausse jusqu'à preuve du contraire. Vaut pour Docling : ce que fait le
  backend HTML ne dit rien du backend PDF, et l'inverse non plus. MESURE LES
  DEUX.
- Deux erreurs qui se compensent se cachent mutuellement.
- Un montage de test qui bouchonne trop haut rend intestable ce qu'il
  prétend vérifier. Mute le producteur, pas le consommateur.
- Un harnais peut effacer ce qu'il doit observer. Vérifie ton harnais avant
  de croire ton rouge — ou ton vert.
- Lis le code avec `git show <réf>:<fichier>`, pas avec `cat` : sur ce
  dépôt, un arbre de travail a longtemps porté le contenu de juin sur un
  HEAD d'août.
- Traite tes propres affirmations comme des hypothèses. Vérifie avant
  d'écrire un chiffre.

═══════════════════════════════════════════════════════════════
CE QUE TU RENDS
═══════════════════════════════════════════════════════════════

1. L'ÉTAT DU POSTE que tu as mesuré : `.env`, stores INTERROGÉS (pas leur
   taille), pile Docker, image Docling. Avec les commandes.
2. Ce que tu as ingéré : quels fichiers, pourquoi ceux-là, et la preuve que
   l'ingestion a réussi.
3. RÉPONSE À LA QUESTION 1 — la profondeur réelle du graphe, avec ses
   chiffres et un exemple lisible. Puis ta conclusion EXPLICITE : le constat
   §3.2 tient-il, ou tombe-t-il ? Le lot 2 existe-t-il ?
4. RÉPONSE À LA QUESTION 2 — images, `minio_url`, et objets réellement
   présents dans MinIO.
5. RÉPONSE À LA QUESTION 3 — troncature sur le texte réellement encodé,
   contre ce qu'annonce `index_report`.
6. Tes mesures avec leurs commandes et leurs étiquettes.
7. Tout écart au mandat, DÉCLARÉ COMME TEL au moment où tu le prends.
8. Ce que tu as trouvé et NON traité, pour le registre. Ce sera long, et
   c'est normal : c'est la première fois que ce code tourne sur ce corpus.
9. Tes désaccords avec le pilote. Ils sont attendus, pas tolérés : sur le
   lot 0b, le développeur a renversé le pilote, l'audit a renversé le
   développeur ET le pilote, et le réparateur a renversé l'audit. Chaque
   fois à raison.

Ta dernière ligne est exactement `TÂCHE TERMINÉE`, ou
`TÂCHE BLOQUÉE — <raison>`.
```
