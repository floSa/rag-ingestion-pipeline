"""Le controle d'identite ne doit dependre d'AUCUN arbre de travail.

Ce fichier garde une propriete qui a vecu deux jours sans garde, et dont la
perte est silencieuse : apres l'installation documentee, un commit portant une
adresse hors liste blanche doit etre refuse **meme dans un arbre de travail dont
`.pre-commit-config.yaml` ne declare pas le controle d'identite**. Sur les 111
commits de `main`, aucun ne le declare (``mesure``, 31 aout 2026) : tout
`git checkout` d'un commit ancien, tout `git bisect`, tout HEAD detache tombait
dans ce cas.

Le hook genere par `pre-commit` ouvre sa configuration en chemin RELATIF
(``--config=.pre-commit-config.yaml``). Un controle declare la-dedans a donc
change de nature en changeant de place : il est passe d'inconditionnel a
conditionnel a la branche, et rien ne l'a note. C'est la lecon « une regle
survit a son motif » appliquee a un fichier qui demenage.

La seule couche independante de l'arbre de travail est ``<type>.legacy``, que
`pre-commit install` cree quand un hook ecrit a la main est deja en place. C'est
ce que ``scripts/installer-les-garde-fous.sh`` monte, et c'est ce que ce fichier
verifie.

POURQUOI DES SOUS-PROCESSUS. Le sujet est le comportement de `git commit`, pas
celui d'une fonction Python : rien de ce qui est teste ici n'est importable. On
monte donc un depot git jetable, on y execute le script LIVRE, et on lit le code
de retour et l'etat de HEAD separement — un refus se prouve par les deux, jamais
par la sortie texte.

CE QUI REND CES TESTS NON CREUX. La configuration du depot d'essai est
``repos: []`` : elle ne porte pas le controle d'identite, exactement comme les
111 commits de `main`. Un refus observe ici ne peut donc pas venir d'elle. Et
``test_le_framework_tourne_aussi`` interdit la mutation qui rendrait les autres
verts pour la mauvaise raison — inverser l'ordre des deux gestes laisse le
controle d'identite en place et perd le framework.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[2]
INSTALLEUR = RACINE / "scripts" / "installer-les-garde-fous.sh"
HOOK_IDENTITE = RACINE / "scripts" / "git-hooks" / "pre-commit"

ADRESSE_INTERDITE = "florian.horellou@aosis.net"
ADRESSE_AUTORISEE = "florian.horellou@gmail.com"

# La configuration d'un arbre de travail qui NE PORTE PAS le controle
# d'identite. `repos: []` evite toute installation d'environnement : le test ne
# touche pas au reseau.
CONFIG_SANS_CONTROLE = "repos: []\n"


def _git(depot: Path, *arguments: str, env: dict[str, str] | None = None):
    """Execute git dans `depot` et rend le CompletedProcess, sans lever."""
    environnement = dict(os.environ)
    environnement.pop("GIT_DIR", None)
    environnement.pop("GIT_WORK_TREE", None)
    if env:
        environnement.update(env)
    return subprocess.run(
        ["git", *arguments],
        cwd=depot,
        env=environnement,
        capture_output=True,
        text=True,
    )


def _identite(adresse_auteur: str, adresse_committer: str) -> dict[str, str]:
    return {
        "GIT_AUTHOR_NAME": "floSa",
        "GIT_AUTHOR_EMAIL": adresse_auteur,
        "GIT_COMMITTER_NAME": "floSa",
        "GIT_COMMITTER_EMAIL": adresse_committer,
    }


def _monte_un_depot_jetable(
    depot: Path, contenu_installeur: str
) -> subprocess.CompletedProcess[str]:
    """Monte un depot git jetable et y execute `contenu_installeur`.

    Le depot recoit le hook d'identite LIVRE et une `.pre-commit-config.yaml`
    qui ne declare AUCUN controle d'identite : c'est l'etat des 111 commits de
    `main`. `repos: []` evite toute installation d'environnement de hook, donc
    ce test ne touche pas au reseau.
    """
    scripts = depot / "scripts"
    (scripts / "git-hooks").mkdir(parents=True)
    shutil.copy2(HOOK_IDENTITE, scripts / "git-hooks" / "pre-commit")
    installeur = scripts / INSTALLEUR.name
    installeur.write_text(contenu_installeur)

    (depot / ".pre-commit-config.yaml").write_text(CONFIG_SANS_CONTROLE)

    assert _git(depot, "init", "-b", "principale").returncode == 0
    assert _git(depot, "config", "user.name", "floSa").returncode == 0
    assert _git(depot, "config", "user.email", ADRESSE_AUTORISEE).returncode == 0
    assert _git(depot, "add", "-A").returncode == 0
    assert _git(depot, "commit", "-m", "initial").returncode == 0

    # `PRE_COMMIT` : le depot d'essai n'est pas un projet `uv`, donc le defaut
    # `uv run pre-commit` du script ne s'y applique pas. On nomme l'interpreteur
    # qui fait tourner ce test — le meme que celui de `uv run`, puisque c'est
    # lui qui a lance pytest.
    environnement = dict(os.environ)
    environnement["PRE_COMMIT"] = f"{sys.executable} -m pre_commit"
    return subprocess.run(
        ["sh", str(installeur)],
        cwd=depot,
        env=environnement,
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="module")
def depot_arme(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Un depot jetable, arme par le script LIVRE, sans le hook dans sa config.

    Portee module : le montage coute quelques secondes et aucun test ne le laisse
    modifie — chacun revoque ce qu'il a fait.
    """
    depot = tmp_path_factory.mktemp("depot-arme")
    execution = _monte_un_depot_jetable(depot, INSTALLEUR.read_text())
    assert execution.returncode == 0, (
        f"le script d'installation a echoue :\n{execution.stdout}\n{execution.stderr}"
    )

    # Le depot d'essai ne declare PAS le controle d'identite : tout refus
    # observe ensuite vient donc de la couche `.legacy`, pas de la config.
    assert "identite" not in (depot / ".pre-commit-config.yaml").read_text()

    return depot


