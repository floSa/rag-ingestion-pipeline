# Piloter le chantier d'audit et de refonte

Ce fichier est le **mandat du pilote**. Il est écrit pour être lu par une
conversation qui n'a aucun historique : elle arrive, elle lit ceci, elle sait
où on en est, ce qui a été décidé, pourquoi, et quelle est l'action suivante.

Il est autosuffisant. Rien de ce qui suit ne suppose d'avoir vu une
conversation précédente.

Son compagnon obligatoire est [`axes_amelioration.md`](axes_amelioration.md),
le registre : ce fichier-ci dit **comment on travaille**, le registre dit **ce
qu'il reste à faire**. Les deux se tiennent à jour lot par lot.

> **Dernière mise à jour : 1er septembre 2026, après la fusion du lot 3**
> (`4e28594`). L'action suivante est le **lot 4**, et son prompt est en annexe A.
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
Datas/pdfs/Hands-On_RAG_for_Production_ER_-_Ofer_Mendelevitch.pdf   71 pages
                     25 fichiers, 57 381 999 octets ; le plus gros 6 362 475,
                     le plus petit 671 707 — 19 des 25 dépassent 1 Mo
```

*(Historique, conservé parce qu'il explique les noms de fichiers.)* Deux sources
ont existé et **n'étaient pas interchangeables** :

```
Datas/htms/MLOps with Databricks/                              12 fichiers
Datas/htms/Practical MLflow for Generative AI on Databricks/   12 fichiers
Datas/pdfs/Hands-On_RAG_for_Production_ER_-_Ofer_Mendelevitch.pdf   71 pages
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
  && uv run ruff check src/ tests/ && uv run mypy src/ && uv run pytest tests/ \
  && uv run ruff format --check src/ tests/
