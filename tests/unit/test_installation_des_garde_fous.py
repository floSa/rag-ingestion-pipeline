"""Le controle d'identite ne depend d'aucun arbre de travail.

Apres ``scripts/installer-les-garde-fous.sh``, un commit portant une adresse
hors liste blanche doit etre refuse meme dans un arbre de travail dont
`.pre-commit-config.yaml` ne declare pas le controle d'identite : apres un
`git checkout` d'un commit ancien, un `git bisect` ou un HEAD detache
(``mesure`` le 31 aout 2026 : aucun des 111 commits de `main` d'alors ne le
declarait).

Le hook genere par `pre-commit` lit sa configuration en chemin relatif
(``--config=.pre-commit-config.yaml``), donc dans l'arbre de travail. La copie
``<type>.legacy``, que `pre-commit install` cree quand un hook ecrit a la main
est deja en place, vit hors de l'arbre de travail : c'est elle que ce fichier
verifie.

Les tests passent par des sous-processus : le sujet est le comportement de
`git commit`. Chaque test monte un depot git jetable, y execute le script
livre, et lit separement le code de retour et l'etat de HEAD (jamais la seule
sortie texte).

La configuration du depot d'essai est ``repos: []`` : elle ne porte pas le
controle d'identite, donc un refus observe vient de la copie `.legacy`.
``test_le_framework_tourne_aussi`` verifie en plus que les hooks du framework
tournent : inverser l'ordre des deux etapes du script les perdrait.
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

# La configuration d'un arbre de travail qui ne porte pas le controle
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

    Le depot recoit le hook d'identite livre et une `.pre-commit-config.yaml`
    qui ne declare aucun controle d'identite. `repos: []` evite toute
    installation d'environnement de hook, donc ce test ne touche pas au reseau.
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
    # `uv run pre-commit` du script ne s'y applique pas. L'interpreteur de ce
    # test est nomme a la place : c'est celui qu'a lance `uv run`.
    environnement = dict(os.environ)
    # Comme dans `_git()`. C'est ici le seul sous-processus qui ecrit des
    # hooks : un `GIT_DIR` herite ferait installer les hooks dans le depot
    # qu'il designe, peut-etre le depot reel (`mesure` : quatre fichiers y
    # etaient ecrits).
    environnement.pop("GIT_DIR", None)
    environnement.pop("GIT_WORK_TREE", None)
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
    """Un depot jetable, installe par le script livre, sans le hook dans sa config.

    Portee module : le montage coute quelques secondes, et chaque test defait ce
    qu'il a modifie.
    """
    depot = tmp_path_factory.mktemp("depot-arme")
    execution = _monte_un_depot_jetable(depot, INSTALLEUR.read_text())
    assert execution.returncode == 0, (
        f"le script d'installation a echoue :\n{execution.stdout}\n{execution.stderr}"
    )

    # Le depot d'essai ne declare pas le controle d'identite : tout refus
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
        # L'auteur est valide : seul un controle portant sur les deux identites
        # (auteur et committer) voit ce cas. `git commit --author` le produit.
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
        # Une adresse autorisee passe. Sans ce test, les precedents seraient
        # vrais d'un hook qui refuse tout, y compris d'un montage casse qui
        # echoue faute de trouver son interpreteur.
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
        """Les hooks du framework tournent aussi (ordre des deux etapes).

        Copier le controle d'identite apres `pre-commit install` laisserait les
        tests precedents passer et perdrait les hooks du framework. Ici, une
        configuration dont un hook refuse tout doit refuser un commit portant
        une adresse autorisee.
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

    Par defaut, `pre-commit install` n'installe que le type `pre-commit`. Or un
    `git merge --no-ff` declenche `pre-merge-commit`, `prepare-commit-msg` et
    `commit-msg`, jamais `pre-commit` (`mesure` le 31 aout 2026).

    Le script installe donc aussi `pre-merge-commit` pour le framework, et copie
    le controle d'identite sur ce type : `pre-merge-commit.legacy` couvre les
    arbres dont la configuration ne porte pas le hook.
    """

    @staticmethod
    def _une_branche_a_fusionner(depot: Path, nom: str) -> None:
        """Cree une branche `nom` portant un fichier a elle, et revient.

        Le nom du fichier derive de celui de la branche : deux appels
        n'entrent pas en conflit (sinon le second echouerait sur « nothing to
        commit »).
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
        # Une fusion autorisee passe. Sans ce cas, le test precedent serait vrai
        # d'un montage qui refuse toute fusion (un `pre-merge-commit` casse).
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


