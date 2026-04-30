# Plan d'integration LLM / Agent RAG

## Etat actuel

Le pipeline d'ingestion est complet : documents -> extraction structuree ->
graphe de connaissances (NebulaGraph) + base vectorielle (ChromaDB).
Il n'y a pas encore de couche LLM ni d'agent conversationnel.

## Architecture cible

```
Utilisateur
    |
    v
[Agent RAG (LangGraph)]
    |
    +---> [Retriever] ---> ChromaDB (recherche semantique)
    |                  \--> NebulaGraph (navigation graphe)
    |
    +---> [LLM] ---> Generation de reponse avec contexte
    |
    +---> [Guardrails] ---> Filtrage PII / validation output
```

## Point d'insertion

Le code agent sera dans un nouveau package `src/agent/` :

```
src/agent/
    __init__.py
    graph.py          # LangGraph state machine
    retriever.py      # Retrieval multi-source (Chroma + Nebula)
    prompts/          # Templates de prompts
    settings.py       # Config agent (model, temperature, etc.)
```

## Separation agent-service

- L'agent ne partage PAS de code avec le pipeline d'ingestion
- Communication via les APIs des stores (ChromaDB HTTP, NebulaGraph nGQL)
- Les schemas Pydantic de `src/pipeline/schemas.py` peuvent etre reutilises
  pour typer les elements recuperes

## Retrieval multi-source

1. **ChromaDB** : recherche semantique par similarite cosinus
2. **NebulaGraph** : navigation parent/enfant pour enrichir le contexte
   (ex: recuperer la section parente d'un paragraphe trouve)
3. **MinIO** : recuperation des images/tables pour le contexte multimodal

## Convention prompts

Voir `src/pipeline/prompts/README.md`. Chaque prompt est un fichier
`.txt` ou `.j2` (Jinja2), jamais inline dans le code Python.

## Choix technologiques recommandes

| Composant     | Option recommandee        | Raison                           |
|---------------|---------------------------|----------------------------------|
| Framework     | LangGraph                 | Workflows stateful, debugging    |
| LLM           | Claude / GPT-4            | Qualite RAG, long context        |
| Reranking     | Cohere / cross-encoder    | Ameliorer precision retrieval    |
| Guardrails    | NeMo Guardrails / Presidio| PII, hallucination control       |
| Observabilite | LangSmith / Langfuse      | Traces, latence, cout par query  |

## Etapes d'implementation

1. Creer `src/agent/` avec retriever basique (ChromaDB only)
2. Ajouter le retrieval NebulaGraph (navigation graphe)
3. Integrer un LLM avec prompt template
4. Ajouter le reranking
5. Implementer les guardrails
6. Evaluer avec le jeu golden (voir `rag_evaluation_strategy.md`)
