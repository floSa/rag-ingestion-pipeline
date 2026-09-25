"""Fixtures partagees pour les tests."""

from __future__ import annotations

import pytest

from src.pipeline.schemas import (
    BoundingBox,
    DocumentElement,
    DocumentMetadata,
    ExtractedDocument,
)

# L'adresse du stockage objet n'a aucune valeur par defaut : sans
# `S3_ENDPOINT`, la construction des reglages echoue (`src/reglages_s3.py`). La
# suite se place donc dans un environnement configure, comme la production.
#
# La valeur ne designe rien de joignable, volontairement : aucun test unitaire
# n'ouvre de connexion. Un test qui en ouvrirait une echouerait sur une
# resolution de nom, ce qui se diagnostique facilement.
#
# Les variables sont posees par `monkeypatch`, donc dans `os.environ` : les
# tests qui lancent un sous-processus (`test_wipe_stores`, `test_verify_data`,
# `test_importabilite_cote_hote`) en heritent. C'est voulu : ils testent un
# module qui construit ses reglages au demarrage.
#
# Le test de l'absence de defaut n'en est pas affecte : `test_settings.py`
# retire lui-meme la variable avant de verifier le refus.
ENDPOINT_DE_TEST = "stockage-de-test:8333"


@pytest.fixture(autouse=True)
def _environnement_de_stockage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Declare la configuration du stockage objet pour toute la suite."""
    monkeypatch.setenv("S3_ENDPOINT", ENDPOINT_DE_TEST)
    monkeypatch.setenv("S3_BUCKET", "documents")
    monkeypatch.setenv("S3_ACCESS_KEY", "cle-d-acces-de-test")
    monkeypatch.setenv("S3_SECRET_KEY", "cle-secrete-de-test")  # pragma: allowlist secret


@pytest.fixture()
def sample_metadata() -> DocumentMetadata:
    return DocumentMetadata(filename="test_doc", type_file="pdf", total_pages=3)


@pytest.fixture()
def sample_bbox() -> BoundingBox:
    return BoundingBox(l=10.0, t=200.0, r=100.0, b=150.0)


@pytest.fixture()
def sample_element(sample_bbox: BoundingBox) -> DocumentElement:
    return DocumentElement(
        id="abc123",
        label="text",
        page_no=1,
        bbox=sample_bbox,
        text="Hello world",
        order=0,
        reference_id="DOC",
        page_position=1,
        ref_position=1,
        type="text",
    )


@pytest.fixture()
def sample_document(
    sample_metadata: DocumentMetadata, sample_element: DocumentElement
) -> ExtractedDocument:
    return ExtractedDocument(metadata=sample_metadata, elements=[sample_element])
