from dagster import ConfigurableResource

class EmbeddingsResource(ConfigurableResource):
    """Ressource Dagster pour charger le modèle d'embeddings une seule fois."""
    model_name: str = "all-MiniLM-L6-v2"
    
    def get_model(self):
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer(self.model_name)
