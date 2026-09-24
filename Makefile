.PHONY: install lint format format-check typecheck test mutations test-cov audit all

# UN SEUL GESTE arme tout ce que ce depot sait garder, et c'est celui-ci.
#
# `uv sync` installe les dependances de production ET le groupe `dev` declare
# dans pyproject.toml : il n'y a pas de liste annexe a se rappeler. Puis
# `installer-les-garde-fous.sh` arme les hooks git — le controle d'identite
# d'auteur et les hooks de `.pre-commit-config.yaml` — et VERIFIE qu'ils le
# sont, en sortant en erreur sinon.
#
# La seconde ligne n'etait pas la avant la reparation du lot 0b, et cette cible
# annoncait pourtant « la seule etape d'installation de la porte qualite ». Les
# hooks demandaient un second geste, dans un ordre precis, connu de la seule
# documentation. Un garde-fou qui repose sur la memoire du suivant n'est pas un
# garde-fou : c'est la phrase que la cible `all` ci-dessous a fait respecter,
# et elle valait aussi pour cette cible-ci.
install:
	uv sync
	sh scripts/installer-les-garde-fous.sh

# Chaque outil passe par `uv run` : la porte tourne immediatement apres
# `make install`, sans activation d'environnement, et toujours aux versions
# epinglees par uv.lock. Un `ruff` ou un `mypy` trouve au hasard du PATH rendrait
# un verdict qui n'est celui d'aucune version declaree.
# La portee est `src/ tests/`, et elle etait bornee a `src/`. C'EST LE MEME ANGLE
# MORT QUE D7, sur `lint` au lieu de `format-check` : le hook `ruff` voit tout ce
# qui est INDEXE, donc `tests/`, tandis que cette cible ne voyait que `src/`.
# `make all` rendait 0 sur un arbre dont le hook refusait le commit, et le
# message d'echec arrivait au moment du commit, pas au moment du controle. Mesure
# le 1er septembre 2026 : deux commits du lot 4 ont ete refuses pour des regles
# (N802, SIM223, E402, I001) que `make all` venait de declarer propres.
#
# Les deux gardes voient desormais la meme chose, comme `format` et
# `format-check` depuis la reparation du lot 3. `typecheck` reste borne : c'est
# `pyproject.toml` qui exclut `tests/` de `mypy`, un choix declare et non une
# divergence de portee.
#
# `scripts/` Y EST DEPUIS LA TROISIEME REPRISE DU LOT 11, et c'est une MESURE qui
# le decide : `scripts/rejouer-les-mutations.py` etait EXECUTE par `make all`
# sans etre controle par `make lint` ni par `make typecheck` — un script que la
# porte lance et qu'elle ne regarde pas. `mesure` le 24 septembre 2026 : ajouter
# TOUT `scripts/` ne rougit RIEN, ni `ruff check` (6 fichiers) ni `mypy`
# (40 fichiers -> 42). Le hook `ruff` voit deja tout ce qui est indexe, donc
# `scripts/` : les deux gardes cessent de diverger, comme pour `tests/`.
lint:
	uv run ruff check src/ tests/ scripts/

# `format` ECRIT dans le depot. C'est le geste volontaire du developpeur qui
# decide de reformater, et il n'entre dans aucune porte.
#
# La portee est `src/ tests/`, et elle etait bornee a `src/`. C'etait un ANGLE
# MORT, nomme au registre (D7) : `tests/unit/test_wipe_stores.py` n'etait pas
# format-propre, `make format-check` ne le voyait jamais, `make format` ne le
# reparait pas — mais le hook `ruff-format --check`, qui voit tout ce qui est
# indexe, BLOQUAIT tout commit qui le touchait, sans issue automatique. Les deux
# portees divergeaient, et c'est la divergence qui etait le defaut.
format:
	uv run ruff format src/ tests/ scripts/

# `scripts/` EST DANS LA PORTEE DE CES DEUX CIBLES depuis la quatrieme reprise du
# lot 11, et c'est le MEME DEFAUT D7 qu'on referme une troisieme fois. `lint` et
# `typecheck` l'avaient pris au point 4a ; `format` et `format-check` etaient
# restes en arriere, donc les trois gardes de la porte ne voyaient plus la meme
# chose. `mesure` le 24 septembre 2026, rc du PROCESSUS et non derriere un tube,
# sur un `scripts/mal-forme-temoin.py` portant `x   =   1` :
# `ruff format --check src/ tests/` rend **0**, `ruff format --check src/ tests/
# scripts/` rend **1**. `make format-check` etait donc vert sur un arbre dont le
# hook `ruff-format --check` — qui voit tout ce qui est INDEXE, donc `scripts/` —
# refusait le commit, et sans issue automatique puisque `make format` ne
# reparait pas ce qu'il ne regarde pas. C'est mot pour mot D7, sur un troisieme
# couple de cibles. `mesure`, meme jour : ajouter `scripts/` ne rougit rien —
# 84 fichiers deja formates.

