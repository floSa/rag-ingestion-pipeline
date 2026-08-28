# Piloter le chantier d'audit et de refonte

Ce fichier est le **mandat du pilote**. Il est écrit pour être lu par une
conversation qui n'a aucun historique : elle arrive, elle lit ceci, elle sait
où on en est, ce qui a été décidé, pourquoi, et quelle est l'action suivante.

Il est autosuffisant. Rien de ce qui suit ne suppose d'avoir vu une
conversation précédente.

Son compagnon obligatoire est [`axes_amelioration.md`](axes_amelioration.md),
le registre : ce fichier-ci dit **comment on travaille**, le registre dit **ce
qu'il reste à faire**. Les deux se tiennent à jour lot par lot.

> **Dernière mise à jour : 28 août 2026, à la fin du lot 0.**
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
> croire — les branches, et `make install && make all` sur `lot-0` — puis
> dis-moi où on en est et quelle est la prochaine action. Un prompt à la fois.

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

Le hook vit sous `scripts/git-hooks/pre-commit` (versionné, donc il arrive avec
le clone) mais **il n'est pas actif tant qu'il n'est pas installé** :

```bash
cp scripts/git-hooks/pre-commit .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
```

Puis l'identité, dans chaque arbre de travail :

```bash
git config user.name "floSa" && git config user.email "florian.horellou@gmail.com"
```

Adresses autorisées, et elles seules : `florian.horellou@gmail.com`,
`florian_horellou@laposte.net`. Le hook ne se contourne jamais : pas de
`--no-verify`.

Le distant est **personnel** : `git@github.com-perso:floSa/rag-ingestion-pipeline.git`.

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

Attendu au 28 août 2026, sur `lot-0`
(`mesuré`) : `ruff` propre, `mypy --strict` sans erreur, **508 tests verts**.
Le compte canonique de tests vit dans `README.md`, section Tests. Sur `main`
(77d4f5b) : **395**.

Si le compte diffère, ne suppose rien : c'est le dépôt qui a bougé, et il faut
comprendre pourquoi avant de continuer.

**Et lis ce nombre avec sa réserve.** 508 est le compte des tests qui passent,
pas le compte des tests qui prouvent quelque chose. Le fichier
`tests/unit/test_hierarchie_bout_en_bout.py` — 20 tests environ — **fabrique
l'arbre imbriqué qu'il prétend vérifier** et reste vert des deux côtés de son
défaut (registre §3.3). Le développeur du lot 0 l'a signalé lui-même plutôt que
de laisser le chiffre parler seul. Un compte de tests est une mesure de volume,
jamais une mesure de garantie.

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

- **Les stores sont vides et prêts.** Bind mounts sous `Datas/database/`, pas
  de volumes nommés. Aucune collection ChromaDB, 0 objet MinIO, `rag_space`
  recréé vide.
- **Le réseau `rag_network` est créé par ce dépôt.** `rag-agent-chat` s'y
  raccroche en `external: true` et ne démarre pas sans lui.
- **Aucun GPU n'est requis.** L'ingestion tourne sur processeur. La réservation
  `nvidia` écrite en dur rendait le service **incréable** sans runtime nvidia ;
  elle vit désormais dans un `docker-compose.gpu.yml` superposable.
- **L'image Docling est à reconstruire.** Le conteneur tournait en Python 3.10
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

### 5.1 Les branches — il n'y en a que deux, et c'est voulu

| Réf | Pointe | Rôle |
|---|---|---|
| `main` | | **tout ce qui est fusionné, y compris ce mandat et le registre.** Un clone frais suffit : il n'y a rien à checkouter pour reprendre le chantier |
| `lot-0` | `390ce8a` | **le seul lot en vol.** Livré, en attente de son audit indépendant puis de sa fusion. 8 commits, avance rapide possible sur `main` |
| tag `reference/lot-0-avant-reparation` | `832c566` | la version du lot 0 **avant** sa réparation. Ce n'est pas une ligne de travail, c'est une **base de comparaison** — d'où un tag et non une branche |

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
  Rien n'est perdu : `git range-diff reference/lot-0-avant-reparation~5..reference/lot-0-avant-reparation lot-0`
  reste possible, et c'est l'angle A du mandat d'audit ;
- `claude/rag-ingestion-restore-9e5aa5` — 0 commit au-dessus de `main`, jamais
  poussée (`mesuré`). Vide.

**Ce qui n'a PAS été fait, et ne le sera pas avant l'audit :** fusionner `lot-0`
dans `main`. Ce serait la façon la plus rapide de n'avoir qu'une branche, et
c'est précisément la règle qu'on ne casse pas.

