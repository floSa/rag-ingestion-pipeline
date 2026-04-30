# Convention Prompts

Ce dossier contient les templates de prompts pour le futur agent RAG.

## Regles

- Un fichier par prompt : `{nom_du_prompt}.txt` ou `.j2` (Jinja2)
- Jamais de prompt inline dans le code Python
- Variables entre accolades : `{context}`, `{question}`, `{history}`
- Documenter les variables attendues en commentaire en tete de fichier

## Structure attendue

```
prompts/
    system.txt              # Prompt systeme de l'agent
    answer_with_context.j2  # Generation de reponse avec contexte
    summarize.j2            # Resume de document
    extract_entities.j2     # Extraction d'entites pour le graphe
```
