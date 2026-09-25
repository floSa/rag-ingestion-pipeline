# Strategie d'evaluation RAG

> **Statut du document : plan.** Cette strategie decrit l'evaluation de bout en
> bout du systeme RAG (retrieval, reranking, generation). Elle n'est pas mise en
> oeuvre dans ce depot : la generation vit dans `rag-agent-chat`, et ce pipeline
> n'appelle aucun LLM.
>
> Ce qui existe aujourd'hui dans ce depot est une mesure du **rappel dense
> seul** : le jeu de questions
> `documentation/campagnes/2026-09-02-jeu-de-questions.yaml` (30 questions) et
> l'instrument `scripts/campagne/mesurer-le-rappel-vectoriel.py`, dont
> l'en-tete donne la commande (`docker compose run --rm --no-deps …`). Les
> campagnes mesurees sont consignees dans `documentation/campagnes/`.

## Objectif

Mesurer la qualite du systeme RAG complet, couche agent comprise. L'evaluation
porte sur la pertinence du retrieval **et** la fidelite des reponses generees.

## Framework recommande

**Ragas** (https://docs.ragas.io), framework open-source d'evaluation RAG.

## Metriques cibles

| Metrique            | Description                                           | Seuil cible |
|---------------------|-------------------------------------------------------|-------------|
| faithfulness        | La reponse est-elle fidele au contexte recupere ?     | >= 0.85     |
| context_precision   | Les chunks recuperes sont-ils pertinents ?            | >= 0.80     |
| context_recall      | Tous les elements necessaires sont-ils recuperes ?    | >= 0.75     |
| answer_relevancy    | La reponse repond-elle a la question ?                | >= 0.85     |
| answer_correctness  | La reponse est-elle factuellement correcte ?          | >= 0.80     |

## Jeu de donnees de reference (golden)

Constituer 50 a 100 triplets (question, reponse_attendue, contexte_source) a
partir des documents deja ingeres :

1. Selectionner 10 a 15 documents couvrant differents types (PDF technique,
   HTML de cours, notes Markdown).
2. Ecrire 5 a 7 questions par document, avec les reponses attendues.
3. Annoter les passages sources pertinents.
4. Versionner le jeu sous `documentation/campagnes/`, a cote du jeu de
   questions existant.

> **Attention aux identifiants.** Les ids de chunk derivent du texte extrait :
> toute evolution de la chaine d'extraction les change. Un jeu annote par ids
> devient caduc a la premiere modification du pipeline. Preferer annoter par
> `source_path` + extrait de texte attendu, et ne resoudre les ids qu'au moment
> de l'evaluation.

> **Point de comparaison.** `context_precision` est la metrique la plus
> sensible au nettoyage de l'index : avant le regroupement des fragments, 36 %
> des chunks recuperables etaient des fragments de mise en page (`x`, `and`,
> `-`). Une mesure anterieure a ce changement n'est pas comparable aux suivantes.

## Pipeline d'evaluation

```python
from ragas import evaluate
from ragas.metrics import (
    faithfulness,
    context_precision,
    context_recall,
    answer_relevancy,
)

result = evaluate(
    dataset=golden_dataset,
    metrics=[faithfulness, context_precision, context_recall, answer_relevancy],
)
```

## Integration continue

- Executer l'evaluation apres chaque changement du retrieval ou des prompts.
- Comparer les scores avec la baseline precedente.
- Alerter si une metrique passe sous le seuil.

## Metriques complementaires (hors Ragas)

- **Latence P95** du retrieval (requete ChromaDB + reranking).
- **Tokens consommes** par requete (cout LLM).
- **Taux d'hallucination** (reponses non supportees par le contexte).