```

**Les DEUX cibles `ruff` portent `src/ tests/`, et la seconde ne le portait pas.**
Le commit qui pretendait aligner les portees n'en a corrige que la moitie :
`ruff check` est passe a `src/ tests/`, `ruff format --check` est reste a `src/`.
`mesure` le 1er septembre 2026 sur la pointe du lot 4 : `--check src/` voit **36**
fichiers, `--check src/ tests/` en voit **73**. Le repli etait donc aveugle aux
fichiers de `tests/`, et les deux verdicts ne coincidaient que par chance.

Mesure du desaccord, de mes mains, sur un arbre ou une seule ligne d'un fichier de
`tests/` est pliee a la main :

| Sequence | arbre propre | une ligne pliee dans `tests/` |
|---|---|---|
| `make all` | `rc=0` | **`rc=2`** |
| le repli tel qu'il etait ecrit | `rc=0` | **`rc=0`** — aveugle |
| le repli corrige ci-dessus | `rc=0` | **`rc=1`** — il voit |

**Lis la colonne comme un VERDICT et non comme un code.** `make` traduit tout
echec de recette en **2** ; `ruff format --check` sort lui-meme en **1**. Les deux
sequences rendent donc le meme verdict — vert sur l'arbre propre, rouge sur
l'arbre sale — et jamais le meme entier. Un controle ecrit `rc = 2` sur le repli
serait vert sur le defaut, exactement comme le `rc=123` d'`xargs` du registre F3.

C'est le trou que ce §2.4 decrit lui-meme pour l'installeur — la premiere ligne
d'une cible recopiee, la seconde oubliee — refait un cran plus loin, dans le
commit qui pretendait aligner les portees.

**La preuve est une mesure et non un test**, et il faut le dire : F7 reste ouvert,
aucun test de ce depot ne lit le `Makefile` ni ce fichier. Un repli qui derive de
la cible qu'il remplace ne rougira nulle part.

**Le `sh scripts/installer-les-garde-fous.sh` de la deuxième position n'était pas
là, et son absence était le trou.** Ce repli prétend remplacer
`make install && make all` ; il ne portait que `uv sync`, c'est-à-dire la
**première** ligne de la cible `install` — celle qui installe les dépendances —
et pas la **seconde**, celle qui arme les hooks. Un poste sans `make` suivant
cette recette n'avait donc **aucun garde-fou** : ni contrôle d'identité, ni
`detect-secrets`, exactement l'état que ce lot ferme. `mesuré` le 31 août 2026,
clone frais : après le repli tel qu'il était écrit, `.git/hooks` ne porte **aucun**
hook ; après celui-ci, les quatre attendus, et la suite rend `lint=0`,
`typecheck=0`, `test=0`, `format-check=1` — le rouge d'alors. Ce rouge-la est
**fermé** : les quatre fichiers ont été reformatés par la réparation du lot 3, et
la même suite rend désormais `format-check=0`. Le repli est conservé tel quel ;
seul son verdict attendu a changé.

La différence avec `make` est nulle pour ce `Makefile` — pas de variable, pas de
motif, pas de parallélisme — mais elle existe : dis-le dans ton rapport plutôt
que de laisser croire que `make` a tourné.

Attendu sur `main` (`mesuré`, 31 août 2026) : `ruff` propre, `mypy --strict`
« no issues found in 36 source files », et la suite verte. Le compte canonique
de tests vit dans `README.md`, section Tests — n'en recopie pas la valeur ici.

**`make all` ne mute plus l'arbre : il le constate, et il rend 0.**
Le lot 0b a séparé `format` — qui écrit, geste volontaire — de `format-check` —
qui constate, et qui est la dernière étape de `make all`. Il n'y a donc plus
rien à révoquer avant un commit, et c'est le point : le garde-fou ne repose plus
sur la mémoire du développeur.

**L'exception « rc=2 est le rouge attendu » est FERMÉE, et c'est un changement de
sens.** Elle a vécu du lot 0b à la réparation du lot 3 : quatre fichiers pliés à
la main faisaient sortir la porte en 2, chaque prompt du chantier devait porter
l'exception, chaque conversation la redécouvrait, et **elle a déjà masqué un vrai
rouge une fois**. Les quatre — `extraction.py` et `matter.py` par le lot 3,
`language.py` et `tests/unit/test_wipe_stores.py` par sa réparation — sont
désormais format-propres.

`mesuré` sur la pointe de la réparation du lot 3 :
`uv run ruff format --check src/ tests/` → « 66 files already formatted »,
`rc=0`, et `make all` → `rc=0`.

**Ce que cela change pour toi : un `rc` non nul de `make all` est un défaut, sans
exception à connaître.** N'écris plus « rc=2 attendu » dans un prompt.

Le coût total du reformatage est **mesuré** et il est petit : **20 lignes** de
diff sur les trois commits de style — 9 pour `extraction.py`, 4 pour `matter.py`,
7 pour les deux derniers (`git show --numstat --format= <commit>`). Le récit d'un
« reformatage massif » était surdimensionné de bout en bout. Le détail vit au
registre §5.4.

`format-check` passe **en dernier**, donc `lint`, `typecheck` et `test` rendent
leur verdict avant lui. Pour lire ces trois seuls : `make lint typecheck test`.

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
- **L'image Docling peut ne pas EXISTER, et pas seulement être périmée**
  (`mesuré`, 31 août 2026). Sur un poste, `docker images | grep -i docling`
  rendait le vide : la panne décrite ci-dessus n'a donc pas pu être observée, et
  un `docker compose up -d --build docling-service` a suffi à obtenir
  `models_ready: true`, Python 3.12.14, zéro `PermissionError`. **Ne présente
  pas ces symptômes comme un état courant : mesure d'abord.** L'image pèse
  **10,4 Go** (registre §6.12).
- **Une pile montée depuis un ARBRE DE TRAVAIL est invisible depuis le clone
  principal, et son arbre ne peut plus être supprimé** (`mesuré`, registre
  §4.26). `docker compose ps` depuis le dépôt principal rend 20 avertissements
  de variables vides et **aucune ligne de service**, alors que six conteneurs
  tournent. Et tous les stores sont des bind mounts de cet arbre : le supprimer
  détruirait graphe, vecteurs, objets et Postgres, sans qu'aucun garde-fou git
  ne s'y oppose — `Datas/database/` est ignoré. **Monte la pile depuis le clone
  principal**, ou sache ce que tu ancres.
- **Un chapitre était capturé deux fois**, texte identique au caractère près :
  le doublon a été retiré avant le chantier.
- **SUR UN POSTGRES DAGSTER VIERGE, `docker compose up -d` DÉCLENCHE L'INGESTION
  COMPLÈTE DU CORPUS, EN SILENCE, 15 s APRÈS LE DÉMARRAGE** (`mesuré`, 31 août
  2026). Ce n'est **pas** un défaut : c'est `default_status=RUNNING` sur les
  sensors d'ingestion (§4.18, fermé par le lot 0b) plus des curseurs vides. C'est
  le comportement voulu. **Mais ce n'était écrit nulle part**, et il n'y a aucun
  avertissement au démarrage.

  Corollaire, et c'est lui qu'il faut retenir : **la copie de
  `Datas/database/postgres` est le maillon qui l'empêche**, et c'est précisément
  celui qui a échoué au lot 3 — le répertoire appartient à `root` dans le
  conteneur, `Permission denied`.

  **Le geste, avant le premier `up` sur un poste dont tu veux garder les
  mesures** : soit copier le Postgres en `sudo`, soit
  `docker compose up -d && docker compose stop dagster-daemon` — ou mieux,
  arrêter le daemon **avant** que les sensors n'aient un tick. Sinon les stores
  sont réécrits par une ingestion que personne n'a demandée, et l'antécédent de
  toute mesure en cours est perdu.
- **Le service Docling monte `/app/src` depuis le CLONE PRINCIPAL, pas depuis ton
  arbre de travail.** Donc `docker compose exec docling-service python -m
  src.<module>` exécute le code de `main`, quelle que soit la branche que tu as
  sortie. `mesuré` : la même commande rend `rc=0` « Contrat respecté » avec le
  code de `main` et `rc=1` avec deux anomalies avec celui de la réparation du
  lot 3. Et un `rc=1` peut venir d'un `ImportError` plutôt que d'un garde. **Le
  pilote s'y est fait prendre.** Le geste — monter son propre `src` — est au
  registre §4.27. **Depuis la fusion du lot 3 (`4e28594`), `main` PORTE le code
  réparé** : le conteneur et `main` disent la même chose, et la divergence
  reviendra à la première branche en vol. Ne lis pas cette ligne comme un état,
  mesure-la.
- **`verify_data` et `verify_contract` ne tournent PAS côté hôte.** `chromadb`
  n'est pas dans le `.venv` du clone — il appartient aux dépendances du service
  Docling, pas à celles du dépôt. `mesuré` le 1er septembre 2026 :
  `uv run python -m src.verify_data` rend `rc=1` sur un
  `ModuleNotFoundError: No module named 'chromadb'`, **et ce rc=1 ressemble à un
  garde qui a parlé**. Le geste :
  `docker exec rag_assistant-docling-service-1 python -m src.verify_data`.
- **Les stores survivent au redémarrage de l'instance.** `mesuré` le
  1er septembre 2026, après une coupure et un redémarrage complet de la machine :
  la pile se relève seule, et l'index est intact à l'octet — 4 365 chunks,
  15 196 sommets, 15 374 arêtes, 23 documents, 13 objets MinIO, exactement les
  chiffres d'avant la coupure. Les bind mounts sous `Datas/database/` sont la
  raison. **Ce qui ne survit pas est le contenu des conversations, pas l'index.**

---

## 5. Où on en est

### 5.1 Les branches — il n'y en a plus qu'une

| Réf | Pointe | Rôle |
|---|---|---|
| `main` | *mesure-la* : `git rev-parse --short main` | **tout ce qui est fusionné, y compris ce mandat, le registre et le corpus.** Le lot 0 y est depuis le 29 août 2026 (fusion `b59bf38`), le lot 0b depuis le 31 (fusion `e998e7d`). Un clone frais suffit : il n'y a rien à checkouter. **Le SHA a été retiré de cette case, à dessein** : il a périmé quatre fois en trois jours, dont une fois dans l'heure qui a suivi le commit qui le corrigeait. Une case qui dit « remesure » et donne quand même un chiffre invite à lire le chiffre |
| tag `reference/lot-0-avant-reparation` | `832c566` | la version du lot 0 avant sa **première** réparation. Base de comparaison, pas une ligne de travail — d'où un tag et non une branche |

**Aucun lot n'est en vol.** `lot-0` a été fusionnée (`--no-ff`, `b59bf38`) puis
supprimée, en local **et côté distant**. Le lot 0b l'a été le 31 août 2026
(`--no-ff`, `e998e7d`), branche supprimée des deux côtés dans le même geste. **Le
lot 3 l'a été le 1er septembre 2026** (`--no-ff`, `4e28594`), et avec lui **cinq**
branches et leurs arbres de travail — les deux du lot 3 et les trois des lots 1 et
3 qui ne portaient plus rien hors `main`. Un clone frais ne voit qu'une seule
branche et un tag (`mesuré`, 1er septembre 2026).

**Un répertoire d'arbre de travail a résisté, et il faut le savoir.**
`.claude/worktrees/lot-1-observation-b12761` — 484 Mo — porte un
`Datas/database/` écrit par Docker en `root` : `git worktree remove` rend
« Permission denied ». La **branche** est supprimée et `git worktree prune` a
retiré l'enregistrement, donc git n'en sait plus rien et rien ne l'ancre
(`mesuré` : aucun conteneur n'y monte quoi que ce soit). Il ne reste qu'un
répertoire mort, à retirer en `sudo` quand l'occasion se présente.

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
  d'alors, `format-check` sur les quatre fichiers pliés à la main — et l'arbre
  **non sali**. *(Mesure du 31 août 2026, conservée telle quelle : elle décrit le
  commit de fusion du lot 0b. Ces quatre fichiers ont été reformatés depuis, et
  `make all` rend 0 — §2.4.)* ;
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

### 5.1 ter Le lot 1 : mesuré, audité, zéro commit — et un lot supprimé du plan

Deux conversations, **aucun commit**, aucun diff, corpus intact à l'octet. Le
détail vit au registre §3.2, §3.3, §3.4, §3.5 et §4.21 à §4.27.

Ce que le pilote a vérifié **de ses mains**, sur la pile laissée debout
(`mesuré`, 31 août 2026) :

- **le chiffre qui décide** : **159** arêtes `SectionHeader → SectionHeader`
  contre **29** `Document → SectionHeader`. Un graphe plat rendrait 0 et 188 ;
- les profondeurs des trois documents au chiffre près, et des fils d'Ariane
  réels à trois niveaux ;
- **la cause du chapitre plat, recomptée sur le corpus versionné** : le nombre de
  titres de rang 0 égale le nombre de balises `<h1>` dans **22 chapitres sur
  22**. *(Le pilote avait ajouté ici que `Practical/…/10. Unifying GenAI Systems
  with MLflow.html` était « le seul chapitre retenu sans aucun `<h2>` ». **C'est
  faux : ils sont trois**, et le tableau des balises était sous ses yeux. Les deux
  `Preface.html` n'ont aucun `<h2>` non plus et **s'imbriquent quand même** —
  `{0: 9, 1: 4}` et `{0: 8, 1: 4}` sur le graphe vivant. « Sans aucun `<h2>` »
  n'est donc pas la propriété discriminante ; la vraie est au registre §3.2, et
  elle est désormais assertée par `test_non_platitude.py` à pleine portée.)* ;
- **le no-op**, dans le code : `elements.py:272` fait
  `place(element_id, heading_rank or 0)` et `docling_level_rank` rend `None`
  quand `level` est absent. La correction §3.2 ne pouvait rien changer ;
- **la troncature** : 8 (1,0 %) sur le texte stocké contre **16 (2,1 %)** sur le
  texte encodé, maximum 140 contre **149**, et les 8 déjà tronqués sont **tous**
  `label=table`. `index_report` annonce bien 8 et 1,0 %, **pas 0 %** ;
- **26 images HTML sur 26 sans `minio_url`**, PDF 10/10 ;
- `sequence` sur **2 285 arêtes sur 2 285**, de 0 à 1 269, aucune absente ;
- le schéma `SectionHeader` : `label`, `page_no`, `text`, `minio_url` — **pas de
  `depth`** (§4.11) ;
- **§4.26** : les cinq stores sont des bind mounts de l'arbre de travail du lot,
  et le seul `.env` du poste y vit aussi.

**Le pilote s'est trompé une fois, et l'audit l'a corrigé** : il avait écrit que
le hook figé sur le `.venv` d'un arbre temporaire était « bloquant ». Il ne
l'était pas — le `.venv` existait, son `python` étant un lien vers
l'interpréteur partagé d'`uv`. Le piège était **armé, pas déclenché**. Mesure
l'état, ne déduis pas la conséquence.

### 5.1 quater Le lot 3 : livré, audité, réparé, fusionné

Onze commits de livraison, puis six de réparation, fusionnés le 1er septembre 2026
(`--no-ff`, `4e28594`). Le détail vit au registre.

**Ce que le pilote a remesuré de ses mains avant de trancher, et rien n'a été pris
sur parole** (`mesuré` le 1er septembre 2026) :

| Ce qui décidait | Mesure du pilote |
|---|---|
| le juge de la réparation — la mutation « groupes anonymes comptés comme des titres », `ranking.py:68` → `if True` | `test_non_platitude.py` **ROUGE**, `rc=1`, 2 tests. C'est la bascule : la même mutation le laissait vert avant la réparation |
| `make all` sur le **résultat de la fusion d'essai**, pas sur la branche | `rc=0`, 708 tests, « 67 files already formatted » |
| `make install && make all` sur `main` après fusion | `rc=0` tous les deux |
| le balayage de graines sur `main` fusionné — graine 0 plus 25 aléatoires | **26/26 vertes**, 0 rouge |
| M15 (la levée contre le mélange de deux modèles), M12 (la remontée de racine), M20 (le code de sortie de `verify_data`) | rougissent : 4, 7 et 5 tests |
| les deux mutations que le rapport déclare **survivantes** | survivantes, confirmé : suite entière `rc=0` sous chacune |
| les 11 SHA du lot 3 sous la réparation | intacts — `22c782e` est ancêtre de `7eb0922`, zéro réécriture |
| l'index vivant, dans le conteneur | 4 365 chunks, 15 196 sommets, 23 documents, 13 objets MinIO, `verify_contract` `rc=1` sur ses deux anomalies |
| les mentions interdites, fichiers **et** messages de commit | aucune |

**Et le pilote a trouvé deux affirmations fausses que ni le lot ni son audit
n'avaient vues**, toutes deux nées dans le commit de style `9d2e341` :
`README.md` annonçait « 66 files already formatted » là où la mesure rend **67**,
et sa table des cibles décrivait encore `ruff format src/` alors que le même
commit venait d'étendre les deux cibles à `src/ tests/`. **Le commit qui fermait
un angle mort a créé un mensonge de documentation dans le même geste** — c'est-à-dire
exactement le défaut que le lot 5 doit chasser. Corrigé par le pilote après fusion,
pas renvoyé en troisième tour : la porte était verte et le juge était passé.

*(Le rapport de réparation annonçait « un total de 20 lignes de diff réparties sur
trois commits de style ». Remesuré : 9 + 4 + 7 = **20**. Exact.)*

**Ce que l'audit a établi, et qui est à son crédit** : aucune régression, les six
points du mandat livrés plus §4.5 et §4.14, et **tous ses chiffres reproduits** —
sur le graphe comme sur ChromaDB — par l'audit et par le pilote. Le lot était bon.

**Les cinq bloquants, et le premier est le plus instructif :**

1. **la fixture assertait ce que la production ne produit pas.**
   `document.iterate_items()` ne rend jamais les nœuds de groupe, la capture en
   omettait 262, et deux titres perdaient leur rang **en silence** dans le filtre
   des `None`. Le test assertait 39 titres là où le graphe en porte **41**. Et le
   coût dépassait deux titres : **le test bâti sur du réel était aveugle au
   mécanisme même qu'il existe pour éprouver sur du réel** — la mutation
   « conteneurs anonymes comptés comme des titres » le laissait vert, et seul
   l'arbre fabriqué à la main la voyait. Registre §3.6 bis ;
2. **« le seul chapitre sans `<h2>` »** : ils sont **trois**, et deux s'imbriquent.
   Le faux est né au lot 1, le pilote l'a recopié deux fois en ayant le tableau des
   balises sous les yeux, et le lot 3 l'a recopié de lui, jusque dans le nom d'un
   test. Registre §3.2 ;
3. **trois gardes neufs que rien ne gardait** — M15 (la levée contre le mélange de
   deux modèles d'embedding), M12 (le seul contrôle d'ordre du contrat), M20 (le
   code de sortie de `verify_data`). Registre §4.4 et §4.5 ;
4. **`verify_contract` avait cinq trous**, dont `rc=0` sur un index vide — et tous
   ses contrôles vivent derrière ce garde. Registre §4.4 ;
5. **`index_report` comptait les documents par `filename`** et rendait 22 pour 23,
   les deux `Preface.html` se confondant. Le cas d'école que l'exigence 3 cite
   comme sa preuve, dans un fichier que le lot venait de réécrire.

**La leçon de pilotage, et elle est nouvelle.** Le lot 3 a fait exactement ce
qu'on lui demandait — bâtir le test sur des captures réelles plutôt que sur un
arbre fabriqué — et **c'est cette bonne décision qui a produit le défaut** : une
capture est une donnée, et une donnée peut être incomplète sans que rien ne le
dise. Le test rejouait un algorithme de **remontée** sur une fixture qui ne
portait que les nœuds de contenu. **Quand un test rejoue un parcours, sa fixture
doit porter le graphe complet, pas seulement les nœuds qui l'intéressent** — et
la seule façon de le savoir est de compter ce qu'on jette.

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

**Et le développeur du lot 3 sur les quatre fichiers, deux fois de suite.** Il a
reformaté `extraction.py` et `matter.py` en écarts déclarés, parce que le hook
`ruff-format --check` refuse tout commit qui les touche et que ses gardes y
vivent. **Le pilote s'est rangé, puis il est allé plus loin** : le report des deux
derniers au lot 5 supposait que personne n'y toucherait, alors que le lot 4 vise
`extraction.py` quatre fois. Sa réparation a donc reformaté `language.py` et
`tests/unit/test_wipe_stores.py`, et **`make all` rend 0** — l'exception « rc=2
est le rouge attendu », qui traînait dans chaque prompt depuis le lot 0b et avait
déjà masqué un vrai rouge, disparaît avec eux. Le §5.4 du registre est fermé.

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
| **1** | **Observer sans corriger.** Monter la pile, reconstruire l'image Docling, ingérer 1 chapitre par ouvrage + le PDF. Trois questions : profondeur réelle du graphe (§3.2), `minio_url` sur les images HTML (§3.5), troncature réelle à l'embedding (§3.4) | contrainte **6** | ✅ **mesuré le 31 août 2026**, sans aucun commit — et son audit indépendant a élargi la mesure de 2 chapitres à **22**. Résultats au registre §3.2, §3.4, §3.5 et §4.21 à §4.27 |
| **2** | ~~La hiérarchie des titres~~ | — | ❌ **SUPPRIMÉ le 31 août 2026.** Pas parce que le graphe est imbriqué — il l'est à 21/22 — mais parce que **la correction qu'il portait est un no-op** : `level` est absent sur les titres concernés et `elements.py:272` fait `heading_rank or 0`, donc le rang reste 0 (registre §3.2). §3.3 est **retourné** : les deux tests qu'il fallait « amender ensemble » assertent le comportement juste. §4.11 en sort vivant et monte au lot 3 ; §4.12 reste consigné inerte avec sa vraie condition d'activation — l'entrée d'un Markdown au corpus |
| **3** | ✅ **FUSIONNÉ le 1er septembre 2026** (`--no-ff`, `4e28594`). **Instruments et gardes.** §3.4 (l'instrument sous-comptait la troncature **de moitié**), §4.4 (dont le garde de `sequence`, §6.16), §4.14, §4.5, §4.11 — le niveau du titre dans le graphe — §4.21, §4.23, §4.24, et le test de non-platitude que l'audit du lot 1 réclamait | la confiance dans tout chiffre produit après l'ingestion, **et** un agent capable de lire la hiérarchie qui existe | ✅ **livré le 31 août 2026** — **onze** commits, **les six points du mandat**, plus §4.5 et §4.14. Il a fermé §3.4, §4.4, §4.5, §4.11, §4.14, §4.21, §4.23, §4.24 et §5.3. **Audité, puis RÉPARÉ, puis fusionné** : l'audit n'a trouvé aucune régression et a reproduit tous ses chiffres, mais cinq bloquants — dont la fixture du test de non-platitude, qui assertait 39 titres là où la production en produit 41. Les cinq sont fermés, le juge de la réparation est passé sous la main du pilote, et `make all` rend **0** sur `main`. Voir §5.1 quater. Il laisse **deux mutations survivantes** consignées et non fermées (§4.12, §4.28.d) et **cinq constats** au lot 4 (§4.28). *(Cette case portait « dix commits » : un compte ne peut pas s'inclure lui-même, et le lot 0b s'était fait prendre pareil. Elle portait aussi « il a fermé §6.16 » : le registre le garde **ouvert**, et le registre a raison — la moitié « écrire au contrat côté agent » n'est pas faite. Le compte de tests a été retiré : son site canonique est `README.md`, section Tests.)*
| **4** | La perte silencieuse : §4.1, §4.2, §4.6, §4.7, §4.3, §4.10, §5.6, plus §4.15 à §4.17 et §4.19 — la famille « un run bloqué gèle tout », qui se ferme d'un geste par le *run monitoring* absent de `dagster.yaml`. **Plus §4.22** (six pages du PDF sans aucun élément, leur texte attribué à la page précédente) et **§4.25** (les URL du graphe rendent 403 en GET anonyme). **Plus les cinq constats que la réparation du lot 3 lui a versés, §4.28** : `chunk_count` mensonger (a), les 199 images HTML absentes du bucket **et** `Datas/.cleaned/` que `wipe_stores` ne purge pas — à lire **avant** d'attaquer §3.5 (b), le daemon arrêté et l'exigence 5 inéprouvable (c), et le déverrouillage de `nebula.py` pour que `document_vid` soit enfin gardé (d) | la certitude que le corpus ingéré est le corpus complet | **à faire — c'est l'action suivante, §7** |
| **5** | Code mort et documentation contre code : §5.1, §5.2, §5.7, §5.8, tout le §6 — **dont §6.16 (les trois réserves de `sequence` à écrire au contrat) et §6.17 (chiffres et renvois faux)**, et les deux docstrings de `vectors.py` qui promettent « plus de troncature » alors que 8 chunks sortent de la fenêtre | la lisibilité, et l'arrêt des faux réglages | à faire |
| **6** | Ingestion complète → `verify_contract` → `index_report` → **puis** les 30 questions | la première campagne de référence | **à faire — et ses trois premières étapes sont déjà faites par accident** (§4.28.e). **Tranché par le pilote le 1er septembre 2026 : les 30 questions attendent le lot 4, et l'ordre n'est pas négociable.** `compute_id` dérive de `(identity.key, page_no, position_in_page, text[:50])` ; §4.22 déplace `page_no` et §4.6 / §4.7 déplacent `text` — tous trois au lot 4. Un jeu de questions écrit avant est un jeu à refaire, et l'écrire *quand même* pour « avancer » est le piège : il paraîtrait bon jusqu'à la réingestion. Ce qui est acquis en revanche l'est vraiment : le corpus complet est indexé et les deux instruments ont rendu leur verdict dessus, ce qui donne au lot 4 son antécédent mesuré |

**Le lot 1 a payé son pari, et pas comme prévu.** Il n'a rien corrigé, il n'a
produit aucun commit, il a coûté quelques minutes d'ingestion — et il a
**supprimé un lot entier du plan** tout en faisant naître sept constats
(§4.21 à §4.27). Le pari « observer avant de corriger » est le meilleur retour
du chantier à ce jour.

**Et il a montré où était le vrai risque : la représentativité.** Le lot 1
avait mesuré 2 chapitres sur 22, et concluait juste. Son audit a mesuré les
**22** et a trouvé le chapitre plat, les 46 % de rangs PDF issus d'un repli, et
le fait que la correction était un no-op — trois choses qu'aucun rapport en
prose ne pouvait porter. **Quand une mesure décide du plan, mesure tout le
corpus, ou dis le périmètre exact où ta conclusion vaut.**

**La réserve du pilote a payé, et elle est conservée telle quelle parce qu'elle
dit comment on l'a gagnée.** Elle disait : « le constat §3.2 est un raisonnement
sur le comportement du backend HTML de Docling, **pas une observation** ; il est
étiqueté `supposé` ; si Docling imbrique bien, le constat tombe et le lot 2
disparaît ». Le lot 1 a mesuré, son audit a élargi la mesure aux 22 chapitres, et
**le lot 2 a disparu** — pour une raison encore plus forte que celle prévue : la
correction était un no-op. Un constat étiqueté `supposé` et traité comme tel a
économisé un lot entier.

---

## 7. L'action suivante

**Livrer le lot 4 — la perte silencieuse.** Le prompt prêt à distribuer est en
**annexe A**, et il va à une conversation NEUVE.

C'est le lot le plus lourd du plan, et il l'est devenu : il portait déjà onze
constats, la réparation du lot 3 lui en a versé cinq de plus (§4.28), et il touche
`extraction.py` quatre fois, `cleaning.py` deux fois, `nebula.py`, `factory.py` et
`dagster.yaml`. **Son fil conducteur est unique : le pipeline perd du texte, des
images et des documents sans qu'aucun code de sortie ne le dise.** Tous ses
constats sont de cette famille, et c'est pourquoi ils vont ensemble.

**Ce que le lot 4 déverrouille au-delà de lui-même** : les `element_id` bougeront
(§4.22 déplace `page_no`, §4.6 et §4.7 déplacent `text`), donc c'est lui qui rend
le lot 6 écrivable — voir §6. Rien ne sert d'écrire les 30 questions avant.

**Et il hérite d'un devoir de mesure**, parce que l'index vivant a été produit par
le code du lot 3 : toute mesure d'antécédent est déjà prise, à la ligne près, dans
le registre §4.28 et au §7.2 ci-dessous. **Ne les redérive pas, remesure-les** —
c'est le principe du chantier, et il vaut aussi pour les chiffres que le chantier
a lui-même écrits.

Puis, dans l'ordre invariable :

1. lire le rapport ;
2. **faire auditer par une conversation qui n'en a écrit aucune ligne.** En dix
   passages, l'audit indépendant n'a **jamais** rien manqué — y compris sur un lot
   qui n'avait produit aucun commit ;
3. lire le diff soi-même et faire tourner `make all` de ses mains, **y compris
   sur le résultat de la fusion** ;
4. **alors seulement**, trancher la fusion ;
5. si fusion : `--no-ff`, jamais `--ff-only`, jamais de rebase. Puis **relancer
   `make install` depuis le clone principal**, et **vérifier qu'aucun projet
   Compose ni bind mount n'ancre l'arbre de travail** avant de supprimer quoi que
   ce soit. Alors seulement, supprimer la branche, local **et distant** ;
6. mettre le registre à jour ;
7. écrire le prompt du lot suivant — et **relire l'annexe A contre `git`**, pas
   contre sa mémoire.

**Une leçon de rédaction de prompt, payée par le lot 1.** Son prompt annonçait le
chiffre attendu (« le rapport annoncera 0 % de troncature »). La mesure valait
1,0 %, et un développeur qui cherchait à confirmer aurait lu ce 1,0 % comme un
bruit d'arrondi et raté le défaut le plus intéressant des trois. **N'annonce
jamais le résultat attendu d'une mesure que tu commandes** — donne le mécanisme,
pas le chiffre.

**Ce que l'audit du lot 3 avait à regarder, et qui est désormais de l'histoire**
— trois renversements de plan qu'il a tranchés et deux écarts au mandat qu'il a
validés — vit au registre (§4.11, §4.14, §5.4) et au §5.1 quater. Ne le recopie
pas ici : cette section dit l'action **suivante**, pas la précédente.

### 7.1 L'état de la pile — §4.26 est TRAITÉ

**La pile ne vit plus dans un arbre de travail.** Le lot 3 l'a remontée depuis le
clone principal, et il a **déménagé les données du lot 1** plutôt que de
réingérer : `docker compose down` depuis l'arbre du lot 1, copie de `.env` et de
`Datas/database/` vers `/home/ubuntu/RAG/rag-ingestion-pipeline`, puis
`docker compose up -d`. Le graphe a survécu à l'octet — 2 288 sommets avant,
2 288 après (`mesuré`).

**État au 1er septembre 2026 (`mesuré`), après une coupure et un redémarrage
complet de l'instance** : projet compose **`rag-ingestion-pipeline`**, neuf
services debout, `.env` et bind mounts dans le clone principal, aucun montage vers
un arbre de travail. **L'index est intact à l'octet** — 4 365 chunks, 15 196
sommets, 15 374 arêtes, 23 documents, 13 objets MinIO, exactement les chiffres
d'avant la coupure. La pile se relève seule ; c'est un fait acquis, pas une chance.

État au 31 août 2026 (`mesuré`) : projet compose **`rag-ingestion-pipeline`**,
bind mounts sous le clone principal, `.env` dans le clone principal.
**L'arbre `lot-1-observation-b12761` n'ancre plus rien et peut être supprimé** —
ses données y restent en copie, filet volontaire.

**Ce qui n'a PAS été déménagé** : le `Datas/database/postgres` du lot 1,
inaccessible en lecture (`root`, `Permission denied`). Le Postgres de Dagster est
donc reparti **vierge**, et il faut savoir ce que cela a fait : les curseurs des
sensors sont repartis de zéro, et **les sensors étant livrés armés** (§4.18,
fermé par le lot 0b), le simple `docker compose up -d` a déclenché
**l'ingestion complète du corpus**. Ce n'est pas un défaut — c'est le
comportement voulu — mais c'est un effet à connaître avant de remonter la pile.

Il en est sorti un bénéfice : le lot 3 a mesuré ses antécédents sur le **corpus
complet** ingéré par le code de `main`, et non sur les 3 documents du lot 1.

### 7.2 Ce que le lot 3 laisse au poste

- **`verify_contract` sort en 1, et c'est le verdict juste.** `mesuré` par le
  pilote le 1er septembre 2026, dans le conteneur, avec le code de `main`
  post-fusion :

  ```
  chunks examines 4365 · element_id au mauvais format 0 · element_id != graph_node_id 0
  ids de chunk suffixes en #n 974 · chunk_index hors de chunk_count 0
  elements au jeu de chunks troue 2 · aretes PARENT_OF 15173 · aretes sans sequence 0
  inversions de page 0 · sommets sans depth 0/15173 · colonnes du tag Document 7
  sommets visuels sans minio_url 251/264 · ancres presentes dans le graphe 3750/3750
  ```

  Deux anomalies, et elles sont **toutes deux au lot 4** : les 2 éléments au jeu
  de chunks troué (§4.28.a) et les 251 sommets visuels sur 264 sans URL (§3.5 et
  §4.28.b). **Lis « 251 sur 264 », pas « 199 »** : le chiffre de 199 images vient
  d'un comptage côté captures HTML, celui de 251 du graphe ingéré — ils ne
  mesurent pas la même chose et le second est celui que l'instrument rend. Il
  redeviendra vert quand le lot 4 aura réparé la chaîne d'images et qu'une
  réingestion aura inscrit le modèle ;
- **`dagster-daemon` est arrêté, et il l'est RESTÉ à travers le redémarrage de
  l'instance** (`mesuré` le 1er septembre 2026 : `exited`, les neuf autres
  services debout). Un service arrêté par `docker compose stop` ne repart pas au
  redémarrage de la machine, quelle que soit sa politique de redémarrage — c'est
  ce qui a protégé l'index. `docker compose start dagster-daemon` pour le
  reprendre, et **sache ce que tu déclenches** : les sensors sont livrés armés
  (§4.18), donc le daemon reprend l'ingestion. Registre §4.28.c : un run est
  `QUEUED` et l'historique porte 67 `ReindexError` ;
- **le space `rag_space` a été recréé** : le lot y avait éprouvé la réversibilité
  d'un `ALTER ... DROP`, et Nebula refuse ensuite le ré-ajout de la colonne. Le
  corpus complet y est réingéré avec le code du lot.

Puis, dans l'ordre invariable :

1. lire le rapport ;
2. **faire auditer par une conversation qui n'en a écrit aucune ligne** ;
3. lire le diff soi-même et faire tourner `make all` de ses mains, **y compris
   sur le résultat de la fusion** ;
4. **alors seulement**, trancher la fusion ;
5. si fusion : `--no-ff`, jamais `--ff-only`, jamais de rebase. Puis **relancer
   `make install` depuis le clone principal**, et **vérifier qu'aucun projet
   Compose ni bind mount n'ancre l'arbre de travail** avant de supprimer quoi que
   ce soit. Alors seulement, supprimer la branche, local **et distant** ;
6. mettre le registre à jour ;
7. écrire le prompt du lot suivant — et **relire l'annexe A contre `git`**, pas
   contre sa mémoire.

**Une leçon de rédaction de prompt, payée par le lot 1.** Son prompt annonçait le
chiffre attendu (« le rapport annoncera 0 % de troncature »). La mesure valait
1,0 %, et un développeur qui cherchait à confirmer aurait lu ce 1,0 % comme un
bruit d'arrondi et raté le défaut le plus intéressant des trois. **N'annonce
jamais le résultat attendu d'une mesure que tu commandes** — donne le mécanisme,
pas le chiffre.

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

Celles que le lot 1 a ajoutées :

- **Un raisonnement juste sur un antécédent faux produit une conclusion fausse,
  et il se relit comme une preuve.** Le constat §3.2 était logiquement
  impeccable : *si* Docling n'imbrique pas, *alors* le graphe est plat. Personne
  n'avait mesuré le *si* — pendant des semaines, et le plan portait un lot entier
  dessus. **Cherche l'antécédent avant d'auditer le raisonnement.**
- **Une conclusion tirée d'un échantillon doit porter son périmètre, ou elle sera
  lue comme universelle.** Le lot 1 mesurait 2 chapitres sur 22 et écrivait « le
  constat tombe ». Son audit a mesuré les 22 : 21 imbriquent, **1 est plat**. La
  conclusion ne tombait pas, elle se **bornait** — et le chapitre plat serait
  devenu un défaut découvert au lot 6, sur le corpus complet.
- **N'annonce jamais le résultat attendu d'une mesure que tu commandes.** Le
  prompt du lot 1 donnait « 0 % de troncature » ; la réponse était 1,0 %, et un
  développeur qui confirme aurait vu un bruit d'arrondi au lieu du défaut.
- **Une correction peut être un no-op, et son avertissement coûter deux tests
  justes.** Avant de planifier une correction, mesure ce qu'elle change.
- **Une mesure qui décide du plan doit laisser un artefact rejouable.** Les
  scripts du lot 1 étaient jetables — donc sa preuve vivait dans un rapport, et
  la conversation suivante ne pouvait que *recroire* ou *tout refaire*.

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
- **Livrer autre chose que la forme demandée.** L'utilisateur a demandé un
  `/compact` — des instructions de compaction — et le pilote a produit un prompt
  pour une conversation neuve. Un demi-million de tokens de contexte, une
  consigne d'une ligne, et la mauvaise forme. **Quand l'utilisateur nomme la forme
  du livrable, produis cette forme** ; l'utilité de ce qu'on livre à la place ne
  rachète rien.
- **Croire qu'un commit qui ferme un angle mort ne peut pas en ouvrir un.** Le
  commit `9d2e341` a étendu `format` et `format-check` à `tests/` — le bon geste,
  qui fermait l'angle mort D7 — et a laissé la table du `README.md` décrire
  `ruff format src/`, plus un « 66 files » là où la mesure rend 67. Le lot 5
  s'appelle « la documentation contre le code » : **son gibier naît dans les
  commits qui font bien leur travail**, parce que c'est là que personne ne
  relit la phrase qui décrivait l'ancien état. Trouvé par le pilote après la
  fusion, ni par le lot ni par son audit.

Traite tes propres affirmations comme des hypothèses. Vérifie avant d'écrire un
chiffre. Relis le code avant d'affirmer ce qu'il fait.

---

# Annexe A — Prompt prêt à distribuer : le lot 4

> **Routage : ceci va à une conversation NEUVE, à nommer `Conv' 11 LOT-4`.
> Rien d'autre à envoyer, à personne.**
>
> Les prompts des lots 0, 0b, 1 et 3 sont consommés. Ce qu'ils ont produit vit au
> registre §8 et aux §5.1 bis, 5.1 ter, 5.1 quater, 5.2 et 5.3 ci-dessus. **Le lot
> 2 a été supprimé du plan** : voir §6.
>
> **Relis cette annexe contre `git` avant de la coller.** La pointe de `main`, le
> chemin du dépôt, la présence de `make` et l'état de la pile sont des faits de
> POSTE : ils périment, et ce prompt a déjà été périmé une fois par un `main` qui
> avait bougé dans l'heure.

