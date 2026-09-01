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
    # PREMIERE page de l'element : celle ou la lecture commence, et celle dont
    # `compute_id` derive son identifiant.
    page_no: int = 1
    # DERNIERE page couverte. Egale a `page_no` sauf pour un element que Docling
    # a fusionne par-dessus une frontiere de page — auquel cas une citation
    # « page N » couvre en realite N a `page_no_end`. Six pages du PDF du corpus
    # n'avaient aucun element propre pour cette raison, et rien ne le disait
    # (registre 4.22).
    page_no_end: int = 1
    bbox: BoundingBox | None = None
    text: str = ""
    order: int = 0
    minio_url: str | None = None
    content: str | None = None
    reference_id: str = "DOC"
    # Profondeur dans la hierarchie des titres : 0 pour un titre de premier
    # niveau, 1 pour ses sous-titres, etc. Elle n'est plafonnee par rien.
    depth: int = 0
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
    # Code ISO 639-1 de la langue du document (``en``, ``fr``...), vide si
    # indeterminee. Le modele d'embedding actuel n'etant entraine que sur de
    # l'anglais, l'agent a besoin de cette cle pour filtrer ou ponderer.
    language: str = ""
    label: str = ""
    # PREMIERE page du chunk. `page_no_end` donne la derniere : un chunk peut
    # porter du texte de DEUX pages, Docling fusionnant les paragraphes qui
    # enjambent une frontiere de page. Citer « page N » seule est donc inexact
    # des que les deux diffèrent (registre 4.22).
    page_no: int = 0
    page_no_end: int = 0
    # Adresse de l'objet MinIO — INTERNE et AUTHENTIFIEE, jamais publique. Un
    # `GET` anonyme y rend **403**, y compris depuis un conteneur du reseau
    # `rag_network` (`mesure`), et l'hote est un nom de service Docker qui ne
    # resout pas au-dehors. L'agent est le PROXY : il lit l'objet avec ses
    # identifiants S3 et le re-sert. Il ne passe jamais cette adresse a un
    # navigateur. Registre 4.25, et `images.object_url` en est le seul site.
    minio_url: str = ""
    reference_id: str = "DOC"
    # Profondeur dans la hierarchie des titres. C'est le nombre d'aretes
    # ``PARENT_OF`` qui separent l'element de la racine de son document, et
    # DEUX ECHELLES S'Y CROISENT — a lire avant de s'en servir :
    #
    # - sur un TITRE, 0 designe un titre rattache au document, 1 un titre
    #   rattache a celui-la, et ainsi de suite sans plafond ;
    # - sur tout AUTRE element, la valeur est celle de son titre + 1. Un
    #   element rattache a un titre de premier niveau vaut donc 1, comme un
    #   sous-titre. La valeur seule ne dit pas laquelle des deux echelles on
    #   lit : c'est ``label`` qui le dit.
    #
    # Et ``depth`` ne decrit JAMAIS un titre par cette voie : aucun
    # ``section_header`` n'est jamais un chunk (registre 4.24, mesure). Le
    # niveau d'un titre se lit sur le sommet du graphe, ou se compte sur la
    # chaine ``PARENT_OF`` — le seul signal exact.
    depth: int = 0
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
