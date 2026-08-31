#!/bin/sh
# Arme les garde-fous de ce clone, ET VERIFIE QU'ILS SONT ARMES.
#
# Ce script est appele par `make install`. Il n'y a donc qu'un geste a taper, et
# ce geste constate son propre resultat : il sort en erreur si le montage n'est
# pas celui qu'il annonce.
#
# POURQUOI UN SCRIPT ET PAS DEUX LIGNES DE DOCUMENTATION
#
# Git n'execute jamais ce qui arrive avec un clone : il y a forcement UN geste
# local a faire, et aucune ecriture dans le depot ne peut s'en passer. Ce qui
# peut etre supprime, en revanche, c'est la MEMOIRE d'un ordre. Le montage
# ci-dessous ne tient que si deux gestes sont faits dans le bon sens, et
# l'inversion ne se voit pas : elle ne produit aucune erreur, seulement
# l'absence d'une protection. C'est le meme defaut que la cible `all` du
# Makefile a ferme, et la meme phrase s'applique — un garde-fou qui repose sur
# la memoire du suivant n'est pas un garde-fou.
#
# CE QUE CE SCRIPT MONTE, ET POURQUOI DANS CET ORDRE
#
#   1. le controle d'identite est copie a la main dans le repertoire des hooks ;
#   2. `pre-commit install`, SANS -f, deplace cette copie en `<type>.legacy`,
#      continue de l'executer AVANT ses propres hooks, et s'installe par-dessus.
#
# Inverse, l'ordre coute le framework : la copie manuelle ecrase le hook genere,
# et seul le controle d'identite subsiste.
#
# LA COUCHE `.legacy` N'EST PAS UN DOUBLON — C'EST LA PROTECTION
#
# Le hook genere par le framework ouvre sa configuration en chemin RELATIF
# (`--config=.pre-commit-config.yaml`) : un controle declare dans ce fichier ne
# vaut que pour les arbres de travail dont la configuration le porte. Sur les
# 111 commits de `main`, aucun ne la porte (`mesure`, 31 aout 2026). Un
# `git checkout` d'un commit ancien, un `git bisect`, un HEAD detache desarmaient
# donc le controle d'identite EN SILENCE — et c'est la famille de defaut qui a
# deja coute un depot entier. `<type>.legacy` vit HORS de l'arbre de travail :
# c'est la seule couche qui vaille pour tout commit, quelle que soit la branche.
#
# NE JAMAIS PASSER -f. `pre-commit install` le suggere lui-meme dans sa sortie
# — « Use -f to use only pre-commit. » — et c'est precisement le geste qui
# supprime cette couche. Ce script ne le passe pas, et la verification finale
# rougit si la couche a disparu.
set -eu

racine=$(git rev-parse --show-toplevel)
cd "$racine"

# `--git-common-dir` et non `--git-dir` : dans un arbre de travail secondaire,
# `--git-dir` rend `.git/worktrees/<nom>`, qui n'heberge aucun hook. Les hooks
# vivent dans le repertoire COMMUN, partage par le depot et tous ses arbres de
# travail — une installation vaut donc pour tous, et il n'y en a qu'une par
# clone.
commun=$(cd "$(git rev-parse --git-common-dir)" && pwd)
identite="$racine/scripts/git-hooks/pre-commit"

# Les types de hook qu'il faut armer. `pre-commit` NE SUFFIT PAS : c'est le seul
# type que `pre-commit install` installe par defaut, et il ne couvre pas les
# commits de fusion. `git merge --no-ff` declenche `pre-merge-commit`,
# `prepare-commit-msg` et `commit-msg`, jamais `pre-commit` (`mesure` le 31 aout
# 2026, mouchards poses sur chaque hook). Un commit de fusion portant une
# adresse interdite partait donc sans rien rencontrer — et le geste suivant du
# chantier est justement une fusion, dont le commit part sur GitHub.
#
# La copie manuelle est posee sur les DEUX types, pour que `<type>.legacy` couvre
# aussi les arbres dont la configuration ne porte pas le hook. Sans cette
# moitie, la fusion serait gardee sur la branche qui declare le hook, et nulle
# part ailleurs.
TYPES="pre-commit pre-merge-commit"

if [ ! -f "$identite" ]; then
    echo "ECHEC : $identite est introuvable." >&2
    exit 1
fi

mkdir -p "$commun/hooks"
for type in $TYPES; do
    cp "$identite" "$commun/hooks/$type"
    chmod +x "$commun/hooks/$type"
done

# `PRE_COMMIT` existe pour un seul appelant : le test qui verifie ce script
# (`tests/unit/test_installation_des_garde_fous.py`), qui monte un depot
# temporaire hors du projet `uv` et doit donc nommer l'interpreteur lui-meme.
# La valeur par defaut est celle du Makefile, et c'est elle que `make install`
# emprunte.
pre_commit="${PRE_COMMIT:-uv run pre-commit}"

arguments=""
for type in $TYPES; do
    arguments="$arguments --hook-type $type"
done

# Les types sont passes explicitement plutot que laisses a
# `default_install_hook_types` : cette cle vit dans `.pre-commit-config.yaml`,
# donc dans l'arbre de travail, et l'installation ne doit rien devoir a la
# branche sortie au moment ou on l'execute.
# shellcheck disable=SC2086
if ! $pre_commit install $arguments; then
    echo "ECHEC : « $pre_commit install » a rendu une erreur." >&2
    echo "Le controle d'identite est copie et actif ; les hooks du framework" >&2
    echo "ne le sont pas. Corrige la cause, puis relance : make install" >&2
    exit 1
fi

# La verification. C'est elle qui distingue ce script d'une consigne ecrite :
# elle constate le montage au lieu de le supposer.
erreurs=0
for type in $TYPES; do
    genere="$commun/hooks/$type"
    legacy="$commun/hooks/$type.legacy"

    if ! grep -q 'generated by pre-commit' "$genere" 2>/dev/null; then
        echo "ECHEC : $genere n'est pas le hook du framework." >&2
        echo "  Cause probable : la copie manuelle est passee APRES" >&2
        echo "  « pre-commit install » et l'a ecrase." >&2
        erreurs=1
    fi

    if ! cmp -s "$identite" "$legacy"; then
        echo "ECHEC : $legacy ne porte pas le controle d'identite." >&2
        echo "  Cause probable : « pre-commit install -f », qui supprime la" >&2
        echo "  seule couche independante de l'arbre de travail." >&2
        erreurs=1
    fi
done

if [ "$erreurs" -ne 0 ]; then
    echo "" >&2
    echo "Les garde-fous ne sont PAS armes. Ne commite pas avant d'avoir" >&2
    echo "corrige : le controle d'identite est celui dont l'oubli a coute un" >&2
    echo "depot entier (documentation/pilotage_du_chantier.md, §2.1)." >&2
    exit 1
fi

echo "Garde-fous armes dans $commun/hooks :"
for type in $TYPES; do
    echo "  $type          hooks du framework (.pre-commit-config.yaml)"
    echo "  $type.legacy   controle d'identite, valable pour toute branche"
done
