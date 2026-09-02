"""Tests de l'appel ``POST /reindex`` en fin d'ingestion.

Deux proprietes s'opposent et doivent tenir ensemble, ce qui est tout l'objet
de ces tests :

- **aucun echec ne doit faire echouer une ingestion reussie.** Le document est
  dans les trois stores ; rougir la partition declencherait des reprises qui
  reconvertiraient des centaines de pages pour rien ;
- **aucun echec ne doit passer inapercu.** Un appel rate qui rendrait un objet
  d'apparence normale serait une degradation silencieuse de plus, dans une
  chaine qui en compte deja trop.

Un test qui se contenterait de verifier « ca ne leve pas » serait vert sur une
fonction qui ne fait rien du tout. C'est pourquoi chaque cas d'echec asserte
AUSSI que ``ok`` est faux et que ``detail`` nomme la cause.
"""

from __future__ import annotations

import pytest
import requests

from src.pipeline.reindex import API_KEY_HEADER, REINDEX_PATH, request_reindex

URL = "http://agent-api:8000"


class FausseReponse:
    def __init__(self, charge=None, statut: int = 200) -> None:
        self.charge = charge if charge is not None else {"chunks_indexed": 1234, "stale": False}
        self.statut = statut

    def raise_for_status(self) -> None:
        if self.statut >= 400:
            raise requests.HTTPError(f"{self.statut} Server Error")

    def json(self):
        return self.charge


class Espion:
    """Note les appels sortants et rend la reponse programmee."""

    def __init__(self, reponse=None, exception: Exception | None = None) -> None:
        self.reponse = reponse or FausseReponse()
        self.exception = exception
        self.appels: list[dict] = []

    def __call__(self, url, headers=None, timeout=None):
        self.appels.append({"url": url, "headers": headers or {}, "timeout": timeout})
        if self.exception is not None:
            raise self.exception
        return self.reponse


class TestAppelReussi:
    def test_frappe_la_bonne_route(self):
        espion = Espion()
        request_reindex(URL, post=espion)
        assert espion.appels[0]["url"] == f"{URL}{REINDEX_PATH}"

    def test_rapporte_le_compte_de_l_agent(self):
        resultat = request_reindex(URL, post=Espion(FausseReponse({"chunks_indexed": 8421})))
        assert resultat.ok is True
        assert resultat.chunks_indexed == 8421

    def test_url_avec_slash_final_ne_double_pas(self):
        espion = Espion()
        request_reindex(f"{URL}/", post=espion)
        assert espion.appels[0]["url"] == f"{URL}{REINDEX_PATH}"

    def test_reponse_sans_compte_reste_un_succes(self):
        # Un agent d'une version anterieure : l'appel a eu lieu, seul le
        # compte manque. Ce n'est pas un echec de reindexation.
        resultat = request_reindex(URL, post=Espion(FausseReponse({"ok": True})))
        assert resultat.ok is True
        assert resultat.chunks_indexed is None

    def test_le_timeout_configure_est_transmis(self):
        espion = Espion()
        request_reindex(URL, timeout=42.0, post=espion)
        assert espion.appels[0]["timeout"] == 42.0


class TestCleDApi:
    def test_cle_envoyee_quand_elle_est_configuree(self):
        # `pragma: allowlist secret` : « secret » est le mot litteral, choisi
        # comme valeur d'essai justement parce qu'il n'est un secret nulle part.
        # `detect-secrets` ne voit que le nom de l'argument, `api_key`.
        espion = Espion()
        request_reindex(URL, api_key="secret", post=espion)  # pragma: allowlist secret
        assert espion.appels[0]["headers"][API_KEY_HEADER] == "secret"  # pragma: allowlist secret

    def test_aucun_en_tete_sans_cle(self):
        espion = Espion()
        request_reindex(URL, post=espion)
        assert API_KEY_HEADER not in espion.appels[0]["headers"]


