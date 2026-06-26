"""Runtime must load the embedding model from the local cache only.

model2vec's from_pretrained defaults force_download=True, which skips the
cache and fetches from the HF Hub on every call — a runtime-egress regression.
get_model must pin force_download=False so a cached model loads with no socket.
"""
from semantic_server import vector


def test_get_model_never_force_downloads(monkeypatch):
    captured = {}

    class _FakeModel:
        def encode(self, texts):
            return [[0.0]]

    def _fake_from_pretrained(name, *args, **kwargs):
        captured["force_download"] = kwargs.get("force_download")
        return _FakeModel()

    import model2vec
    monkeypatch.setattr(
        model2vec.StaticModel, "from_pretrained",
        staticmethod(_fake_from_pretrained),
    )
    monkeypatch.setattr(vector, "_model", None)

    vector.get_model()

    # force_download=False is the load-bearing guarantee: cached load, no fetch.
    assert captured["force_download"] is False
