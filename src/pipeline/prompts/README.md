# Convention Prompts

Ce dossier est reserve aux templates de prompts d'un agent RAG. Il ne contient
aujourd'hui aucun template : la couche agent vit dans le projet
`rag-agent-chat`, et ce pipeline n'appelle aucun LLM.

## Regles

- Un fichier par prompt : `{nom_du_prompt}.txt` ou `.j2` (Jinja2).
- Aucun prompt inline dans le code Python.
- Variables entre accolades : `{context}`, `{question}`, `{history}`.
- Documenter les variables attendues en commentaire en tete de fichier.

## Structure envisagee

```
prompts/
    system.txt              # Prompt systeme de l'agent
    answer_with_context.j2  # Generation de reponse avec contexte
    summarize.j2            # Resume de document
    extract_entities.j2     # Extraction d'entites pour le graphe
```