---

Tu livres le **lot 4 du chantier de refonte de `rag-ingestion-pipeline` : la perte
silencieuse**.

## Avant d'écrire une ligne

Lis **en entier** `documentation/pilotage_du_chantier.md` et
`documentation/axes_amelioration.md`, à la pointe de `main`. Ils sont
autosuffisants : le rôle, le contrat avec `rag-agent-chat`, le plan de lots, les
conventions de commit, les règles imposées, les leçons qui ont trouvé les défauts.
**Ne me demande pas de contexte que ces deux fichiers portent.**

Puis **mesure l'état du poste au lieu de le lire** : la pointe de `main`, la
présence de `make`, l'état de la pile Docker, la porte qualité. Le mandat §2 dit
pourquoi : un poste a déjà été repris en croyant sur parole qu'il était le poste
d'origine.

## Le fil conducteur, et il est unique

**Le pipeline perd du texte, des images et des documents sans qu'aucun code de
sortie ne le dise.** Chaque constat de ce lot est de cette famille. Ce n'est pas
une liste de corrections indépendantes : c'est un seul défaut de conception —
*l'absence de compteur là où il y a perte* — vu sous seize angles.

## Le périmètre, et il est fermé

Aux sections du registre, et à elles seules :

**La perte de texte**
- **§4.1** — un lot PDF en échec laisse un document **partiel écrit dans les
  stores**, partition rouge, index pollué, et `verify_contract` ne peut pas le
  voir parce que les `element_id` écrits sont valides ;