class TestLeHarnaisResteDansSonBacASable:
    """Les hooks ne sont ecrits que dans le depot jetable.

    `_git()` et le sous-processus de l'installeur retirent `GIT_DIR` et
    `GIT_WORK_TREE` de l'environnement. Sinon, avec un `GIT_DIR` dans
    l'environnement de pytest, l'installeur resoudrait `--git-common-dir` sur
    le depot designe et y ecrirait quatre fichiers (`pre-commit`,
    `pre-commit.legacy`, `pre-merge-commit`, `pre-merge-commit.legacy`,
    `mesure` le 31 aout 2026).

    Le test designe un depot par `GIT_DIR`, lance le harnais, et exige que ce
    depot reste intact.
    """

    def test_git_dir_dans_l_environnement_ne_deporte_pas_les_hooks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        designe = tmp_path / "depot-designe"
        designe.mkdir()
        assert _git(designe, "init", "-q", "-b", "principale", ".").returncode == 0
        hooks_designes = designe / ".git" / "hooks"
        monkeypatch.setenv("GIT_DIR", str(designe / ".git"))

        depot = tmp_path / "depot-sous-git-dir"
        execution = _monte_un_depot_jetable(depot, INSTALLEUR.read_text())

        poses = sorted(
            fichier.name
            for fichier in hooks_designes.iterdir()
            if not fichier.name.endswith(".sample")
        )
        assert not poses, (
            f"le harnais a arme le depot designe par GIT_DIR au lieu du sien : {poses}"
        )
        # Le harnais a bien installe les hooks dans le depot jetable. Sans ce
        # cas, l'assertion precedente serait vraie d'un harnais qui n'installe
        # rien (chemin faux, interpreteur absent).
        assert execution.returncode == 0, f"{execution.stdout}\n{execution.stderr}"
        assert (depot / ".git" / "hooks" / "pre-commit.legacy").exists(), (
            "le harnais n'a arme aucun hook dans son propre bac a sable"
        )


class TestLeScriptConstateSonPropreResultat:
    """Le script echoue quand l'installation n'est pas celle attendue.

    Ces tests couvrent le bloc de verification du script : sans eux, on
    pourrait le vider sans qu'aucun test n'echoue.

    Le premier cas est celui que `pre-commit install` suggere dans sa sortie
    (« Use -f to use only pre-commit. ») : -f supprime la copie `.legacy`.
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
        """Une liste `TYPES` vide fait echouer le script.

        La boucle de verification itere la meme variable `TYPES` que la boucle
        d'installation : vide, aucune copie `.legacy` n'est posee, rien n'est
        verifie, et le script sortirait en 0 (`mesure` le 31 aout 2026 avant le
        refus explicite). Le framework resterait installe (sans `--hook-type`,
        `pre-commit install` retombe sur `default_install_hook_types`), ce qui
        masquerait l'absence.

        Le test vide la liste dans le script livre et exige un echec.
        """
        source = INSTALLEUR.read_text()
        mutee = source.replace('TYPES="pre-commit pre-merge-commit"', 'TYPES=""')
        # Si la ligne `TYPES` change de forme, ce remplacement ne modifie plus
        # rien et le test passerait sans rien verifier.
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
        # Le script livre passe sur le meme harnais. Sans ce cas, un `rc != 0`
        # obtenu pour une autre raison que -f (chemin faux, interpreteur absent)
        # ferait passer le test precedent a tort.
        depot = tmp_path / "depot-installeur-livre"
        livre = _monte_un_depot_jetable(depot, INSTALLEUR.read_text())

        assert livre.returncode == 0, f"{livre.stdout}\n{livre.stderr}"
        assert (depot / ".git" / "hooks" / "pre-commit.legacy").exists()
