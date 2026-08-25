import ray

from src.actors.embedding_actor import EmbeddingActor

ray.init()

actor = EmbeddingActor.remote()

result = ray.get(
    actor.encode.remote(
        ["GeoHydroAI flood mapping"]
    )
)

print(len(result[0]))