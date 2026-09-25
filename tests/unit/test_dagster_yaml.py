"""Le *run monitoring* de Dagster, configure dans `dagster.yaml`.

Registres 4.15 a 4.17 : sans run monitoring, un run bloque en `STARTED` bloque
la reindexation indefiniment, sans alerte. Le delai au-dela duquel un run est
declare mort est regle une fois, dans `dagster.yaml`, plutot que dans chaque
sensor.

Ces tests valident la configuration avec le processeur de configuration de
Dagster lui-meme, et comparent ses seuils aux reglages reels du pipeline : ils
testent ce que Dagster lit, pas une chaine de caracteres. Un `dagster.yaml`
invalide fait echouer le demarrage du daemon ; ces tests le detectent avant.
Seul `test_la_duree_annoncee_par_la_documentation_est_celle_de_la_valeur_livree`
lit du texte, pour la raison donnee dans sa docstring.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from dagster._config import process_config
from dagster._core.instance.config import dagster_instance_config_schema

from src.pipeline.settings import PipelineSettings

RACINE = Path(__file__).resolve().parents[2]
CHEMIN = RACINE / "dagster.yaml"


def _sans_separateur_de_milliers(texte: str) -> str:
    """`90 000` et `90\u00a0000` deviennent `90000`.

    La prose separe les milliers, la configuration non : sans cette
    normalisation, la valeur lue dans la configuration ne serait jamais
    retrouvee dans la documentation.
    """
    return re.sub(r"(?<=\d)[ \u00a0](?=\d)", "", texte)


def _reference_de_l_instance(chemin: Path) -> Any:
    """Resout ce que Dagster lit du `dagster.yaml` livre, sans rien ouvrir.

    `InstanceRef.from_dir` rend des `ConfigurableClassData`, qui decrivent les
    objets sans les instancier : aucun des trois stores Postgres declares n'est
    contacte, et la suite reste hors reseau.

    Le fichier est copie dans un repertoire jetable : `from_dir` prend son
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
        """Controle negatif : sans lui, un schema permissif validerait
        n'importe quel fichier, et le test precedent ne prouverait rien."""
        resultat = process_config(
            dagster_instance_config_schema(), {**configuration, "reglage_inexistant": 1}
        )

        assert not resultat.success


