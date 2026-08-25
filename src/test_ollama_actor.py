import ray

from src.actors.ollama_actor import OllamaActor

ray.init()

actor = OllamaActor.remote()

candidate = {
    "paper_id": "test_001",
    "title": "Flood mapping using Sentinel-1 SAR in Ukraine",
    "study_type": "case_study",
    "study_country": "Ukraine",
    "task": "flood_mapping_satellite"
}

sections = {
    "title": "Flood mapping using Sentinel-1 SAR in Ukraine",
    "abstract": "This study applies Sentinel-1 SAR data for flood extent mapping in Ukraine.",
    "methods": "Sentinel-1 SAR images were used to extract flood water extent.",
    "study_area": "The study area is located in Ukraine.",
    "data_sources": "Sentinel-1 SAR imagery."
}

result = ray.get(
    actor.judge.remote(candidate, sections)
)

print(result)