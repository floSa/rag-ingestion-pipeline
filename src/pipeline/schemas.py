"""Modèles Pydantic partagés entre le pipeline Dagster et le service Docling."""

from __future__ import annotations

from pydantic import BaseModel, Field


class BoundingBox(BaseModel):
    """Coordonnées d'une zone dans une page."""

    left: float = Field(alias="l")
    top: float = Field(alias="t")
    right: float = Field(alias="r")
    bottom: float = Field(alias="b")

    model_config = {"populate_by_name": True}


class DocumentMetadata(BaseModel):
    """Métadonnées d'un document extrait."""

    filename: str
    type_file: str
    total_pages: int = 0


class DocumentElement(BaseModel):
    """Élément structurel extrait d'un document (paragraphe, image, table, etc.).

    Les trois champs de position décrivent la place de l'élément dans le
    document, telle que la consomme ``rag-agent-chat`` :

    - ``reference_id`` : parent hiérarchique (id de la section, ou ``DOC``) ;
    - ``page_position`` : rang de l'élément dans sa page ;
    - ``ref_position`` : rang de l'élément sous son parent.
    """

    id: str
    label: str
    page_no: int = 1
    bbox: BoundingBox | None = None
    text: str = ""
    order: int = 0
    minio_url: str | None = None
    content: str | None = None
    reference_id: str = "DOC"
    section_title: str = ""
    page_position: int = 0
    ref_position: int = 0
    type: str = "text"


class ExtractedDocument(BaseModel):
    """Résultat complet d'une extraction Docling."""

    metadata: DocumentMetadata
    elements: list[DocumentElement] = Field(default_factory=list)


class ChunkMetadata(BaseModel):
    """Métadonnées d'un chunk dans ChromaDB — contrat avec ``rag-agent-chat``.

    Ce modèle est la définition de référence du contrat : le consommateur lit
    exactement ces clés dans ``retriever.py``. Construire les métadonnées à
    travers lui garantit qu'aucune n'est oubliée (``page_position`` et
    ``ref_position`` l'étaient, et arrivaient toujours à 0 côté agent).

    ``element_id`` et ``graph_node_id`` restent le hash 10 hexadécimaux de
    l'élément, y compris pour un élément découpé en plusieurs chunks :
    ``rag-agent-chat`` valide ``/context/{element_id}`` sur ``^[a-f0-9]{10}$``
    et lit l'identifiant du chunk dans un champ distinct.
    """

    element_id: str
    graph_node_id: str
    # Nom du fichier seul — le chapitre, pour un livre decoupe.
    filename: str
    # Dossier parent : l'ouvrage auquel appartient le chapitre. Sans lui, une
    # citation ne peut pas dire de quel livre elle vient.
    collection: str = ""
    # Chemin complet relatif a Datas/, identite unique du document.
    source_path: str = ""
    label: str = ""
    page_no: int = 0
    minio_url: str = ""
    reference_id: str = "DOC"
    section_title: str = ""
    page_position: int = 0
    ref_position: int = 0
    chunk_index: int = 0
    chunk_count: int = 1
    # Nombre d'elements du document fusionnes dans ce chunk. 1 signifie que le
    # chunk correspond exactement a un element ; au-dela, l'ancre est le
    # premier d'entre eux.
    block_size: int = 1


class ExtractRequest(BaseModel):
    """Requête d'extraction envoyée au service Docling."""

    filepath: str
    # Chemin relatif à ``Datas/``, tel que le connaît le pipeline — c'est la
    # clé de partition Dagster. Il porte l'identité du document : deux
    # chapitres homonymes dans deux ouvrages différents sont distingués par
    # lui, alors que leur nom de fichier seul les confondrait.
    #
    # Renseigné par le pipeline. Laissé vide (appel manuel), le service le
    # déduit du chemin du fichier.
    source_path: str = ""


class ExtractResponse(BaseModel):
    """Réponse du service Docling : identifiant du job à interroger."""

    job_id: str
    status: str = "pending"