class TestLeDelaiDeGardeEstArme:
    """Le run monitoring est active et borne les runs orphelins."""

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
        """`max_runtime_seconds` est superieur au plafond du pipeline.

        `PipelineSettings.extraction_timeout_seconds` est le plafond que le
        pipeline s'accorde par document. Un `max_runtime_seconds` plus court
        tuerait des runs que le pipeline considere encore legitimes.

        Le run monitoring est le dernier recours : il ne se declenche que si le
        plafond du pipeline n'a pas agi, c'est-a-dire si le run est bloque et
        non simplement lent.

        Ce test ne verifie que la borne basse. La borne haute est verifiee par
        `test_la_borne_reste_juste_au_dessus_du_plafond_du_pipeline`, et la
        duree annoncee dans la documentation par
        `test_la_duree_annoncee_par_la_documentation_est_celle_de_la_valeur_livree`.
        """
        borne = configuration["run_monitoring"]["max_runtime_seconds"]
        plafond_du_pipeline = PipelineSettings().extraction_timeout_seconds

        assert borne > plafond_du_pipeline, (
            f"max_runtime_seconds={borne} est sous le plafond que le pipeline "
            f"s'accorde par document ({plafond_du_pipeline} s) : des runs "
            "legitimes seraient tues, et la cause serait cherchee ailleurs"
        )

    def test_la_borne_reste_juste_au_dessus_du_plafond_du_pipeline(
        self, configuration: dict[str, Any]
    ) -> None:
        """`max_runtime_seconds` reste juste au-dessus du plafond du pipeline.

        Le test precedent verifie la borne basse. Sans celui-ci, une valeur de
        500 000 s passait la suite entiere (mesure lors d'un audit).

        Le test ne fige pas la valeur (`borne == 90000`) : il verifie la
        propriete que `dagster.yaml` annonce, a savoir une borne posee *juste
        au-dessus* du plafond du pipeline, dernier recours et non second
        plafond independant.

        Pourquoi un dixieme : l'ecart actuel vaut 3 600 s sur 86 400, soit
        4,2 %. Une marge d'un dixieme (8 640 s) laisse l'ecart plus que doubler :
        un ajustement reste possible, un changement de nature non. Un tel
        changement est une decision a ecrire dans `dagster.yaml` et ici.
        """
        borne = configuration["run_monitoring"]["max_runtime_seconds"]
        plafond_du_pipeline = PipelineSettings().extraction_timeout_seconds
        marge_maximale = plafond_du_pipeline // 10

        assert borne <= plafond_du_pipeline + marge_maximale, (
            f"max_runtime_seconds={borne} depasse le plafond du pipeline "
            f"({plafond_du_pipeline} s) de {borne - plafond_du_pipeline} s, soit "
            f"plus d'un dixieme : ce n'est plus la DERNIERE ligne posee juste "
            f"au-dessus, c'est un second plafond independant. Si c'est voulu, "
            f"c'est une decision : l'ecrire dans `dagster.yaml` et relacher ce "
            f"garde en disant pourquoi"
        )

    def test_la_duree_annoncee_par_la_documentation_est_celle_de_la_valeur_livree(
        self, configuration: dict[str, Any]
    ) -> None:
        """La duree annoncee par la documentation est celle de la valeur livree.

        Confusion a eviter (registre 4.35.a) : `max_runtime_seconds: 90000`
        vaut 25 h ; les 24 h sont celles d'`extraction_timeout_seconds`, qui
        borne le pipeline par document et non le run monitoring.

        C'est un test de texte, car la propriete verifiee est textuelle : la
        documentation doit dire la verite sur la valeur. Il part de la valeur
        effective, lue dans la configuration, et non d'un litteral, et exige que
        chaque fichier la nomme avec le bon nombre d'heures.

        Deux fichiers sont verifies : `dagster.yaml`, qui porte l'arithmetique
        de reference, et `documentation/orchestration.md`, la documentation
        d'exploitation du reglage. Les autres (README, axes_amelioration,
        factory) ne font que citer le chiffre.
        """
        borne = configuration["run_monitoring"]["max_runtime_seconds"]
        heures_attendues = borne // 3600

        for relatif in ("dagster.yaml", "documentation/orchestration.md"):
            texte = _sans_separateur_de_milliers((RACINE / relatif).read_text(encoding="utf-8"))
            annonces = [int(h) for h in re.findall(rf"{borne}\D{{0,20}}?(\d+)\s*h\b", texte)]

            assert annonces, (
                f"{relatif} n'annonce nulle part la duree de la valeur livree "
                f"(max_runtime_seconds={borne}) : soit la valeur a change sans "
                f"que la documentation suive, soit la phrase d'arithmetique a "
                f"disparu. Les deux laissent un operateur devant un chiffre faux"
            )
            assert all(h == heures_attendues for h in annonces), (
                f"{relatif} annonce {annonces} h la ou "
                f"max_runtime_seconds={borne} vaut {heures_attendues} h : c'est "
                f"exactement la confusion du registre 4.35.a, ou le plafond du "
                f"PIPELINE etait cite comme celui du RUN MONITORING"
            )

    def test_la_reprise_n_est_pas_armee_pour_un_launcher_qui_ne_sait_pas_reprendre(
        self, configuration: dict[str, Any]
    ) -> None:
        """`DefaultRunLauncher` ne reprend pas un run dont le worker est mort.

        Armer la reprise donnerait un reglage sans effet (meme cas que la
        variable morte `CHUNK_SIZE` du registre 5.1). Un run mort est marque en
        echec, ce qui libere la reindexation.
        """
        assert configuration["run_monitoring"].get("max_resume_run_attempts") == 0

    def test_le_launcher_est_bien_celui_que_ce_raisonnement_suppose(self) -> None:
        """Le launcher effectif est `DefaultRunLauncher`, que suppose le test precedent.

        `max_resume_run_attempts: 0` ne se justifie que pour un launcher qui ne
        sait pas reprendre un run. Avec un launcher qui le sait
        (`K8sRunLauncher`, `DockerRunLauncher`), ce reglage deviendrait mauvais,
        et ce test le signalera.

        Le launcher est lu tel que Dagster le resout depuis le `dagster.yaml`
        livre, et non cherche dans le texte, ou la chaine n'apparait qu'en
        commentaire (registre 4.29.g). La resolution par `InstanceRef` n'ouvre
        aucun store (voir `_reference_de_l_instance`).

        La seconde assertion (`"run_launcher:" not in texte`) detecte
        l'apparition d'un bloc `run_launcher` explicite, precisement ce qui
        rendrait `max_resume_run_attempts: 0` a revoir.
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