- **§4.6** — `min_text_ratio = 0.05` : un nettoyage peut jeter **95 %** du texte
  sans journal ni métadonnée Dagster ;
- **§4.7** et **§5.6** — quatre `except Exception` muets, sans justification
  écrite au site : `cleaning.py` (le plus grave, une stratégie qui plante devient
  un non-candidat silencieux) et les trois de `wipe_stores.py`. **À traiter d'une
  seule main** ;
- **§4.22** — six pages du PDF (8, 18, 19, 25, 68, 69 sur 71) n'ont **aucun**
  élément : leur texte est attribué à la **page précédente**. Toute citation
  « page 7 » couvre en réalité 7 et 8 ;
- **§4.28.a** — `chunk_count` est **mensonger** : il est fixé avant le filtrage
  des chunks. Deux éléments perdent un morceau, et l'agent reconstitue un texte
  troué. **Le contrôle est déjà livré** (§4.4) ; ce qui te revient est la
  **cause** — décider si `chunk_count` se recalcule après filtrage, ou si les
  chunks filtrés doivent cesser de l'être. Tranche, et écris pourquoi.

**La perte d'images**
- **§3.5** et **§4.28.b** — la chaîne d'images HTML est rompue, et **§4.28.b
  d'abord** : les images ne sont pas seulement absentes du graphe, elles sont
  absentes du **bucket**. `Datas/.cleaned/` référence encore des URL MinIO
  d'objets inexistants, et **`wipe_stores` ne purge pas `Datas/.cleaned/`** — donc
  une purge suivie d'une réingestion repart du HTML nettoyé périmé. Seule une
  exécution Dagster de l'asset `cleaned_html` les restaure. **Réextraire ne suffit
  pas, et c'est le piège de ce lot** ;
