"""Le service refuse de demarrer hors contrat — et c'etait la seule preuve.

Registre 4.19. `main.lifespan` place le controle du modele d'embedding **hors**
du `try` du prechargement, avant `queue.start()` : le service refuse donc bien de
demarrer sur un modele hors contrat, et le `README.md` l'annonce en propres
termes — « le service refuse desormais de demarrer sur un autre modele ».

**Mais retirer cette ligne, ou la deplacer DANS le `try`, laissait la suite
verte.** Le contrat lui-meme reste tenu par `get_embedding_model` (garde) ; c'est
le *fail-fast* — la propriete que la documentation VEND — qui reposait sur une
relecture. C'est mot pour mot la lecon du mandat : « un code de sortie documente
et justifie n'etait asserte nulle part ».

Le controle passe par un **sous-processus**, et pour la meme raison que
`test_verify_data.py` : ce qui est en cause est que le PROCESSUS meurt. Un test
qui appellerait `lifespan` en attrapant l'exception prouverait qu'un objet a ete
leve, pas que le service refuse de servir — et c'est la difference exacte entre
« l'exception existe » et « le service est mort ». Un `uvicorn` qui demarre quand
meme sur un modele anglais est precisement la panne la plus couteuse du systeme
(contrat, exigence 1), et elle est parfaitement silencieuse.

Les dependances lourdes sont bouchonnees comme de VRAIS paquets en tete de
`PYTHONPATH`, et non dans `sys.modules` : sinon les bouchons survivraient au test
et l'ordre des tests deviendrait significatif.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Le modele du contrat, lu depuis son site canonique : le recopier ici en ferait
# un second site, donc une divergence possible avec ce que le code exige.
from src.docling_service.embedding import CONTRACT_MODEL

RACINE = Path(__file__).resolve().parents[2]

# Un modele anglais, celui-la meme qu'un `.env` de juin portait face a un agent
# multilingue (contrat, exigence 1). Les deux rendent 384 dimensions : ChromaDB
# accepte sans broncher, et verifier la dimension ne protege de rien.
MODELE_HORS_CONTRAT = "all-MiniLM-L6-v2"

# Bouchons des deps lourdes. Le service en importe assez pour qu'un import nu
# echoue cote hote ; seul le DEMARRAGE nous interesse.
BOUCHONS = {
    "chromadb/__init__.py": "",
    "nebula3/__init__.py": "",
    "nebula3/Config.py": "class Config:\n    pass\n",
    "nebula3/gclient/__init__.py": "",
    "nebula3/gclient/net.py": (
        "class ConnectionPool:\n"
        "    def init(self, a, c):\n        return True\n"
        "    def get_session(self, u, p):\n        return None\n"
        "    def close(self):\n        pass\n"
    ),
    "docling/__init__.py": "",
    # FastAPI n'est pas dans le venv du depot : ce bouchon porte exactement ce
    # que `main.py` en utilise — une application, ses deux decorateurs de route,
    # et son exception HTTP. Rien de plus : un bouchon qui en ferait plus
    # deviendrait un second FastAPI a maintenir.
    "fastapi/__init__.py": (
        "class HTTPException(Exception):\n"
        "    def __init__(self, status_code=500, detail=''):\n"
        "        super().__init__(detail)\n"
        "        self.status_code = status_code\n"
        "        self.detail = detail\n"
        "\n"
        "\n"
        "def _decorateur(*a, **k):\n"
        "    def poser(fonction):\n        return fonction\n"
        "    return poser\n"
        "\n"
        "\n"
        "class FastAPI:\n"
        "    def __init__(self, *a, **k):\n        self.kwargs = k\n"
        "    post = staticmethod(_decorateur)\n"
        "    get = staticmethod(_decorateur)\n"
    ),
    "minio/__init__.py": (
        "class Minio:\n"
        "    def __init__(self, *a, **k):\n        pass\n"
        "\n"
        "\n"
        "class S3Error(Exception):\n    pass\n"
    ),
    "sentence_transformers/__init__.py": (
        "class SentenceTransformer:\n    def __init__(self, *a, **k):\n        pass\n"
    ),
}

# Le programme d'essai : il fait tourner le `lifespan` du service livre, comme
# uvicorn le fait, et sort en 0 si le service accepte de demarrer.
PROGRAMME = """
import asyncio
import sys

from src.docling_service.main import app, lifespan, queue


async def demarrer():
    async with lifespan(app):
        pass


try:
    asyncio.run(demarrer())
except BaseException as exc:
    print(f"REFUS {type(exc).__name__}: {exc}")
    # L'etat de la FILE apres le refus. C'est la propriete d'ordre : place apres
    # `queue.start()`, le controle laisserait un worker vivant, capable de
    # prendre un job et d'ecrire avec le mauvais modele avant que le service ne
    # meure.
    ouvrier = getattr(queue, "_worker", None)
    print(f"FILE_ACTIVE={bool(ouvrier is not None and ouvrier.is_alive())}")
    sys.exit(1)
