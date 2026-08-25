import ray
import spacy

from src.config.settings import SPACY_MODEL


@ray.remote(max_restarts=1)
class SpacyActor:

    def __init__(self):
        self.nlp = spacy.load(SPACY_MODEL)

    def extract(self, text):

        doc = self.nlp(text)

        return [
            {
                "text": ent.text,
                "label": ent.label_
            }
            for ent in doc.ents
        ]