- **§4.25** — les URL du graphe rendent **403 en GET anonyme** : la forme stockée
  est `http://minio:9000/…`, inutilisable hors du réseau Docker. À trancher, en
  écrivant ce que l'agent peut en faire.

**La perte de documents**
- **§4.2** — une réingestion d'un document modifié laisse des **orphelins** :
  `delete_document` existe et **n'a aucun appelant**, alors que le sensor déclenche
  sur `mtime`. Le chemin nominal est celui qui casse ;
- **§4.10** — un doublon exact rend une partition **verte avec zéro élément** :
  dans l'interface Dagster, un document écarté ressemble à un document ingéré vide ;
- **§4.28.d** — `document_vid` reçoit la bonne clé de ses trois appelants, mais
  **aucun test ne le garde** : `document_vid(identity.key)` →
  `document_vid(identity.filename)` laisse la suite entièrement verte, et ferait
  collisionner les deux `Preface.html` sur un seul sommet. La cause est mécanique :
  `nebula.py` importe `nebula3` au niveau du module, donc aucun test ne peut
  l'importer côté hôte. **C'est le cinquième module dans ce cas, et le seul qui
  reste. Déverrouille-le** — les quatre autres l'ont été aux lots 3 et 3-réparé,
  et le geste est écrit dans `vectors.py`.

