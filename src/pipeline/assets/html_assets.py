"""Asset de pré-traitement et nettoyage des fichiers HTML."""

from __future__ import annotations

import glob
import os

from bs4 import BeautifulSoup
from dagster import asset

PUBLISHER_NAV_CLASSES: list[str] = ["sbo-site-nav", "packt-header", "site-menu"]


@asset(group_name="pre_processing")
def pre_process_html() -> list[str]:
    """Nettoie les fichiers HTML en supprimant les éléments de navigation éditeurs.

    Cible les contenus parasites Packt, O'Reilly, etc. (nav, header, footer, aside)
    et sauvegarde une copie nettoyée ``.cleaned`` pour Docling.

    Returns:
        Liste des chemins des fichiers HTML nettoyés.
    """
    source_dir = "/opt/dagster/app/Datas"
    files = glob.glob(f"{source_dir}/**/*.html", recursive=True)

    cleaned_files: list[str] = []
    for f in files:
        if not os.path.exists(f) or os.path.getsize(f) == 0:
            continue
        try:
            with open(f, encoding="utf-8") as fh:
                soup = BeautifulSoup(fh.read(), "html.parser")

            for element in soup.find_all(["nav", "header", "footer", "aside"]):
                element.decompose()

            for class_name in PUBLISHER_NAV_CLASSES:
                for element in soup.find_all(class_=class_name):
                    element.decompose()

            clean_path = f"{f}.cleaned"
            with open(clean_path, "w", encoding="utf-8") as out:
                out.write(str(soup))

            cleaned_files.append(clean_path)
        except Exception as exc:
            print(f"Error processing {f}: {exc}")

    return cleaned_files