ouvrier = getattr(queue, "_worker", None)
print(f"FILE_ACTIVE={bool(ouvrier is not None and ouvrier.is_alive())}")
print("SERVICE_ACCEPTE")
"""


def _demarrer(tmp_path: Path, modele: str):
    """Fait tourner le `lifespan` du service dans un sous-processus.

    Args:
        tmp_path: Repertoire de travail. Pas de `.env` dedans, donc les reglages
            sont ceux du code et de l'environnement, jamais ceux du poste.
        modele: Valeur de `EMBEDDING_MODEL_NAME`.

    Returns:
        Le processus termine.
    """
    bouchons = tmp_path / "bouchons"
    for chemin, source in BOUCHONS.items():
        cible = bouchons / chemin
        cible.parent.mkdir(parents=True, exist_ok=True)
        cible.write_text(source, encoding="utf-8")

    environnement = dict(os.environ)
    environnement["PYTHONPATH"] = os.pathsep.join([str(bouchons), str(RACINE)])
    environnement["EMBEDDING_MODEL_NAME"] = modele
    return subprocess.run(
        [sys.executable, "-c", PROGRAMME],
        cwd=tmp_path,
        env=environnement,
        capture_output=True,
        text=True,
        timeout=180,
    )


class TestLeServiceRefuseDeDemarrerHorsContrat:
    """Le *fail-fast* que le README vend, enfin asserte."""

    def test_un_modele_hors_contrat_empeche_le_demarrage(self, tmp_path: Path) -> None:
        acheve = _demarrer(tmp_path, MODELE_HORS_CONTRAT)

        assert acheve.returncode == 1, acheve.stdout + acheve.stderr
        assert "REFUS" in acheve.stdout, acheve.stdout
        assert "SERVICE_ACCEPTE" not in acheve.stdout, (
            "le service a demarre sur un modele hors contrat : l'index sera "
            "silencieusement anglais, et la recherche rendra des passages "
            "plausibles et faux (contrat, exigence 1)"
        )

    def test_le_modele_du_contrat_laisse_le_service_demarrer(self, tmp_path: Path) -> None:
        """LE TEMOIN, et sans lui le test precedent serait vrai d'un service
        qui ne demarre JAMAIS — ce qui passerait pour un fail-fast reussi."""
        acheve = _demarrer(tmp_path, CONTRACT_MODEL)

        assert acheve.returncode == 0, acheve.stdout + acheve.stderr
        assert "SERVICE_ACCEPTE" in acheve.stdout, acheve.stdout

    def test_le_refus_nomme_le_modele_fautif(self, tmp_path: Path) -> None:
        """Un refus qui ne dit pas ce qui cloche fait chercher ailleurs.

        C'est le meme defaut que le « `pre-commit` not found » du mandat §2.1 :
        fail-closed, donc sans danger, mais muet sur sa cause.
        """
        acheve = _demarrer(tmp_path, MODELE_HORS_CONTRAT)

        assert MODELE_HORS_CONTRAT in acheve.stdout, acheve.stdout
        assert CONTRACT_MODEL in acheve.stdout, acheve.stdout

    def test_le_refus_precede_le_demarrage_de_la_file(self, tmp_path: Path) -> None:
        """Le controle est AVANT `queue.start()`, et l'ordre est la propriete.

        Place apres, le worker serait deja lance : il pourrait prendre un job et
        commencer a ecrire avec le mauvais modele avant que le service ne meure.
        Un index MIXTE est pire qu'un index faux — `_inscrire_le_modele` leve
        dessus, donc la reparation coute une purge.
        """
        acheve = _demarrer(tmp_path, MODELE_HORS_CONTRAT)

        assert "FILE_ACTIVE=False" in acheve.stdout, (
            "la file d'extraction tourne alors que le service refuse de "
            "demarrer : un worker vivant peut prendre un job et ecrire avec le "
            f"mauvais modele. Sortie : {acheve.stdout}"
        )

    def test_le_temoin_de_la_file_n_est_pas_creux(self, tmp_path: Path) -> None:
        """LE TEMOIN DU TEMOIN, et il manquait — la mutation le prouve.

        `mesure` : deplacer le controle APRES `queue.start()` laissait le test
        d'ordre ci-dessus VERT, parce qu'il cherchait l'absence d'un mot dans la
        sortie. Une assertion d'ABSENCE est vraie quand rien ne se produit, y
        compris quand rien ne l'observe. Celle-ci prouve que le montage sait
        voir une file DEMARREE : sur le modele du contrat, elle l'est.
        """
        acheve = _demarrer(tmp_path, CONTRACT_MODEL)

        assert "FILE_ACTIVE=True" in acheve.stdout, (
            "le montage ne sait pas observer une file demarree : le test "
            f"d'ordre serait vert sans rien garder. Sortie : {acheve.stdout}"
        )