**La famine : un run bloqué gèle tout**
- **§4.15 à §4.17** et **§4.28.c** — un run coincé en `STARTED` bloque la
  réindexation **indéfiniment**, sans délai de garde ni alerte. Le *run monitoring*
  de Dagster est **absent de `dagster.yaml`** : c'est là que la famille entière se
  ferme d'un geste, plutôt qu'au cas par cas dans chaque sensor. §4.28.c dit l'état
  réel du poste : daemon arrêté, un run `QUEUED`, **67 `ReindexError`** dans
  l'historique, et **l'exigence 5 du contrat n'est pas éprouvable en l'état** ;
- **§4.19** — le refus de démarrer hors contrat n'est prouvé par **aucun test** :
  retirer la ligne, ou la déplacer dans le `try`, laisse la suite verte. C'est le
  *fail-fast* que le `README.md` vend qui repose sur une relecture ;
- **§4.3** — `nebula.py:125` code en dur `("root", "nebula")`. `NEBULA_USER` et
  `NEBULA_PASSWORD` existent dans `.env.example` et ne sont exposés par **aucun**
  settings : le `.env` mentait sur ce qui est lu. Trois sites.

**Ce qui n'est PAS de ce lot** : §5.1 à §5.3, §5.7, §5.8 et tout le §6 sont au lot
5 ; les 30 questions sont au lot 6 et **elles attendent ce lot-ci** — voir §6 du
mandat. Ne les écris pas.

