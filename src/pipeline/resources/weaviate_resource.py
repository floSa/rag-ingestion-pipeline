from dagster import ConfigurableResource
import weaviate
from pydantic import Field

class WeaviateResource(ConfigurableResource):
    host: str = Field(default="localhost", description="Weaviate host")
    port: int = Field(default=8080, description="Weaviate HTTP port")
    grpc_port: int = Field(default=50051, description="Weaviate GRPC port")

    def get_client(self) -> weaviate.WeaviateClient:
        if self.host == "localhost" or self.host == "127.0.0.1":
            return weaviate.connect_to_local(port=self.port, grpc_port=self.grpc_port)
        else:
            return weaviate.connect_to_custom(
                http_host=self.host,
                http_port=self.port,
                http_secure=False,
                grpc_host=self.host,
                grpc_port=self.grpc_port,
                grpc_secure=False,
            )