**L'écart au mandat du lot 0, tranché.** Le lot 0 avait été livré sur une
branche neuve plutôt qu'en réparant celle du mandat, et proposait soit de
force-pousser l'historique réparé sur l'originale, soit de livrer droit dans
`main`. Les deux ont été refusées : la première aurait détruit la base de
comparaison au moment où l'audit en a besoin, la seconde aurait fusionné sans
audit. Le choix du développeur — livrer ailleurs et garder l'originale — était
le bon ; c'est de ne pas l'avoir posé comme un écart au moment de le faire qui
était le défaut.

### 5.2 Ce que le lot 0 a livré, et ce que j'en ai vérifié moi-même

Les 8 commits de `lot-0` :

```
eaa8a8e build: declarer le groupe de dependances de developpement dans pyproject.toml
98bb20d feat: refuser de demarrer sur un modele d'embedding hors contrat
7d587b0 fix: purger aussi le bucket MinIO, et echouer sur une purge partielle
cc338b8 fix: ne plus reserver de GPU par defaut, le service etait increable sans nvidia
3eb5aef feat: appeler POST /reindex sur l'agent en fin d'ingestion
28cce7d test: prouver que le rang des titres remonte, du item Docling au graphe
a3ad1f4 fix: reindexer l'agent une fois par rafale, et non une fois par document
390ce8a docs: le compte de tests, mesure, a son site canonique dans le README
```

**Vérifié par le pilote, pas repris du rapport** (`mesuré`, 28 août 2026) :

- avance rapide possible sur `main` ;
- aucune mention de Claude, Anthropic, Copilot ou ChatGPT, ni dans les messages
  de commit, ni dans le diff ; aucun trailer `Co-Authored-By` ;
- auteurs : `floSa <florian.horellou@gmail.com>` et
  `Florian Horellou <florian_horellou@laposte.net>`, tous deux dans la liste
  blanche ;
- après `rm -rf .venv && uv sync` : `pytest`, `ruff` et `mypy` sont bien
  installés — le groupe `dev` de `pyproject.toml` fonctionne, `pip-audit` et
  `pre-commit` y sont aussi, donc `make audit` n'est pas cassé par la
  suppression de `requirements-dev.txt` ;
- sur la pointe : `ruff check src/` propre, `mypy src/` « no issues found in 36
  source files », **508 tests verts** ;
- `ruff format --check src/` : **3 fichiers seraient reformatés**
  (`extraction.py`, `language.py`, `matter.py`) — le constat §5.4 du registre
  est confirmé, il préexiste au lot 0 et n'est pas de son fait.

**Non encore vérifié par le pilote** : le contenu de `reindex_job.py` (236
lignes neuves), la validité des 13 mutations annoncées, et l'intégrité du
replantage des 5 commits d'origine. **C'est le travail de l'audit
indépendant.**

### 5.3 Ce que le lot 0 a fait apparaître

Trois constats nouveaux, tous consignés au registre §5.4, §5.5 et §5.6 :

- **§5.4** — `make format` mute l'arbre avant que `make all` ne le contrôle, et
  `main` n'est pas format-propre ;
- **§5.5** — les hooks du framework `pre-commit` ne sont installés nulle part :
  `ruff`, `ruff-format`, `detect-secrets`, `check-yaml` sont déclarés dans
  `.pre-commit-config.yaml` et **rien ne les exécute**. `detect-secrets` n'a
  donc jamais servi de garde-fou. Arbitrage à faire : `.git/hooks/pre-commit`
  est déjà occupé par le contrôle d'identité, et `pre-commit install`
  l'écraserait ;
- **§5.6** — trois `except Exception` nus sans justification au site dans
  `wipe_stores.py`.

### 5.4 Le désaccord du développeur, et ma position

Le développeur a livré « **une réindexation par rafale** » là où le contrat dit
« une fois l'ingestion terminée », et il l'a dit lui-même plutôt que de laisser
passer. Il a raison sur les deux points : « fin d'ingestion » n'existe pas comme
événement dans cette architecture — un job par source, un run par fichier, des
partitions créées au fil de l'eau — et attendre un signal qui n'existe pas
reviendrait à ne jamais réindexer.

