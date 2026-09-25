"""Les hooks ne doivent pas toucher au corpus versionne.

`Datas/htms/` et `Datas/pdfs/` sont versionnes (commit `a005172`) : 25
fichiers, 55 Mo de HTML capture et un PDF. Sans exclusion, quatre hooks
posaient probleme sur le corpus (``mesure`` sur une fusion d'essai) :

1. `detect-secrets` refuse un commit touchant deux fichiers du corpus (deux
   ``Hex High Entropy String``, faux positifs, dans
   ``Datas/htms/MLOps with Databricks/3. MLflow for Traditional ML.html:94`` et
   ``…/4. Model Serving： …html:330``). Aucun pragma n'y est possible : le
   contenu du fichier entre dans le calcul de ``element_id`` (contrat,
   exigences 2 et 3), et un commentaire changerait les identifiants ;
2. `trailing-whitespace` et `end-of-file-fixer` reecrivent 240 lignes dans 24
   fichiers sur 25. Un `git add` suivi d'un nouveau commit ferait entrer le
   fichier altere sans erreur, et le contenu entre dans `element_id` : c'est
   le cas le plus grave ;
3. `check-added-large-files --maxkb=500` refuse un nouveau fichier du corpus
   (un chapitre de 661 ko, `rc=1`) : le corpus ne pourrait plus etre etendu.

La correction est un `exclude` a la racine de `.pre-commit-config.yaml`, et non
par hook : `pre-commit` applique `files`/`exclude` de la racine avant de
distribuer les fichiers aux hooks. Un seul motif couvre donc les hooks actuels
et futurs.

Ce fichier reproduit le filtrage de `pre-commit` (un ``re.search`` du motif sur
le chemin) et verifie le motif livre sur des chemins representatifs. Il ne fait
pas tourner les hooks, ce qui demanderait le corpus et le reseau. Il detecte la
disparition ou l'affaiblissement du motif : ``^Datas`` sans barre oblique
finale exclurait aussi ``Datastore/``, et ``Datas/`` sans ancre exclurait
``src/Datas/``.

Il y a deux cles a la racine. `pre-commit` filtre par ``files`` puis par
``exclude`` : un fichier est vu par les hooks si ``re.search(files, chemin)``
est vrai et ``re.search(exclude, chemin)`` est faux. ``files`` vaut ``''`` par
defaut, ce qui correspond a tout. Les tests couvrent donc aussi :

- ``files: '^Datas/'`` a la racine, qui ne laisserait aucun fichier aux hooks
  (« no files to check » sur chacun) ;
- ``exclude: '^Datas/|^scripts/'``, qui soustrairait aux hooks
  ``scripts/git-hooks/pre-commit`` (le controle d'identite) et
  ``scripts/installer-les-garde-fous.sh``.
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

# Ce que l'exclusion ne doit pas emporter. Sans cette liste, un motif trop large
# (`.` par exemple) ferait passer ce fichier tout en retirant tous les fichiers
# aux hooks.
CHEMINS_A_GARDER_SOUS_CONTROLE = [
    "src/pipeline/factory.py",
    "src/docling_service/extraction.py",
    "tests/unit/test_hooks_contre_le_corpus.py",
    "README.md",
    "documentation/pilotage_du_chantier.md",
    ".pre-commit-config.yaml",
    "docker-compose.yml",
    # Les deux scripts dont depend l'installation des hooks. Sans eux, une
    # exclusion elargie a `^scripts/` soustrairait le controle d'identite aux
    # hooks sans qu'aucun test n'echoue.
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

    `pre-commit` applique `files` avant `exclude`. Un `files` pose a la racine
    reduit la liste distribuee a tous les hooks : un motif etroit les rendrait
    tous inactifs, sans changer l'`exclude`.
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
        # Le point 3 : sans exclusion, `check-added-large-files` refuse tout
        # nouveau fichier au-dela de 500 ko, et le corpus ne peut plus grandir.
        # Teste a part : ce n'est pas une alteration, mais un refus.
        exclusion = _exclusion_racine()
        assert re.search(exclusion, "Datas/htms/MLOps with Databricks/11. Un chapitre de plus.html")
        assert re.search(exclusion, "Datas/pdfs/Un_ouvrage_de_plus.pdf")

    def test_le_reste_du_depot_reste_sous_controle(self):
        """Les fichiers hors corpus restent vus par les hooks.

        Le test modelise les deux cles de la racine, car deux modifications
        rendraient les hooks inactifs sans toucher a l'`exclude` livre :

        - `files: '^Datas/'` a la racine ne laisserait aux hooks que le corpus,
          ensuite exclu : plus aucun fichier (`mesure` le 31 aout 2026 : un
          `.py` volontairement sale commite en `rc=0`) ;
        - `exclude: '^Datas/|^scripts/'` soustrairait
          `scripts/git-hooks/pre-commit` (le controle d'identite) et
          l'installeur.

        Les deux derniers chemins de la liste testent l'ancrage : `^Datas`
        emporterait `Datastore/`, `Datas/` non ancre emporterait `src/Datas/`.
        """
        inclusion, exclusion = _inclusion_racine(), _exclusion_racine()
        hors_de_portee = [
            chemin for chemin in CHEMINS_A_GARDER_SOUS_CONTROLE if not _sous_controle(chemin)
        ]
        assert not hors_de_portee, (
            f"les motifs de la racine — files « {inclusion} », exclude « {exclusion} » — "
            f"soustraient ces chemins a la porte : {hors_de_portee}"
        )