## Le devoir de mesure, et il est particulier ici

**L'index vivant a été produit par le code du lot 3, et il est intact.** Tes
antécédents sont donc déjà mesurés, à la ligne près, au registre §4.28 et au §7.2
du mandat. **Ne les recopie pas : remesure-les de tes mains, puis dis si tu
retrouves le chiffre.** Un chiffre du chantier qu'on reprend sans le remesurer est
la façon dont « le seul chapitre sans `<h2>` » a vécu du lot 1 au lot 3, recopié
par trois conversations dont le pilote.

Et prends les pièges de mesure du registre **§4.27** avant ta première commande.
Trois d'entre eux ont déjà attrapé quelqu'un ; le pilote s'est fait prendre par
deux.

## Ce que je juge à la lecture de ton rapport

1. **Chaque garde neuf rougit à la mutation du code livré.** Pas « un test
   existe » : la mutation, le site, le `rc`, le nombre de rouges. Un garde qui
   reste vert quand on casse ce qu'il garde ne compte pas, et trois s'étaient
   glissés ainsi dans le lot 3 ;
2. **rouge d'abord.** Le test échoue avant la correction, et tu montres les deux
   états ;
3. **`make all` rend 0**, et il n'y a **plus aucune exception à connaître** — le
   « rc=2 attendu » est mort avec la réparation du lot 3. Un `rc` non nul est un
   défaut. Plus le balayage de graines `PYTHONHASHSEED` (0 plus ≥ 25 aléatoires) ;
