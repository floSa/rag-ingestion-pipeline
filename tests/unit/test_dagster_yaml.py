"""Le delai de garde de l'orchestrateur, et le fichier qui le porte.

Registre 4.15 a 4.17 : un run coince en `STARTED` bloque la reindexation
INDEFINIMENT, sans delai de garde ni alerte, et le *run monitoring* de Dagster
etait **absent de `dagster.yaml`**. C'est la que la famille entiere se ferme d'un
geste, plutot qu'au cas par cas dans chaque sensor.

**Ce fichier n'est pas un test de texte, et la distinction compte.** Le registre
laisse ouvert (F7) le fait qu'aucun test de ce depot ne lit le `Makefile`, en
notant qu'une assertion sur du texte « resterait verte si le script etait renomme,
deplace ou rendu non executable ». Ici, la configuration EST lue par Dagster et
par personne d'autre : la valider avec le processeur de configuration de Dagster
lui-meme, et comparer ses seuils aux reglages reels du pipeline, eprouve donc le
comportement et non une chaine. Un `dagster.yaml` invalide fait echouer le
demarrage du daemon — ce test le dit avant.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from dagster._config import process_config
from dagster._core.instance.config import dagster_instance_config_schema

from src.pipeline.settings import PipelineSettings

RACINE = Path(__file__).resolve().parents[2]
CHEMIN = RACINE / "dagster.yaml"


def _reference_de_l_instance(chemin: Path) -> Any:
    """Resout ce que Dagster lit du `dagster.yaml` livre, sans rien ouvrir.

    `InstanceRef.from_dir` rend des `ConfigurableClassData` — du descriptif, pas
    des objets : rien n'est instancie, donc aucun des trois stores Postgres que
    ce fichier declare n'est joint. C'est ce qui rend le controle utilisable dans
    une suite qui ne sort jamais du disque.

    Le fichier est COPIE dans un repertoire jetable : `from_dir` prend son
    argument pour la racine des artefacts locaux et peut y ecrire.
    """
    import shutil
    import tempfile

    from dagster._core.instance.ref import InstanceRef

    with tempfile.TemporaryDirectory() as bac:
        shutil.copy2(chemin, Path(bac) / "dagster.yaml")
        return InstanceRef.from_dir(bac)


@pytest.fixture(scope="module")
def configuration() -> dict[str, Any]:
    """Le `dagster.yaml` livre, tel que Dagster le lit."""
    charge = yaml.safe_load(CHEMIN.read_text(encoding="utf-8"))
    assert isinstance(charge, dict), f"{CHEMIN} ne contient pas un mapping"
    return charge


class TestLaConfigurationEstCELLEQueDagsterAccepte:
    """Un `dagster.yaml` invalide empeche le daemon de demarrer."""

    def test_le_fichier_passe_le_schema_du_dagster_epingle(
        self, configuration: dict[str, Any]
    ) -> None:
        resultat = process_config(dagster_instance_config_schema(), configuration)

        assert resultat.success, [erreur.message for erreur in resultat.errors or []]

    def test_une_cle_inconnue_serait_refusee(self, configuration: dict[str, Any]) -> None:
        """LE TEMOIN. Sans lui, un schema permissif rendrait le test ci-dessus
        vrai de n'importe quel fichier, et l'assertion serait creuse."""
        resultat = process_config(
            dagster_instance_config_schema(), {**configuration, "reglage_inexistant": 1}
        )

        assert not resultat.success


class TestLeDelaiDeGardeEstArme:
    """Le run monitoring etait ABSENT : rien ne reprenait un run orphelin."""

    def test_le_run_monitoring_est_active(self, configuration: dict[str, Any]) -> None:
        assert configuration.get("run_monitoring", {}).get("enabled") is True, (
            "sans run monitoring, un run coince en STARTED bloque la "
            "reindexation indefiniment (registre 4.15)"
        )

    def test_un_run_qui_ne_demarre_pas_a_une_borne(self, configuration: dict[str, Any]) -> None:
        borne = configuration["run_monitoring"].get("start_timeout_seconds")

        assert isinstance(borne, int) and borne > 0, (
            "un run que le launcher n'arrive jamais a demarrer doit finir par "
            "echouer, sans quoi il gele la reindexation pour toujours"
        )

    def test_un_run_qui_ne_finit_pas_a_une_borne(self, configuration: dict[str, Any]) -> None:
        borne = configuration["run_monitoring"].get("max_runtime_seconds")

        assert isinstance(borne, int) and borne > 0

    def test_la_borne_de_duree_ne_contredit_pas_le_plafond_du_pipeline(
        self, configuration: dict[str, Any]
    ) -> None:
        """LE GARDE QUI COMPTE, et il compare deux fichiers.

        `PipelineSettings.extraction_timeout_seconds` est le plafond que le
        pipeline s'accorde LUI-MEME par document. Un `max_runtime_seconds` plus
        court tuerait des runs que le pipeline considere encore legitimes, et le
        developpeur chercherait la cause du mauvais cote — deux plafonds qui se
        contredisent sont pires qu'un seul.

        Ce delai-ci est la DERNIERE ligne : il ne se declenche que quand le
        plafond du pipeline a lui-meme echoue a se declencher, c'est-a-dire quand
        le run est reellement gele et non lent.

        C'est aussi le seul garde du depot qui rougirait si l'un des deux
        reglages bougeait sans l'autre.
        """
        borne = configuration["run_monitoring"]["max_runtime_seconds"]
        plafond_du_pipeline = PipelineSettings().extraction_timeout_seconds

        assert borne > plafond_du_pipeline, (
            f"max_runtime_seconds={borne} est sous le plafond que le pipeline "
            f"s'accorde par document ({plafond_du_pipeline} s) : des runs "
            "legitimes seraient tues, et la cause serait cherchee ailleurs"
        )

    def test_la_reprise_n_est_pas_armee_pour_un_launcher_qui_ne_sait_pas_reprendre(
        self, configuration: dict[str, Any]
    ) -> None:
        """`DefaultRunLauncher` ne reprend pas un run dont le worker est mort.

        L'armer donnerait un reglage qui ne fait rien — la famille des
        `CHUNK_SIZE=900` du registre 5.1, dont le debat entier etait vide parce
        que la variable etait morte. Un run mort est marque en ECHEC, et c'est ce
        qui libere la reindexation.
        """
        assert configuration["run_monitoring"].get("max_resume_run_attempts") == 0

    def test_le_launcher_est_bien_celui_que_ce_raisonnement_suppose(self) -> None:
        """LE TEMOIN du precedent, et il porte son antecedent.

        Le raisonnement ci-dessus ne vaut que pour `DefaultRunLauncher`. Le jour
        ou ce depot passe a un launcher qui SAIT reprendre — `K8sRunLauncher`,
        `DockerRunLauncher` — `max_resume_run_attempts: 0` devient un mauvais
        reglage, et ce test est ce qui le rappellera. *Cherche l'antecedent avant
        d'auditer le raisonnement.*

        **CETTE ASSERTION PORTAIT SUR UN COMMENTAIRE** (registre 4.29.g). Elle
        etait `"DefaultRunLauncher" in texte`, sur le contenu BRUT du fichier.
        `mesure` le 2 septembre 2026 : la chaine apparait sur TROIS lignes de
        `dagster.yaml`, et les trois sont des commentaires —
        `grep -v` sur les lignes de commentaire puis `grep -c` rend **0**.
        Le test ne trouvait donc que du commentaire, dans un fichier dont le
        docstring affirme « ce fichier n'est pas un test de texte, et la
        distinction compte ». Le docstring avait raison sur le reste du fichier,
        et faux sur cette assertion-la.

        Elle lit desormais le launcher **effectif**, celui que Dagster resout
        depuis le `dagster.yaml` livre. La resolution est PURE — `InstanceRef`
        calcule un `ConfigurableClassData` sans instancier — donc aucun store
        n'est ouvert et aucun Postgres n'est joint : le fichier declare pourtant
        trois stores Postgres, et les instancier sortirait la suite du disque.
        Le fichier livre est copie dans un repertoire jetable, `from_dir`
        pouvant y creer des repertoires d'artefacts.

        La seconde assertion, elle, etait deja substantielle et elle est
        conservee : `"run_launcher:" not in texte` detecte l'APPARITION d'un bloc
        explicite, c'est-a-dire exactement l'evenement qui rendrait
        `max_resume_run_attempts: 0` mauvais. Elle porte le raisonnement ; la
        premiere ne portait rien.
        """
        texte = CHEMIN.read_text(encoding="utf-8")
        ref = _reference_de_l_instance(CHEMIN)
        donnees = ref.run_launcher_data

        assert donnees is not None, "aucun launcher resolu depuis le dagster.yaml livre"
        assert (donnees.module_name, donnees.class_name) == (
            "dagster",
            "DefaultRunLauncher",
        ), (
            f"le launcher effectif est {donnees.module_name}.{donnees.class_name} "
            "et non DefaultRunLauncher : relire `max_resume_run_attempts`, dont "
            "le 0 ne se defend que pour un launcher incapable de reprendre un run"
        )
        assert "run_launcher:" not in texte, (
            "un `run_launcher` explicite est apparu dans dagster.yaml : verifier "
            "qu'il ne sait pas reprendre un run avant de garder le 0"
        )
