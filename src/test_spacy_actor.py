import ray

from src.actors.spacy_actor import SpacyActor

ray.init()

actor = SpacyActor.remote()

entities = ray.get(
    actor.extract.remote(
        "Floods in the Prut River basin in Ukraine."
    )
)

print(entities)