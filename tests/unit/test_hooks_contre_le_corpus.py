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

    def test_l_exclusion_n_emporte_rien_d_autre(self):
        """Le temoin, sans lequel tout ce fichier serait vert sur un motif large.

        Un `exclude` trop permissif desarmerait la porte entiere en restant vert
        ici : c'est la definition d'un test vert des deux cotes du defaut. Les
        deux derniers chemins sont les pieges d'ancrage — un motif `^Datas`
        emporterait `Datastore/`, un motif `Datas/` non ancre emporterait
        `src/Datas/`.
        """
        exclusion = _exclusion_racine()
        emportes = [
            chemin for chemin in CHEMINS_A_GARDER_SOUS_CONTROLE if re.search(exclusion, chemin)
        ]
        assert not emportes, (
            f"le motif « {exclusion} » soustrait ces chemins a la porte : {emportes}"
        )