# `format-check` ne fait que CONSTATER, et c'est lui qui entre dans `all`. Une
# porte qualite qui reecrit l'arbre qu'elle controle ne controle rien : elle
# rend vrai ce qu'elle allait verifier. Avant cette separation, `make all`
# reecrivait trois fichiers a chaque execution, et chaque developpeur devait se
# souvenir de les revoquer avant chaque commit — celui de la reparation du lot 0
# l'a fait six fois parce qu'il le savait. Un garde-fou qui repose sur la
# memoire du suivant n'est pas un garde-fou.
format-check:
	uv run ruff format --check src/ tests/ scripts/

# `scripts/campagne/` EST DANS LA PORTEE depuis le lot 11. Le harnais
# d'equivalence y portait quatre `type: ignore[assignment]` injustifies, que
# `mypy --strict --warn-unused-ignores` disait tous inutilises : hors de portee,
# personne ne le voyait. `mesure` le 24 septembre 2026 : inclure le dossier ne
# rougit AUCUN des trois scripts, et une erreur deposee dans chacun est vue.
# La portee est passee a TOUT `scripts/` a la troisieme reprise, pour la raison
# ecrite a `lint` : le rejeu des mutations etait execute par `make all` sans
# etre controle par elle.
typecheck:
	uv run mypy src/ scripts/

# N'AJOUTE JAMAIS `-q` A CETTE COMMANDE, ni en ligne de commande. `pyproject.toml`
# porte deja `addopts = "-q --tb=short"` : un second `-q` donne `-qq`, qui
# SUPPRIME la ligne `N passed` en laissant `rc=0`. La borne est etroite et c'est
# elle qui rend le piege vicieux — `mesure` le 22 septembre 2026, pytest 9.1.1 :
# sur un couple vert + rouge, `-qq` imprime toujours `1 failed, 1 passed` et son
# `FAILURES`. Le compte ne disparait QUE quand tout passe, c'est-a-dire
# exactement dans le cas ou il est la seule chose a lire. Un verificateur de ce
# chantier a produit cinq balayages de graines « verts » sans aucun compte avant
# de s'en apercevoir (registre 4.27 n° 5).
#
# Ce que le compte garde, et que le `rc` ne garde pas : un fichier de tests qui
# cesse d'etre COLLECTE — renomme hors de `test_*.py`, deplace hors de
# `testpaths` — retire ses tests sans un seul rouge. `mesure`, meme jour :
# `test_wipe_stores.py` renomme fait passer la suite de 925 a 875, `rc=0`.
test:
	uv run pytest tests/

# LE REJEU DES MUTATIONS, et il entre dans la porte a l'issue d'une MESURE.
#
# LE CHIFFRE N'EST PAS ICI, ET C'EST VOULU : ce qu'il en coute a la porte est
# mesure, date et cite a UN SEUL SITE, le registre 4.39.e. Il vivait aussi ici,
# et les deux sites avaient deja diverge — « 34,2 s » au registre pour une
# somme qui en faisait 35,4. Un nombre a deux sites finit par mentir a l'un des
# deux ; c'est le motif du lot 5, applique a ce fichier-ci.
#
# Le defaut nomme au 4.35.e etait que les mutations annoncees n'etaient pas
# rejouables. Le prix est tenu pour acceptable au regard de ce qu'il achete, et
# cette cible est separee, donc retirable d'une ligne si l'avis change.
#
# IL N'ECRIT PLUS RIEN DANS L'ARBRE DE TRAVAIL : il rejoue sur une COPIE
# JETABLE, et le verifie lui-meme par les `mtime` de `src/` et `tests/`. La
# version precedente mutait les fichiers de production en place (registre
# 4.39.c).
mutations:
	uv run python scripts/rejouer-les-mutations.py

test-cov:
	uv run pytest tests/ --cov=src --cov-report=term-missing

audit:
	uv run pip-audit -r requirements.txt -r src/docling_service/requirements.txt

# `format-check` passe EN DERNIER, et l'ordre a ete choisi quand il etait ROUGE :
# les quatre fichiers plies a la main faisaient sortir `make all` en 2 sur
# `main`, et le placer en premier aurait prive tous les lots a venir du signal de
# `lint`, `typecheck` et `test`. Ces quatre fichiers sont desormais
# format-propres (registre 5.4), donc `make all` rend 0 et l'exception « rc=2 est
# le rouge attendu » n'existe plus : un rc non nul est un defaut, sans exception
# a connaitre. L'ordre est conserve — il ne coute rien et il redeviendrait le bon
# le jour ou un fichier repart de travers.
all: lint typecheck test mutations format-check
