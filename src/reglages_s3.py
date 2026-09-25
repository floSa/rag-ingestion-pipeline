"""Les reglages du stockage objet, et le SEUL site qui les declare.

DEUX classes de reglages decident du MEME objet, et c'est le registre 4.29.b :
le pipeline Dagster TELEVERSE (``src/pipeline/settings.py``), le service
d'extraction PUBLIE l'adresse (``src/docling_service/settings.py``). Les quatre
champs etaient ECRITS DEUX FOIS, avec les memes defauts, et leur accord ne
tenait qu'a deux tests. Un bucket qui derive d'un cote produit un objet qui
existe et une adresse qui rend 404, sans aucune erreur nulle part. Les deux
classes heritent desormais de celle-ci : l'accord n'est plus verifie, il est
STRUCTUREL.

**``S3_ENDPOINT`` N'A AUCUNE VALEUR PAR DEFAUT, ET C'EST LE POINT DU LOT.**
Elle valait l'adresse du stockage d'alors, ecrite en dur a ces deux sites-la.
Un poste dont le `.env` ne declarait pas la variable parlait donc au stockage
nomme dans le code sans l'avoir choisi — y compris ``python -m src.wipe_stores``, qui VIDE le bucket
qu'on lui designe. Tant que ce defaut a designe le stockage en service, il
n'etait qu'un raccourci ; le 25 septembre 2026 il a cesse de l'etre, et un
raccourci qui survit au lieu qu'il designait mene au MAUVAIS stockage.

*Un defaut absent fait echouer le demarrage ; un defaut faux fait REUSSIR la
purge du mauvais stockage.* C'est toute la difference, et elle ne se rattrape
pas : la purge ne previent pas, elle rend compte.

Les noms sont GENERIQUES — ``S3_*`` — et ils ne nomment pas plus le successeur
que le predecesseur. Ce qui parle ici est un protocole, pas un produit ; le
produit se lit dans la valeur de ``S3_ENDPOINT``, qui est le seul endroit ou
il ait sa place.
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

    Les identifiants n'ont AUCUN defaut non plus. Ils valaient la chaine vide,
    au motif qu'un client sans identifiants echoue a l'appel ; mais il echoue
    TARD, en 403, un televersement apres l'autre, et c'est ainsi que
    `dagster-webserver` et `dagster-daemon` — qui ne recevaient pas ces deux
    variables — auraient perdu les images HTML sans une erreur au demarrage
    (livraison §4.28.b). Le bucket, lui, garde son defaut : c'est un nom de
    convention du depot, pas un secret et pas une adresse.
    """

    # `validate_default=True` : sans lui, pydantic ne valide PAS une valeur qui
    # vient du defaut, et le champ absent passerait le validateur ci-dessous
    # sans le declencher. Le champ serait alors « sans defaut » de nom et
    # « defaut vide » de fait — exactement ce que ce lot ferme.
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
