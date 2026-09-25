"""L'adresse d'un objet, sa cle, et le seul site qui construit un client S3.

Trois proprietes :

- **`object_key` est l'inverse exact d'`object_url`.** Le contrat publie les
  deux, et ils doivent decrire le meme objet. Une derivation approximative
  rendrait une cle que `stat_object` ne retrouve pas, sur un objet present ;
- **la bibliotheque cliente n'est importee qu'a un endroit de `src/`**, pour
  qu'il n'y ait qu'une construction de client et un seul `secure=` ;
- **un client ne se construit pas sans adresse.** `build_client` est publique,
  donc joignable avec une adresse lue ailleurs que dans les reglages valides.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.docling_service import images
from src.reglages_s3 import MESSAGE_ENDPOINT_MANQUANT

RACINE_DEPOT = Path(__file__).resolve().parents[2]

# Le nom du paquet de la bibliotheque cliente, un client S3 generique. Les tests
# verifient le nombre de ses points d'entree, pas sa presence.
PAQUET_DU_SDK = "minio"

# Le seul module de `src/` autorise a l'importer.
SITE_UNIQUE = "src/docling_service/images.py"


class TestLaCleEstLInverseExactDeLAdresse:
    """`object_key(object_url(cle)) == cle`, y compris sur ce que le corpus porte."""

    # Les cles telles que la production les emet. `sanitize_key` les borne a
    # `[A-Za-z0-9/_.-]`, et les trois formes ci-dessous sont celles des trois
    # chemins d'image : crop PDF, image Markdown, image de capture HTML.
    CLES = (
        "images/docling_paper/bcbe047fc2_table.png",
        "images/md/notes/Un_titre/0003_photo.jpg",
        "images/html/htms/MLOps_with_Databricks/Preface/img_0000.png",
    )

    @pytest.mark.parametrize("cle", CLES)
    def test_l_aller_retour_rend_la_cle_nue(self, cle: str) -> None:
        assert images.object_key(images.object_url(cle)) == cle

    def test_la_cle_n_est_pas_decodee(self) -> None:
        """Un `unquote` rendrait 404 sur un objet present.

        `object_url` est une concatenation pure : la cle apparait telle quelle
        dans le chemin. La decoder rendrait une chaine differente de celle
        passee a `put_object`, un ecart qui ne se voit qu'a la lecture.
        """
        cle = "images/livre/100%25_de_marge.png"

        assert images.object_key(images.object_url(cle)) == cle

    def test_une_adresse_vide_rend_une_cle_vide(self) -> None:
        """Une absence se lit comme une absence, jamais comme une cle fausse."""
        assert images.object_key("") == ""

    def test_une_adresse_sans_cle_sous_le_bucket_rend_une_cle_vide(self) -> None:
        assert images.object_key("http://stockage:8333/documents") == ""

    def test_l_adresse_est_en_style_chemin(self) -> None:
        """La forme publiee est `http://hote/bucket/cle`.

        Sans ce test, un `object_url` qui rendrait n'importe quoi passerait les
        aller-retours ci-dessus, `object_key` etant son inverse.
        """
        rendue = images.object_url("images/a/b.png")

        assert rendue.startswith("http://")
        assert rendue.endswith("/documents/images/a/b.png")


class TestUnClientNeSeBatitPasSansAdresse:
    """`build_client` est publique, donc joignable hors des reglages valides."""

    def test_une_adresse_vide_est_refusee(self) -> None:
        with pytest.raises(ValueError) as leve:
            images.build_client("", "cle", "secret")

        assert str(leve.value) == MESSAGE_ENDPOINT_MANQUANT

    def test_une_adresse_blanche_est_refusee(self) -> None:
        with pytest.raises(ValueError):
            images.build_client("   ", "cle", "secret")

    def test_une_adresse_declaree_construit_un_client(self) -> None:
        """Contre-epreuve : un `build_client` qui leverait toujours passerait les tests de refus.

        Construire n'ouvre aucune connexion : le SDK ne joint le serveur qu'au
        premier appel.
        """
        client = images.build_client("stockage-de-test:8333", "cle", "secret")

        assert client is not None


class TestLeSdkEstNommeAUnSeulEndroitDeSrc:
    """Un seul module de `src/` importe la bibliotheque cliente (registre 4.29.b).

    Plusieurs sites de construction voudraient dire plusieurs reglages et
    plusieurs `secure=` pour une seule decision.

    Le balayage est statique, a l'AST : il n'importe rien, donc il ne depend ni
    du venv ni de l'ordre des tests.
    """

    @staticmethod
    def _modules_qui_importent_le_sdk() -> dict[str, list[str]]:
        """Par module de `src/`, les imports du paquet du SDK, a tout niveau."""
        porteurs: dict[str, list[str]] = {}
        for chemin in sorted((RACINE_DEPOT / "src").rglob("*.py")):
            arbre = ast.parse(chemin.read_text(encoding="utf-8"))
            lignes = []
            for noeud in ast.walk(arbre):
                if isinstance(noeud, ast.Import):
                    lignes += [
                        alias.name
                        for alias in noeud.names
                        if alias.name.split(".")[0] == PAQUET_DU_SDK
                    ]
                elif (
                    isinstance(noeud, ast.ImportFrom)
                    and not noeud.level
                    and (noeud.module or "").split(".")[0] == PAQUET_DU_SDK
                ):
                    lignes.append(noeud.module or "")
            if lignes:
                porteurs[str(chemin.relative_to(RACINE_DEPOT))] = lignes
        return porteurs

    def test_le_balayage_voit_le_site_connu(self) -> None:
        """Contre-epreuve, placee en premier.

        Un balayage qui ne lirait rien rendrait « zero porteur », et le test
        suivant passerait sans rien verifier.
        """
        porteurs = self._modules_qui_importent_le_sdk()

        assert SITE_UNIQUE in porteurs, (
            f"le balayage ne trouve pas l'import connu de {SITE_UNIQUE} : il ne "
            f"lit pas ce qu'il annonce ({sorted(porteurs)})"
        )

    def test_aucun_autre_module_de_src_ne_nomme_le_sdk(self) -> None:
        """Un second site d'import fait echouer ce test."""
        porteurs = self._modules_qui_importent_le_sdk()

        autres = {nom: lignes for nom, lignes in porteurs.items() if nom != SITE_UNIQUE}
        assert autres == {}, (
            f"le SDK est nomme ailleurs que dans {SITE_UNIQUE} : {autres}. Il "
            "l'etait a trois endroits, dont deux qui construisaient chacun leur "
            "client avec leurs propres reglages. Passe par "
            "`images.build_client`, et prends le type par `images.ClientS3`."
        )

    def test_le_type_du_client_est_publie_sous_un_nom_generique(self) -> None:
        """Sans lui, un appelant devrait importer le SDK pour annoter son client.

        Le site unique serait alors impossible a tenir.
        """
        assert hasattr(images, "ClientS3")