class TestUnEchecNeCasseJamaisLIngestion:
    """Le document est ingere : rien ici ne doit remonter jusqu'a l'asset."""

    @pytest.mark.parametrize(
        "exception",
        [
            requests.ConnectionError("agent arrete"),
            requests.Timeout("reconstruction trop longue"),
            requests.HTTPError("401 Unauthorized"),
            ValueError("reponse illisible"),
            OSError("resolution DNS impossible"),
        ],
    )
    def test_aucune_exception_ne_remonte(self, exception):
        resultat = request_reindex(URL, post=Espion(exception=exception))
        assert resultat.ok is False

    @pytest.mark.parametrize("statut", [400, 401, 403, 404, 500, 502, 503])
    def test_aucun_statut_d_erreur_ne_remonte(self, statut):
        resultat = request_reindex(URL, post=Espion(FausseReponse(statut=statut)))
        assert resultat.ok is False


class TestUnEchecNePasseJamaisInapercu:
    """« Ne leve pas » ne suffit pas : une fonction vide serait verte."""

    def test_l_echec_est_marque_comme_tel(self):
        resultat = request_reindex(URL, post=Espion(exception=requests.ConnectionError("refus")))
        assert resultat.called is True
        assert resultat.ok is False
        assert resultat.chunks_indexed is None

    def test_le_detail_nomme_la_cause(self):
        resultat = request_reindex(
            URL, post=Espion(exception=requests.ConnectionError("agent arrete"))
        )
        assert "agent arrete" in resultat.detail
        assert "ConnectionError" in resultat.detail

    def test_les_metadonnees_dagster_crient_l_echec(self):
        # C'est ce que verra l'humain dans l'interface, par partition.
        resultat = request_reindex(URL, post=Espion(exception=requests.Timeout("trop long")))
        assert "ECHEC" in resultat.metadata_value

    def test_un_echec_ne_se_rend_jamais_comme_un_succes(self):
        """LE GARDE DE LA BRANCHE QUE LE LOT 5 A DECIDE DE NE PAS AMPUTER.

        Le registre 5.7 range cette branche dans le code mort : la production ne
        l'atteint plus, `reindex_job.lexical_index` levant d'abord. C'est exact.
        Mais l'etat est atteignable sur l'objet, et sans la branche il tombe sur
        le cas nominal — `mesure` : `"ok — None chunks indexes"`.

        Le test precedent asserte la PRESENCE de « ECHEC ». Celui-ci asserte la
        propriete qui compte, et il est plus fort : un echec ne doit pas pouvoir
        se LIRE comme un succes. Un rendu qui porterait les deux mots passerait
        le premier et rougirait ici.
        """
        resultat = request_reindex(URL, post=Espion(exception=requests.Timeout("trop long")))

        rendu = resultat.metadata_value

        assert not rendu.startswith("ok"), (
            f"un echec de reindexation se rend « {rendu} » : l'operateur lit un "
            "succes dans l'interface Dagster, et l'index lexical de l'agent est "
            "pourtant perime"
        )
        assert "chunks indexes" not in rendu, (
            f"le rendu « {rendu} » annonce un compte de chunks indexes alors "
            "qu'aucune reindexation n'a abouti"
        )

    def test_les_metadonnees_disent_le_compte_en_cas_de_succes(self):
        resultat = request_reindex(URL, post=Espion(FausseReponse({"chunks_indexed": 77})))
        assert "77" in resultat.metadata_value
        assert "ECHEC" not in resultat.metadata_value


class TestUrlVide:
    """Desactiver l'appel est un choix possible ; le taire ne l'est pas."""

    @pytest.mark.parametrize("url", ["", "   "])
    def test_aucun_appel_n_est_tente(self, url):
        espion = Espion()
        resultat = request_reindex(url, post=espion)
        assert espion.appels == []
        assert resultat.called is False

    def test_le_detail_explique_la_consequence(self):
        resultat = request_reindex("", post=Espion())
        assert "lexicale" in resultat.detail

    def test_les_metadonnees_le_signalent(self):
        assert "non appele" in request_reindex("", post=Espion()).metadata_value