**Position du pilote : c'est la bonne lecture, et elle doit rester écrite comme
un écart assumé, pas comme une conformité.** Le levier si l'agent supporte mal
des réindexations rapprochées est le `minimum_interval_seconds` du sensor
(30 s aujourd'hui). C'est un réglage, pas une refonte.

---

## 6. Le plan de lots

L'ordre se décide au **coût de l'attente**, pas à la sévérité. Un défaut grave
mais inerte attend ; un défaut mineur qui bloque une mesure passe devant.

| Lot | Contenu | Débloque | État |
|---|---|---|---|
| **0** | Réparer et fusionner la branche de restauration : porte qualité reproductible, `mypy`, `/reindex` déplacé | démarrage de la stack, exigences **1** et **5** | **livré, à auditer puis à fusionner** |
| **0b** | **Les gardes qu'on croit avoir** : §5.5, les hooks du framework `pre-commit` ne sont installés nulle part — `detect-secrets` n'a **jamais** tourné sur un dépôt dont le `.env` porte les mots de passe MinIO et Postgres. Entraîne §5.4 | un garde-fou de secrets qui existe vraiment, et un `make all` qui contrôle l'arbre au lieu de le muter | à faire, court |
| **1** | **Observer sans corriger.** Reconstruire l'image Docling, ingérer 1 chapitre par ouvrage + 5 pages du PDF. Trois questions : profondeur réelle du graphe (§3.2), `minio_url` présent sur les images HTML (§3.5), troncature réelle à l'embedding (§3.4) | contrainte **6** — la décision « corriger la hiérarchie avant ou après l'ingestion complète » | à faire |
| **2** | La hiérarchie des titres, **si et seulement si** le lot 1 la montre plate : §3.2, §3.3, §4.11, §4.12. Impose une purge du space | contrainte **6**, donc toute l'ablation | conditionnel |
| **3** | Instruments et gardes : §3.4, §4.4 (dont la **monotonie de `sequence`**, exigence 4), §4.14, §4.5 | la confiance dans tout chiffre produit après l'ingestion | à faire **avant** l'ingestion complète |
| **4** | La perte silencieuse : §4.1, §4.2, §4.6, §4.7, §4.3, §4.10, §5.6 | la certitude que le corpus ingéré est le corpus complet | à faire |
| **5** | Code mort et documentation contre code : §5.1 à §5.5, tout le §6 | la lisibilité, et l'arrêt des faux réglages | à faire |
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

**Distribuer l'audit indépendant du lot 0.** Le prompt est prêt, en annexe A de
ce fichier : le coller tel quel dans une conversation neuve nommée
`AUDIT-LOT-0`.

Puis, dans l'ordre :

1. lire son rapport ;
2. lire le diff `main..lot-0` toi-même ;
3. faire tourner `make all` de tes mains ;
4. **alors seulement**, trancher la fusion ;
5. si fusion : `git merge --ff-only lot-0` depuis `main`, puis `git push`, puis
   **supprimer `lot-0`** — local et distant. Une branche fusionnée ne reste pas ;
6. mettre le registre à jour — §8 « Traité », et §2 pour la mesure d'après-fusion ;
7. écrire le prompt du lot 1.

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

Traite tes propres affirmations comme des hypothèses. Vérifie avant d'écrire un
chiffre. Relis le code avant d'affirmer ce qu'il fait.

---

# Annexe A — Prompt prêt à distribuer : audit indépendant du lot 0

> **Routage : ceci va à une conversation NEUVE, à nommer `AUDIT-LOT-0`. Rien
> d'autre à envoyer, à personne.**

```
Tu es l'auditeur indépendant du LOT 0 sur le dépôt rag-ingestion-pipeline.

Tu es éligible pour une raison précise, et il faut que tu la saches : tu n'as
écrit AUCUNE ligne de ce que tu vas auditer. C'est cela, et rien d'autre, qui
rend ton verdict utile. Le développeur de ce lot est une autre conversation ;
tu ne dialogues pas avec lui, tu ne reprends aucune de ses conclusions sans
l'avoir refaite toi-même.

Dépôt : /home/florianhorellou/Projets/rag-ingestion-pipeline
À auditer : lot-0 (pointe 390ce8a, 8 commits)
Base : main = 77d4f5b, avance rapide possible.
Base de comparaison, conservée comme tag : reference/lot-0-avant-reparation (832c566)

LIS D'ABORD, EN ENTIER, sur main :
  documentation/pilotage_du_chantier.md   (le mandat, l'état, les règles)
  documentation/axes_amelioration.md      (le registre : le contrat en tête)

Installe le hook d'identité avant tout commit, si tu en fais un :
  cp scripts/git-hooks/pre-commit .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
  git config user.name "floSa" && git config user.email "florian.horellou@gmail.com"

═══════════════════════════════════════════════════════════════
CE QUE LE LOT PRÉTEND AVOIR FAIT
═══════════════════════════════════════════════════════════════

0.1  Déclarer les dépendances de développement dans pyproject.toml
     (groupe [dependency-groups] dev), supprimer requirements-dev.txt,
     faire passer chaque outil du Makefile derrière `uv run`.
0.2  Corriger deux erreurs mypy (réexport implicite de get_embedding_model
     via vectors.py) À LA SOURCE, et replier la correction dans le commit
     qui introduisait l'erreur.
0.3  Déplacer POST /reindex de « une fois par document » à « une fois par
     rafale d'ingestion », via un asset, un job et un sensor dédiés
     (src/pipeline/reindex_job.py, 236 lignes neuves).
0.4  Remesurer le compte de tests (508) et lui donner un site canonique.

Il annonce : make all vert sur les 8 commits individuellement, 26 graines de
hachage vertes sur chacun, et 13 mutations vérifiées.

═══════════════════════════════════════════════════════════════
CE QUE LE PILOTE A DÉJÀ VÉRIFIÉ — NE LE REFAIS PAS
═══════════════════════════════════════════════════════════════

Mesuré par le pilote sur la pointe 390ce8a, le 28 août 2026 :
  - avance rapide possible sur main ;
  - aucune mention de Claude / Anthropic / Copilot / ChatGPT dans les
    messages de commit ni dans le diff ; aucun trailer Co-Authored-By ;
  - auteurs dans la liste blanche ;
  - après rm -rf .venv && uv sync : pytest, ruff, mypy, pip-audit et
    pre-commit sont installés ;
  - ruff check src/ propre, mypy src/ « no issues found in 36 source
    files », 508 tests verts ;
  - ruff format --check src/ : 3 fichiers seraient reformatés
    (extraction.py, language.py, matter.py) — constat §5.4 du registre,
    antérieur à ce lot.

Ton travail commence là où celui-ci s'arrête.

═══════════════════════════════════════════════════════════════
TON MANDAT
═══════════════════════════════════════════════════════════════

Audite non seulement le code, mais l'algorithme, sa logique et son
comportement. Six angles, et tu es libre d'en ouvrir d'autres.

── A. Le replantage des cinq commits d'origine ────────────────

Les 5 commits d'origine, reperables par le tag
reference/lot-0-avant-reparation, ont ete
replantés sur la nouvelle branche, avec une correction repliée dans le
premier. Quelque chose a-t-il été perdu, ajouté ou altéré au passage ?

`git range-diff` est l'outil, mais ne t'y arrête pas : compare aussi les
arbres. Le pilote a mesuré 477 tests sur l'origine et 508 sur la branche
réparée. La différence est-elle entièrement expliquée par les tests
nouveaux de 0.3, ou un test a-t-il disparu ?

── B. reindex_job.py — le cœur du lot ─────────────────────────

236 lignes neuves qui décident QUAND l'agent est réindexé. Le mécanisme
annoncé : un sensor qui ne déclenche que si aucun run d'ingestion n'est en
vol ET qu'au moins un a réussi depuis la dernière réindexation, avec un
curseur sur le storage_id du dernier run réussi.

Les questions qui méritent une réponse démontrée, pas plausible :
  - que se passe-t-il si le run de réindexation lui-même échoue ? Le
    curseur a-t-il déjà avancé ? Perd-on la réindexation pour toujours ?
  - le sensor peut-il se retrouver en famine — une ingestion continue qui
    ne laisse jamais de fenêtre « rien en vol » ?
  - peut-il déclencher DEUX runs de réindexation concurrents ?
  - le curseur survit-il à un redémarrage du daemon Dagster ? à un reset
    du sensor ?
  - la garde « le job de réindexation ne se compte pas lui-même » tient-elle
    si le job est lancé à la main depuis l'interface ?
  - STATUTS_EN_COURS est dérivé par soustraction des statuts terminaux.
    Vérifie que la soustraction porte sur la BONNE énumération, et que la
    liste des terminaux est celle de la version de Dagster épinglée
    (1.13.16) — pas celle d'une autre.

── C. La table de mutations : construis la TIENNE ─────────────

Le développeur annonce 13 mutations. **N'audite pas sa liste.** Construis
la tienne, depuis les docstrings, la documentation et le comportement que
le lot prétend garantir, puis DIFFE. Ce que ta liste contient et la sienne
pas est le résultat qui compte.

Vérifie en particulier qu'aucun de ses tests n'est vert des DEUX CÔTÉS du
défaut qu'il prétend garder. Le développeur signale lui-même avoir dû
corriger un point d'injection intestable (`post: Callable = requests.post`
figeait l'objet fonction à l'import) : vérifie que la correction est
complète et qu'il n'existe pas d'autre montage qui bouchonne au-dessus de
ce qu'il vérifie.

── D. 0.1 : ce que la suppression de requirements-dev.txt a coûté

Quatre outils y étaient déclarés et n'ont pas été repris (httpx,
pytest-mock, pytest-asyncio, pydantic-settings). L'argument est « aucun
utilisateur ». Vérifie-le toi-même, sur tout le dépôt, y compris dans les
Dockerfiles, le compose, la CI si elle existe, et .pre-commit-config.yaml.

Vérifie aussi que le Makefile versionné fait bien ce que le lot dit qu'il
fait, cible par cible, y compris `audit` et `test-cov`.

── E. La documentation livrée dans le même commit que son code

Le lot touche README.md, documentation/orchestration.md, .env.example.
Applique la question la plus productive des deux dépôts : qu'est-ce que
cette documentation affirme que le code ne fait pas ? Et l'inverse : le
code fait-il quelque chose que la documentation ne dit pas ?

Cherche les phrases d'exhaustivité — une énumération close est un défaut en
attente.

── F. La conformité au contrat

Le lot prétend honorer l'exigence 5 du contrat (POST /reindex). Le
développeur a lui-même signalé un écart : il livre « une fois par rafale »
et non « une fois l'ingestion terminée ». Le pilote a accepté cet écart.

Ta question n'est donc pas « est-ce littéralement conforme » — c'est tranché
— mais : **l'écart est-il écrit là où quelqu'un le lira**, ou seulement dans
un rapport qui va disparaître ? Et le comportement livré tient-il les trois
propriétés que le contrat exige vraiment : un échec qui ne fait jamais
échouer une ingestion, un échec qui ne passe pas inaperçu, une URL vide qui
est un choix annoncé et non un oubli ?

═══════════════════════════════════════════════════════════════
CE QUE TU NE FAIS PAS
═══════════════════════════════════════════════════════════════

Tu n'écris pas de code de production. Tu peux écrire des tests jetables
pour DÉMONTRER un défaut — dis-le alors explicitement, et ne les commite
pas.

Tu ne fusionnes rien. La fusion est la décision du pilote.

Tu ne corriges pas ce que tu trouves : tu le rapportes, avec sa preuve
fichier:ligne, sa sévérité, et ce qu'il coûte de ne pas le corriger.

═══════════════════════════════════════════════════════════════
LES LEÇONS — APPLIQUE-LES, NE TE CONTENTE PAS DE LES LIRE
═══════════════════════════════════════════════════════════════

- Un test « ça marche » est vert DES DEUX CÔTÉS du défaut.
- Asserte depuis le côté qui PRODUIT le comportement.
- Une phrase d'exhaustivité est un défaut en attente.
- Un test qui choisit lui-même son cas doit PROUVER qu'il l'a atteint.
- Vérifie tes bornes de balayage.
- Une généralisation tirée d'UNE branche d'une fonction qui en a plusieurs
  est fausse jusqu'à preuve du contraire.
- Un montage qui bouchonne trop haut rend intestable ce qu'il vérifie.
- Deux erreurs qui se compensent se cachent mutuellement.
- Tester un point d'entrée demande un SOUS-PROCESSUS, pas un import.
- Lis le code avec `git show <branche>:<fichier>`, pas avec `cat` : sur ce
  dépôt, un arbre de travail a porté du contenu périmé sur un HEAD récent.
- Traite tes propres affirmations comme des hypothèses. Ne recopie aucun
  chiffre sans l'avoir produit toi-même.

═══════════════════════════════════════════════════════════════
CE QUE TU RENDS
═══════════════════════════════════════════════════════════════

1. Un verdict clair en tête : FUSIONNABLE / FUSIONNABLE SOUS RÉSERVE DE …
   / NON FUSIONNABLE — et la raison en une phrase.
2. Chaque constat avec sa preuve fichier:ligne, sa sévérité, et son coût.
3. Ta table de couverture, construite par toi, et son diff avec celle du
   développeur. Dis explicitement ce que ta liste contient et la sienne
   pas.
4. Les mesures que tu as produites toi-même, avec les commandes.
5. Ce que tu as cherché et NON trouvé — c'est une information, pas un vide.
6. Tes désaccords avec le pilote, s'il y en a. Ils sont attendus, pas
   tolérés : le pilote a été renversé plusieurs fois, chaque fois à raison.

Ta dernière ligne est exactement `TÂCHE TERMINÉE`, ou
`TÂCHE BLOQUÉE — <raison>`.
```
