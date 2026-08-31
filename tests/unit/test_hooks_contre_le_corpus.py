"""Les hooks ne doivent pas toucher au corpus versionne.

Depuis `a005172` — « data: versionner le corpus, il fait partie de l'identite du
projet » — `Datas/htms/` et `Datas/pdfs/` sont dans le depot : 25 fichiers,
55 Mo de HTML capture et un PDF (``mesure`` sur le resultat d'une fusion d'essai
`--no-ff` avec `a005172`, 0 conflit). La porte qualite et le corpus se sont
rencontres a ce moment-la, et le corpus a perdu trois fois :

1. `detect-secrets` REFUSE un commit touchant deux fichiers du corpus — deux
   ``Hex High Entropy String``, faux positifs, dans
   ``Datas/htms/MLOps with Databricks/3. MLflow for Traditional ML.html:94`` et
   ``…/4. Model Serving： …html:330`` (``mesure``). **On ne peut pas y poser de
   pragma** : le contenu du fichier entre dans le calcul de ``element_id``
   (contrat, exigences 2 et 3), donc y ajouter un commentaire change les
   identifiants de tout ce qui en sort ;
2. `trailing-whitespace` et `end-of-file-fixer` ECRIVENT dans le corpus —
   24 fichiers sur 25, 240 lignes reecrites (``mesure`` : 216 pour le premier,
   24 pour le second). Et au commit, le geste naturel — `git add` puis recommit
   — fait entrer le fichier ALTERE, au-dela de ce que l'humain a ecrit, sans
   aucune erreur. C'est le sinistre du mandat §2.2 applique au contenu au lieu du
   nom, et c'est le plus grave des trois : `source_path` ET le contenu entrent
   dans `element_id` ;
3. `check-added-large-files --maxkb=500` laisse passer un fichier du corpus
   DEJA SUIVI qu'on modifie, et REFUSE un fichier NOUVEAU (``mesure`` : un
   chapitre de 661 ko, `rc=1`). Le corpus ne pouvait donc plus etre ETENDU.

**La reparation est un `exclude` au niveau RACINE**, et non un `exclude` par
hook. `pre-commit` applique les motifs `files`/`exclude` de la racine a la liste
de fichiers AVANT de la distribuer aux hooks : un seul site couvre donc les
quatre hooks fautifs, et aussi tous ceux qu'on ajoutera. Un `exclude` par hook
aurait demande de se souvenir de le reporter sur le hook suivant — un garde-fou
qui repose sur la memoire du suivant n'est pas un garde-fou.

CE QUE CE FICHIER TESTE, ET CE QU'IL NE TESTE PAS. Il reproduit le filtrage de
`pre-commit`, qui est un ``re.search`` du motif sur le chemin, et il asserte le
motif LIVRE contre des chemins representatifs. Il ne fait pas tourner les hooks :
cela demanderait le corpus — absent d'un arbre de travail qui n'a pas encore
fusionne `main` — et l'installation d'environnements de hook, donc le reseau. La
mesure de bout en bout est prise a la main sur le resultat de la fusion d'essai
et consignee au registre.

Ce qu'il attrape, en revanche, est le defaut reel : la disparition du motif, ou
son affaiblissement. ``^Datas`` sans barre oblique finale, par exemple, exclurait
aussi un futur ``Datastore/`` — et ``Datas/`` sans ancre exclurait
``src/Datas/``.

IL Y A DEUX CLES A LA RACINE, PAS UNE. `pre-commit` filtre la liste de fichiers
par ``files`` PUIS par ``exclude`` : un fichier est vu par les hooks si
``re.search(files, chemin)`` est vrai ET ``re.search(exclude, chemin)`` est faux.
``files`` vaut ``''`` par defaut, ce qui matche tout. Ce fichier n'en lisait
qu'une, ``exclude``, et deux mutations lui survivaient (``mesure`` le 31 aout
2026, suite entiere verte a 550 dans les deux cas) :

- ajouter ``files: '^Datas/'`` a la RACINE desarme les SEPT hooks — « no files to
  check » sur chacun — et fait passer en ``rc=0`` un commit portant un ``.py``
  volontairement sale. La porte entiere disparait, et l'``exclude`` livre, lui,
  ne bouge pas : les trois tests restaient verts ;
- elargir l'exclusion a ``'^Datas/|^scripts/'`` soustrait a TOUS les hooks
  ``scripts/git-hooks/pre-commit`` — le controle d'identite lui-meme — et
  ``scripts/installer-les-garde-fous.sh``. La liste temoin ne couvrait aucun
  chemin sous ``scripts/``.

Les deux sont fermees ici : le temoin modelise les DEUX cles, et il couvre les
deux scripts dont depend tout le montage.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

RACINE = Path(__file__).resolve().parents[2]
CONFIG = RACINE / ".pre-commit-config.yaml"

# Des chemins du corpus tel qu'il est versionne depuis `a005172`, plus les deux
# extensions qu'il faut pouvoir faire : un chapitre de plus, un PDF de plus.
CHEMINS_DU_CORPUS = [
    "Datas/htms/MLOps with Databricks/3. MLflow for Traditional ML.html",
    "Datas/htms/MLOps with Databricks/4. Model Serving： Architectures and Implementation.html",
    "Datas/htms/Practical MLflow for Generative AI on Databricks/Preface.html",
    "Datas/pdfs/Hands-On_RAG_for_Production_ER_-_Ofer_Mendelevitch.pdf",
    "Datas/htms/MLOps with Databricks/11. Un chapitre de plus.html",
    "Datas/pdfs/Un_ouvrage_de_plus.pdf",
]

# Ce que l'exclusion ne doit PAS emporter. Sans cette liste, un motif trop large
# — `.` par exemple — rendrait tout ce fichier vert en desarmant la porte
# entiere : les tests seraient verts des deux cotes du defaut.
CHEMINS_A_GARDER_SOUS_CONTROLE = [
    "src/pipeline/factory.py",
    "src/docling_service/extraction.py",
    "tests/unit/test_hooks_contre_le_corpus.py",
    "README.md",
    "documentation/pilotage_du_chantier.md",
    ".pre-commit-config.yaml",
    "docker-compose.yml",
    # Les deux scripts dont depend TOUT le montage des garde-fous. Sans eux,
    # une exclusion elargie a `^scripts/` soustrairait le controle d'identite
    # lui-meme a la porte, en restant verte ici.
    "scripts/git-hooks/pre-commit",
    "scripts/installer-les-garde-fous.sh",
    # Les deux pieges d'un motif mal ancre ou mal termine.
    "Datastore/settings.py",
    "src/Datas/faux_corpus.py",
]


def _exclusion_racine() -> str:
    """Le motif `exclude` de la racine de `.pre-commit-config.yaml`."""
    config = yaml.safe_load(CONFIG.read_text())
    exclusion = config.get("exclude")
    assert exclusion, (
        "`.pre-commit-config.yaml` ne porte plus d'`exclude` a la racine : "
        "les hooks reecrivent le corpus versionne et refusent de l'etendre"
    )
    return str(exclusion)


def _inclusion_racine() -> str:
    """Le motif `files` de la racine, `''` par defaut — qui matche tout.

    `pre-commit` applique `files` AVANT `exclude`. Un `files` pose a la racine
    reduit donc la liste distribuee a TOUS les hooks, et un motif etroit les
    desarme tous d'un coup, sans toucher a l'`exclude` que ce fichier garde.
    """
    config = yaml.safe_load(CONFIG.read_text())
    return str(config.get("files", ""))


def _sous_controle(chemin: str) -> bool:
    """Reproduit le filtrage de `pre-commit` : `files` d'abord, `exclude` ensuite.

    C'est un `re.search` dans les deux cas, et non un `re.match`.
    """
    return bool(re.search(_inclusion_racine(), chemin)) and not re.search(
        _exclusion_racine(), chemin
    )


class TestLeCorpusEstHorsDePorteeDesHooks:
    def test_chaque_fichier_du_corpus_est_exclu(self):
        exclusion = _exclusion_racine()
        # `re.search`, et non `re.match` : c'est ce que `pre-commit` applique.
        non_exclus = [chemin for chemin in CHEMINS_DU_CORPUS if not re.search(exclusion, chemin)]
        assert not non_exclus, (
            f"le motif « {exclusion} » n'exclut pas ces chemins du corpus, "
            f"donc les hooks y ecrivent ou les refusent : {non_exclus}"
        )

    def test_un_chapitre_neuf_peut_entrer(self):
        # Le cas du point 3 : `check-added-large-files` refusait tout fichier
        # NOUVEAU au-dela de 500 ko, donc le corpus ne pouvait plus grandir. Ce
        # test le nomme a part parce que la consequence est differente des deux
        # autres — ce n'etait pas une alteration, c'etait une impossibilite.
        exclusion = _exclusion_racine()
        assert re.search(exclusion, "Datas/htms/MLOps with Databricks/11. Un chapitre de plus.html")
        assert re.search(exclusion, "Datas/pdfs/Un_ouvrage_de_plus.pdf")

    def test_le_reste_du_depot_reste_sous_controle(self):
        """Le temoin, sans lequel tout ce fichier serait vert sur une porte morte.

        Il modelise les DEUX cles de la racine, parce que deux mutations
        distinctes desarment la porte en laissant l'`exclude` livre intact :

        - un `files: '^Datas/'` a la racine ne laisse aux sept hooks que le
          corpus, qui est ensuite exclu — donc plus rien du tout. `mesure` le
          31 aout 2026 : « no files to check » sur les sept, un `.py`
          volontairement sale commite en `rc=0`, et 550 tests verts ;
        - un `exclude: '^Datas/|^scripts/'` soustrait `scripts/git-hooks/pre-commit`
          — le controle d'identite lui-meme — et l'installeur. `mesure` : 550
          tests verts.

        Les deux derniers chemins de la liste sont les pieges d'ancrage : un
        motif `^Datas` emporterait `Datastore/`, un motif `Datas/` non ancre
        emporterait `src/Datas/`.
        """
        inclusion, exclusion = _inclusion_racine(), _exclusion_racine()
        hors_de_portee = [
            chemin for chemin in CHEMINS_A_GARDER_SOUS_CONTROLE if not _sous_controle(chemin)
        ]
        assert not hors_de_portee, (
            f"les motifs de la racine — files « {inclusion} », exclude « {exclusion} » — "
            f"soustraient ces chemins a la porte : {hors_de_portee}"
        )