4. **tes écarts au mandat, déclarés comme écarts au moment où tu les prends**, pas
   découverts par l'audit. Le lot 3 en a déclaré deux et le pilote lui a donné
   raison sur les deux ;
5. **ce que tu consignes sans traiter**, avec la forme du garde à écrire. Trouver
   et laisser en le disant vaut mieux que corriger hors périmètre.

## Les règles, et elles ne se négocient pas

Elles sont au **§9 du mandat**, en entier. Les quatre qui coûtent le plus cher
quand on les oublie :

- **l'identité git se vérifie sur l'ADRESSE, jamais sur le nom** — deux identités
  portent le même nom, et sept commits partis avec la mauvaise adresse ont coûté
  un dépôt entier. Jamais de `--no-verify` ;
- **aucune mention** de Claude, Claude Code, Anthropic, Copilot ou ChatGPT nulle
  part — code, documentation, messages de commit. Aucun trailer `Co-Authored-By` ;
- **ne modifie jamais le corpus.** Si une mesure y touche, restaure et vérifie le
  sha. `source_path` et le contenu entrent dans le calcul d'`element_id` ;
- **aucun test désactivé**, aucun `skip`, `xfail`, `type: ignore`, `noqa`, aucune
  règle `ruff`/`mypy` relâchée, aucun `except` élargi sans justification écrite au
  site. C'est le lot qui en **retire** quatre : n'en ajoute pas.

Travaille sur une branche à toi. **Ne fusionne rien** : tu livres, une conversation
qui n'a écrit aucune de tes lignes t'audite, et le pilote tranche. En dix passages,
l'audit indépendant n'a jamais rien manqué.

Quand tu as fini : un rapport, et **estime honnêtement le temps** — le réparateur
du lot 3 a annoncé dix minutes et il en a fallu vingt, le balayage de graines
étant relancé à chaque commit.
