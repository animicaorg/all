"""register_adapter / register_embedding_adapter (7.1.1 P2 mesh hook)."""

from animica.ena import providers as P
from animica.ena.models import ModelProviderConfig


def test_register_adapter_picked_up_by_builder():
    class MyModel(P.ModelAdapter):
        name = "mymodel"

        def generate(self, prompt, *, system=None, history=None, max_tokens=None,
                     temperature=None, seed=None):
            return "custom:" + prompt

    P.register_adapter("mymodel", MyModel)
    cfg = ModelProviderConfig(name="x", provider="mymodel", model="m")
    adapter = P.build_model_adapter(cfg)
    assert isinstance(adapter, MyModel)
    assert adapter.generate("hi") == "custom:hi"


def test_unknown_provider_falls_back_to_deterministic():
    cfg = ModelProviderConfig(name="x", provider="does-not-exist", model="m")
    adapter = P.build_model_adapter(cfg)
    assert isinstance(adapter, P.DeterministicModel)


def test_register_embedding_adapter():
    class MyEmb(P.EmbeddingAdapter):
        name = "myemb"

        def embed(self, texts):
            return [[float(len(t))] for t in texts]

    P.register_embedding_adapter("myemb", MyEmb)
    from animica.ena.models import EmbeddingProviderConfig
    cfg = EmbeddingProviderConfig(name="x", provider="myemb", model="m")
    adapter = P.build_embedding_adapter(cfg)
    assert adapter.embed(["abc"]) == [[3.0]]
