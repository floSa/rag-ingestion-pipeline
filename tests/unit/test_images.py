"""L'adresse d'un objet, sa CLE, et le seul site qui construit un client S3.

Trois proprietes que le lot « sans-minio » installe, et qu'aucun test ne tenait :

- **`object_key` est l'inverse EXACT d'`object_url`.** Le contrat publie les
  deux, et ils doivent decrire le meme objet. Une derivation approximative
  rendrait une cle que `stat_object` ne retrouve pas, sur un objet present ;
- **le SDK n'est nomme qu'a UN endroit de `src/`.** Il en etait nomme a trois,
  dont deux qui construisaient chacun leur client avec leur propre `secure=` ;
- **un client ne se batit pas sans adresse.** `build_client` est publique, donc
  joignable avec une adresse lue ailleurs que dans les reglages valides.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.docling_service import images
from src.reglages_s3 import MESSAGE_ENDPOINT_MANQUANT

RACINE_DEPOT = Path(__file__).resolve().parents[2]

# Le nom du paquet de la bibliotheque cliente. Elle RESTE : c'est un client S3
# generique, et c'est par elle que la pile a change de stockage sans qu'une
# ligne de televersement bouge. Ce qui est garde ici est le nombre de ses
# points d'entree, pas sa presence.
PAQUET_DU_SDK = "minio"

# Le SEUL module de `src/` qui a le droit de le nommer.
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
        """Un `unquote` ici rendrait 404 sur un objet PRESENT.

        `object_url` est une concatenation pure : la cle apparait telle quelle
        dans le chemin. La decoder rendrait une chaine differente de celle
        passee a `put_object`, et c'est exactement le genre d'ecart qui ne se
        voit qu'a la lecture, document par document.
        """
        cle = "images/livre/100%25_de_marge.png"

        assert images.object_key(images.object_url(cle)) == cle

    def test_une_adresse_vide_rend_une_cle_vide(self) -> None:
        """Une absence se lit comme une absence, jamais comme une cle fausse."""
        assert images.object_key("") == ""

    def test_une_adresse_sans_cle_sous_le_bucket_rend_une_cle_vide(self) -> None:
        assert images.object_key("http://stockage:8333/documents") == ""

    def test_l_adresse_est_en_style_chemin(self) -> None:
        """LE TEMOIN : la forme publiee est `http://hote/bucket/cle`.

        Sans lui, un `object_url` qui rendrait n'importe quoi passerait les
        aller-retours ci-dessus, `object_key` etant son inverse.
        """
        rendue = images.object_url("images/a/b.png")

        assert rendue.startswith("http://")
        assert rendue.endswith("/documents/images/a/b.png")


class TestUnClientNeSeBatitPasSansAdresse:
    """`build_client` est PUBLIQUE, donc joignable hors des reglages valides."""

    def test_une_adresse_vide_est_refusee(self) -> None:
        with pytest.raises(ValueError) as leve:
            images.build_client("", "cle", "secret")

        assert str(leve.value) == MESSAGE_ENDPOINT_MANQUANT

    def test_une_adresse_blanche_est_refusee(self) -> None:
        with pytest.raises(ValueError):
            images.build_client("   ", "cle", "secret")

    def test_une_adresse_declaree_construit_un_client(self) -> None:
        """LE TEMOIN. Un `build_client` qui leverait toujours rendrait tout vert.

        Construire n'ouvre aucune connexion : le SDK ne joint le serveur qu'au
        premier appel.
        """
        client = images.build_client("stockage-de-test:8333", "cle", "secret")

        assert client is not None


class TestLeSdkEstNommeAUnSeulEndroitDeSrc:
    """Il l'etait a TROIS, et deux d'entre eux construisaient leur propre client.

    `pipeline/media.py` batissait le sien avec ses propres reglages et son
    propre `secure=` ; `verify_data.py` le sien encore. Trois sites pour une
    seule decision, donc trois endroits ou en changer — et `media.py` publiait
    de surcroit l'adresse avec les reglages d'un AUTRE module (registre 4.29.b).

    Le balayage est STATIQUE, a l'AST : il n'importe rien, donc il ne depend ni
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
        """LE TEMOIN, ET IL PASSE EN PREMIER.

        Un balayage qui ne lirait rien rendrait « zero porteur », et le garde
        ci-dessous serait vert sans rien garder.
        """
        porteurs = self._modules_qui_importent_le_sdk()

        assert SITE_UNIQUE in porteurs, (
            f"le balayage ne trouve pas l'import connu de {SITE_UNIQUE} : il ne "
            f"lit pas ce qu'il annonce ({sorted(porteurs)})"
        )

    def test_aucun_autre_module_de_src_ne_nomme_le_sdk(self) -> None:
        """LE GARDE. Un second site de construction rougit ici."""
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

        C'est precisement ce que faisait `media.py`, et c'est ce qui rendait le
        site unique impossible a tenir.
        """
        assert hasattr(images, "ClientS3")
