"""Les reglages du stockage objet, declares en un seul endroit.

Deux classes de reglages decident du meme objet (registre 4.29.b) : le pipeline
Dagster televerse (``src/pipeline/settings.py``), le service d'extraction
publie l'adresse (``src/docling_service/settings.py``). Un bucket different
d'un cote produirait un objet present et une adresse qui rend 404, sans erreur.
Les deux classes heritent donc de celle-ci.

``S3_ENDPOINT`` n'a pas de valeur par defaut. Un defaut ecrit dans le code
ferait parler un poste sans `.env` a un serveur qu'il n'a pas choisi, y compris
``python -m src.wipe_stores``, qui vide le bucket designe. Un defaut absent fait
echouer le demarrage ; un defaut faux ferait reussir la purge du mauvais
stockage.

Les noms ``S3_*`` designent le protocole, pas un produit : le serveur utilise
se lit dans la valeur de ``S3_ENDPOINT`` (aujourd'hui la passerelle S3 de
SeaweedFS).
"""

from __future__ import annotations

from pydantic import Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings

MESSAGE_IDENTIFIANT_MANQUANT = (
    "{variable} n'est pas defini, ou vide. Les identifiants du stockage objet "
    "n'ont AUCUNE valeur par defaut : un client aux identifiants vides demarre "
    "sans erreur puis rend 403 a chaque televersement, et les images manquent "
    "sans que rien ne le dise. docker-compose.yml derive S3_ACCESS_KEY et "
    "S3_SECRET_KEY de SEAWEEDFS_RW_ACCESS_KEY / SEAWEEDFS_RW_SECRET_KEY : "
    "verifie que le service les recoit, puis recree-le "
    "(« docker compose up -d --force-recreate --no-deps <service> »)."
)

MESSAGE_ENDPOINT_MANQUANT = (
    "S3_ENDPOINT n'est pas defini. Le stockage objet n'a AUCUNE adresse par "
    "defaut, et c'est delibere : celle qui existait a survecu au stockage "
    "qu'elle designait, si bien qu'un outil lance sans `.env` "
    "visait le mauvais serveur — dont « python -m src.wipe_stores », qui vide "
    "le bucket qu'on lui donne. Renseigne S3_ENDPOINT dans le `.env`, puis "
    "recree les services (« docker compose up -d --force-recreate » : un "
    "restart ne relit pas le `.env`)."
)


class ReglagesDuStockageObjet(BaseSettings):
    """Les quatre variables du stockage objet, partagees par les deux reglages.

    Les identifiants n'ont pas de defaut non plus. Un client aux identifiants
    vides demarre, puis echoue en 403 a chaque televersement : un service qui
    ne recevrait pas ces variables perdrait les images sans erreur au
    demarrage (registre 4.28.b). Le bucket garde son defaut : c'est un nom de
    convention du depot, ni un secret ni une adresse.
    """

    # `validate_default=True` : sans lui, pydantic ne valide pas une valeur qui
    # vient du defaut, et un champ absent passerait sans declencher les
    # validateurs ci-dessous.
    s3_endpoint: str = Field(default="", validate_default=True)
    s3_access_key: str = Field(default="", validate_default=True)
    s3_secret_key: str = Field(default="", validate_default=True)
    s3_bucket: str = "documents"

    @field_validator("s3_endpoint")
    @classmethod
    def _exiger_une_adresse(cls, valeur: str) -> str:
        """Refuse une adresse absente ou vide, et dit quoi faire.

        Args:
            valeur: Ce que l'environnement a rendu, ou la chaine vide.

        Returns:
            L'adresse, inchangee.

        Raises:
            ValueError: Si l'adresse est absente ou vide. pydantic l'enveloppe
                dans une ``ValidationError`` qui porte ce message.
        """
        if not valeur.strip():
            raise ValueError(MESSAGE_ENDPOINT_MANQUANT)
        return valeur

    @field_validator("s3_access_key", "s3_secret_key")
    @classmethod
    def _exiger_des_identifiants(cls, valeur: str, info: ValidationInfo) -> str:
        """Refuse un identifiant absent ou vide, et nomme la variable en cause.

        Args:
            valeur: Ce que l'environnement a rendu, ou la chaine vide.
            info: Le contexte de validation, qui porte le nom du champ.

        Returns:
            L'identifiant, inchange.

        Raises:
            ValueError: Si l'identifiant est absent ou vide. Le message ne
                cite jamais la valeur, seulement le nom de la variable.
        """
        if not valeur.strip():
            variable = (info.field_name or "").upper()
            raise ValueError(MESSAGE_IDENTIFIANT_MANQUANT.format(variable=variable))
        return valeur
