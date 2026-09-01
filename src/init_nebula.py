"""Amorcage du cluster NebulaGraph : enregistre le storaged et cree le space.

Lance a la main sur une pile neuve, avant le premier demarrage du service
d'extraction. Le service, lui, joue `init_schema()` a chaque demarrage et cree
tags et aretes ; ce script-ci ne fait que ce qu'`init_schema` ne peut pas faire
tant que le storaged n'est pas enregistre.

**Les adresses et les identifiants viennent des reglages, plus du code.** Ce
fichier ecrivait `("graphd", 9669)` et `("root", "nebula")` en dur : c'etait le
QUATRIEME site du defaut que le registre 4.3 en annonce trois. Un poste dont le
graphd ecoute ailleurs, ou dont le mot de passe a change, voyait ce script
echouer sans qu'aucun reglage n'explique pourquoi.
"""

from __future__ import annotations

import sys
import time

from src.docling_service.ngql import create_space_statement
from src.docling_service.settings import get_settings


def main() -> int:
    """Enregistre le storaged et cree le space. Rend 0 si la connexion a eu lieu.

    Returns:
        0 si le pool s'est ouvert, 1 sinon. Les echecs de requete sont
        affiches : ce script est un outil d'amorcage qu'on lit, pas une porte.
    """
    from nebula3.Config import Config
    from nebula3.gclient.net import ConnectionPool

    settings = get_settings()
    pause = settings.nebula_amorcage_pause_seconds
    pool = ConnectionPool()
    if not pool.init([(settings.nebula_host, settings.nebula_port)], Config()):
        print(f"Connexion impossible a {settings.nebula_host}:{settings.nebula_port}")
        return 1

    session = pool.get_session(settings.nebula_user, settings.nebula_password)

    print("Enregistrement du storaged...")
    res = session.execute('ADD HOSTS "storaged":9779;')
    print("ADD HOSTS:", res.is_succeeded(), res.error_msg())

    time.sleep(pause)

    print("Hotes...")
    res = session.execute("SHOW HOSTS;")
    if res.is_succeeded():
        for row in res.rows():
            print(row)
    else:
        print("SHOW HOSTS a echoue :", res.error_msg())

    print("Creation du space...")
    res = session.execute(create_space_statement())
    print("CREATE SPACE:", res.is_succeeded(), res.error_msg())

    time.sleep(pause * 2)

    print("Spaces...")
    res = session.execute("SHOW SPACES;")
    if res.is_succeeded():
        for row in res.rows():
            print(row)
    else:
        print("SHOW SPACES a echoue :", res.error_msg())

    pool.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