class TestLaProtectionNeDependPasDeLArbreDeTravail:
    def test_une_adresse_hors_liste_blanche_est_refusee(self, depot_arme: Path):
        avant = _git(depot_arme, "rev-parse", "HEAD").stdout.strip()
        resultat = _git(
            depot_arme,
            "commit",
            "--allow-empty",
            "-m",
            "essai auteur et committer interdits",
            env=_identite(ADRESSE_INTERDITE, ADRESSE_INTERDITE),
        )
        apres = _git(depot_arme, "rev-parse", "HEAD").stdout.strip()

        assert resultat.returncode != 0, "le commit a ete accepte"
        assert apres == avant, f"HEAD a bouge : {avant} -> {apres}"

    def test_l_adresse_de_committer_seule_est_refusee(self, depot_arme: Path):
        # L'auteur est valide : c'est le cas que seul un controle portant sur les
        # DEUX identites voit. `git commit --author` le produit sans effort.
        avant = _git(depot_arme, "rev-parse", "HEAD").stdout.strip()
        resultat = _git(
            depot_arme,
            "commit",
            "--allow-empty",
            "-m",
            "essai committer interdit",
            env=_identite(ADRESSE_AUTORISEE, ADRESSE_INTERDITE),
        )
        apres = _git(depot_arme, "rev-parse", "HEAD").stdout.strip()

        assert resultat.returncode != 0, "le commit a ete accepte"
        assert apres == avant, f"HEAD a bouge : {avant} -> {apres}"

    def test_l_adresse_d_auteur_seule_est_refusee(self, depot_arme: Path):
        avant = _git(depot_arme, "rev-parse", "HEAD").stdout.strip()
        resultat = _git(
            depot_arme,
            "commit",
            "--allow-empty",
            "-m",
            "essai auteur interdit",
            env=_identite(ADRESSE_INTERDITE, ADRESSE_AUTORISEE),
        )
        apres = _git(depot_arme, "rev-parse", "HEAD").stdout.strip()

        assert resultat.returncode != 0, "le commit a ete accepte"
        assert apres == avant, f"HEAD a bouge : {avant} -> {apres}"

    def test_une_adresse_de_la_liste_blanche_passe(self, depot_arme: Path):
        # Sans ce test, tout ce qui precede serait vrai d'un hook qui refuse
        # TOUT — y compris le montage casse, qui echoue faute de trouver son
        # interpreteur.
        avant = _git(depot_arme, "rev-parse", "HEAD").stdout.strip()
        resultat = _git(
            depot_arme,
            "commit",
            "--allow-empty",
            "-m",
            "essai adresse autorisee",
            env=_identite(ADRESSE_AUTORISEE, ADRESSE_AUTORISEE),
        )
        apres = _git(depot_arme, "rev-parse", "HEAD").stdout.strip()

        assert resultat.returncode == 0, (
            f"le commit a ete refuse :\n{resultat.stdout}\n{resultat.stderr}"
        )
        assert apres != avant, "aucun commit n'a ete cree"
        _git(depot_arme, "reset", "--hard", avant)

    def test_le_framework_tourne_aussi(self, depot_arme: Path):
        """Interdit l'inversion des deux gestes du script d'installation.

        Copier le controle d'identite APRES `pre-commit install` laisse tous les
        tests ci-dessus VERTS — le script est bien en place — et perd
        silencieusement les hooks du framework. Ce test asserte donc depuis
        l'autre cote : une configuration dont un hook refuse tout doit refuser
        un commit portant une adresse autorisee.
        """
        config = depot_arme / ".pre-commit-config.yaml"
        original = config.read_text()
        config.write_text(
            "repos:\n"
            "  - repo: local\n"
            "    hooks:\n"
            "      - id: refuse-tout\n"
            "        name: refuse tout\n"
            "        entry: false\n"
            "        language: system\n"
            "        always_run: true\n"
            "        pass_filenames: false\n"
        )
        try:
            avant = _git(depot_arme, "rev-parse", "HEAD").stdout.strip()
            resultat = _git(
                depot_arme,
                "commit",
                "--allow-empty",
                "-m",
                "essai framework",
                env=_identite(ADRESSE_AUTORISEE, ADRESSE_AUTORISEE),
            )
            apres = _git(depot_arme, "rev-parse", "HEAD").stdout.strip()

            assert resultat.returncode != 0, (
                "le framework ne tourne pas : son hook « refuse tout » n'a pas arrete le commit"
            )
            assert apres == avant
        finally:
            config.write_text(original)


