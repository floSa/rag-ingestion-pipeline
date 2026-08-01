# Strategie d'evaluation RAG

## Objectif

Definir comment mesurer la qualite du systeme RAG une fois la couche LLM/agent
ajoutee. L'evaluation porte sur la pertinence du retrieval ET la fidelite des
reponses generees.

## Framework recommande

**Ragas** (https://docs.ragas.io) — framework open-source d'evaluation RAG.

## Metriques cibles

| Metrique            | Description                                           | Seuil cible |
|---------------------|-------------------------------------------------------|-------------|
| faithfulness        | La reponse est-elle fidele au contexte recupere ?     | >= 0.85     |
| context_precision   | Les chunks recuperes sont-ils pertinents ?            | >= 0.80     |
| context_recall      | Tous les elements necessaires sont-ils recuperes ?    | >= 0.75     |
| answer_relevancy    | La reponse repond-elle a la question ?                | >= 0.85     |
| answer_correctness  | La reponse est-elle factuellement correcte ?          | >= 0.80     |

## Jeu de donnees golden

Creer un jeu de 50-100 paires (question, reponse_attendue, contexte_source) a
partir des documents deja ingeres :

1. Selectionner 10-15 documents couvrant differents types (PDF technique, HTML cours)
2. Ecrire 5-7 questions par document avec les reponses attendues
3. Annoter les chunks sources pertinents (IDs ChromaDB)
4. Stocker dans `tests/fixtures/golden_qa.json`

> **Attention aux identifiants.** Les ids de chunk derivent du texte extrait :
> toute evolution de la chaine d'extraction les change. Un jeu golden annote
> par ids devient caduc a la premiere modification du pipeline. Preferer
> annoter par `filename` + extrait de texte attendu, et ne resoudre les ids
> qu'au moment de l'evaluation.

> **Point de comparaison.** `context_precision` est la metrique que le
> nettoyage de l'index fait bouger le plus : avant regroupement, 36 % des
> chunks recuperables etaient des fragments de mise en page (`x`, `and`, `-`).
> Toute mesure anterieure a ce changement n'est pas comparable aux suivantes.

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

- Executer l'evaluation apres chaque changement du retrieval ou des prompts
- Comparer les scores avec la baseline precedente
- Alerter si une metrique passe sous le seuil

## Metriques complementaires (hors Ragas)

- **Latence P95** du retrieval (ChromaDB query + reranking)
- **Tokens consommes** par requete (cout LLM)
- **Taux de hallucination** (reponses non supportees par le contexte)