class TestLesCommitsDeFusionSontCouverts:
    """`git commit` n'est pas le seul chemin qui cree un commit.

    `pre-commit install` n'installe que le type `pre-commit`. Une fusion sans
    avance rapide declenche `pre-merge-commit`, et rien d'autre : `mesure` le
    31 aout 2026, mouchards poses sur chaque hook de `.git/hooks`, un
    `git merge --no-ff` fait tourner `pre-merge-commit`, `prepare-commit-msg` et
    `commit-msg`, jamais `pre-commit`.

    Ce n'etait pas une regression du lot 0b — le script brut avait le meme trou —
    mais le geste suivant du chantier est precisement `git merge --no-ff`, et ce
    commit-la part sur GitHub, ou la liste des contributeurs ne se defait pas.

    Le trou se ferme des DEUX cotes : le type est installe pour le framework, et
    la copie manuelle est posee sur `pre-merge-commit` comme sur `pre-commit`,
    pour que `pre-merge-commit.legacy` couvre les arbres dont la configuration ne
    porte pas le hook. Sans cette seconde moitie, la fusion serait gardee sur la
    branche du lot et nulle part ailleurs.
    """

    @staticmethod
    def _une_branche_a_fusionner(depot: Path, nom: str) -> None:
        """Cree une branche `nom` portant un fichier a elle, et revient.

        Le nom du fichier derive de celui de la branche : deux appels ne se
        marchent pas dessus. Un harnais non idempotent a rendu ces deux tests
        rouges pour la mauvaise raison avant que celui-ci ne soit ecrit — le
        second echouait sur « nothing to commit », pas sur son sujet.
        """
        depuis = _git(depot, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        assert _git(depot, "checkout", "-B", nom, depuis).returncode == 0
        (depot / f"{nom}.txt").write_text(f"apport de {nom}\n")
        assert _git(depot, "add", f"{nom}.txt").returncode == 0
        assert _git(depot, "commit", "-m", f"apport de {nom}").returncode == 0
        assert _git(depot, "checkout", depuis).returncode == 0

    def test_une_fusion_portant_une_adresse_interdite_est_refusee(self, depot_arme: Path):
        self._une_branche_a_fusionner(depot_arme, "fusion-interdite")
        avant = _git(depot_arme, "rev-parse", "HEAD").stdout.strip()
        resultat = _git(
            depot_arme,
            "merge",
            "--no-ff",
            "fusion-interdite",
            "-m",
            "merge interdit",
            env=_identite(ADRESSE_INTERDITE, ADRESSE_INTERDITE),
        )
        apres = _git(depot_arme, "rev-parse", "HEAD").stdout.strip()
        _git(depot_arme, "merge", "--abort")

        assert resultat.returncode != 0, "la fusion a ete acceptee"
        assert apres == avant, f"HEAD a bouge : {avant} -> {apres}"

    def test_une_fusion_portant_une_adresse_autorisee_passe(self, depot_arme: Path):
        # Le temoin. Sans lui, le test precedent serait vrai d'un montage qui
        # refuse TOUTE fusion — un `pre-merge-commit` casse, par exemple.
        self._une_branche_a_fusionner(depot_arme, "fusion-autorisee")
        avant = _git(depot_arme, "rev-parse", "HEAD").stdout.strip()
        resultat = _git(
            depot_arme,
            "merge",
            "--no-ff",
            "fusion-autorisee",
            "-m",
            "merge autorise",
            env=_identite(ADRESSE_AUTORISEE, ADRESSE_AUTORISEE),
        )
        apres = _git(depot_arme, "rev-parse", "HEAD").stdout.strip()

        assert resultat.returncode == 0, f"{resultat.stdout}\n{resultat.stderr}"
        assert apres != avant, "aucun commit de fusion n'a ete cree"
        _git(depot_arme, "reset", "--hard", avant)


class TestLeScriptConstateSonPropreResultat:
    """Le script doit ROUGIR quand le montage n'est pas celui qu'il annonce.

    C'est ce qui le distingue d'une consigne ecrite : une consigne suppose que
    le geste a ete fait dans le bon ordre, le script le CONSTATE. Sans ce test,
    le bloc de verification du script serait decoratif — on pourrait le vider
    sans qu'aucun test ne bronche, et l'installation redeviendrait une promesse.

    Le montage casse qu'on lui donne ici est celui que `pre-commit install`
    suggere lui-meme dans sa sortie — « Use -f to use only pre-commit. » — et
    qui supprime la seule couche independante de l'arbre de travail.
    """

    def test_un_installeur_qui_passe_moins_f_est_refuse(self, tmp_path: Path):
        depot = tmp_path / "depot-installeur-mute"
        mute = _monte_un_depot_jetable(
            depot,
            INSTALLEUR.read_text().replace(
                "$pre_commit install $arguments",
                "$pre_commit install -f $arguments",
            ),
        )

        assert mute.returncode != 0, (
            "un installeur passant -f a rendu 0 : la verification du script "
            f"est morte.\n{mute.stdout}\n{mute.stderr}"
        )
        assert "identite" in mute.stderr, f"le message ne nomme pas ce qui manque :\n{mute.stderr}"
        assert not (depot / ".git" / "hooks" / "pre-commit.legacy").exists()

    def test_un_installeur_dont_la_liste_de_types_est_vide_est_refuse(self, tmp_path: Path):
        """Une boucle sur une liste VIDE verifie zero chose, et elle est vraie.

        C'est la forme exacte du defaut que ce lot traque, dans le garde-fou de
        ce lot. La boucle de VERIFICATION du script itere la meme variable
        `TYPES` que la boucle d'ARMEMENT : videe, la premiere ne pose aucun
        `<type>.legacy`, la seconde n'a rien a verifier, et le script sort en 0
        en annoncant « Garde-fous armes dans ... » suivi d'une liste vide.

        `mesure` le 31 aout 2026, sur le script tel qu'il etait livre : `rc=0`,
        message de succes, et ZERO `.legacy` — donc la couche independante de
        l'arbre de travail, celle qui a coute au lot 0b sa fusion au premier
        tour, disparait EN SILENCE. Le framework, lui, reste installe : sans
        `--hook-type`, `pre-commit install` retombe sur
        `default_install_hook_types` de la configuration. Le montage a donc
        exactement l'air du bon, et c'est le pire des etats.

        Ce test asserte depuis le cote qui PRODUIT le defaut : on vide la liste
        dans le script LIVRE, et on exige que le script s'en apercoive.
        """
        source = INSTALLEUR.read_text()
        mutee = source.replace('TYPES="pre-commit pre-merge-commit"', 'TYPES=""')
        # Un test qui choisit lui-meme son cas doit prouver qu'il l'a atteint :
        # si la ligne `TYPES` change de forme, cette mutation ne mute plus rien
        # et le test resterait vert sans rien garder.
        assert mutee != source, "la ligne TYPES a change de forme : la mutation ne mute plus rien"

        depot = tmp_path / "depot-types-vides"
        mute = _monte_un_depot_jetable(depot, mutee)

        assert mute.returncode != 0, (
            "un installeur dont la liste de types est vide a rendu 0 : il "
            "annonce un montage qu'il n'a pas fait.\n"
            f"{mute.stdout}\n{mute.stderr}"
        )
        assert not (depot / ".git" / "hooks" / "pre-commit.legacy").exists()
        assert not (depot / ".git" / "hooks" / "pre-merge-commit.legacy").exists()

    def test_le_script_livre_passe_sur_le_meme_harnais(self, tmp_path: Path):
        # Le temoin du test precedent : sans lui, un `rc != 0` obtenu pour une
        # raison etrangere au -f (un chemin faux, un interpreteur absent) le
        # rendrait vert a tort.
        depot = tmp_path / "depot-installeur-livre"
        livre = _monte_un_depot_jetable(depot, INSTALLEUR.read_text())

        assert livre.returncode == 0, f"{livre.stdout}\n{livre.stderr}"
        assert (depot / ".git" / "hooks" / "pre-commit.legacy").exists()
