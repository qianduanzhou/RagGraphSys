"""services.embedding_service.EmbeddingService 的测试（Embeddings 注入替身，无网络）。"""
from services.embedding_service import EmbeddingService
from tests.conftest import FakeEmbeddingsModel


def make_service(settings, **kw) -> EmbeddingService:
    return EmbeddingService(settings, embeddings=FakeEmbeddingsModel(**kw))


def test_embed_single(settings):
    svc = make_service(settings, dim=3)
    assert svc.embed("hello") == [0.0, 0.0, 0.0]


def test_embed_batch_returns_per_text_vector(settings):
    fake = FakeEmbeddingsModel(dim=2)
    svc = EmbeddingService(settings, embeddings=fake)
    out = svc.embed_batch(["a", "b", "c"])
    assert out == [[0.0, 0.0], [1.0, 1.0], [2.0, 2.0]]
    assert fake.embed_documents_calls == [["a", "b", "c"]]


def test_embed_batch_empty(settings):
    svc = make_service(settings)
    assert svc.embed_batch([]) == []


def test_embed_batch_preserves_order(settings):
    svc = make_service(settings, dim=1)
    assert svc.embed_batch(["x", "y", "z"]) == [[0.0], [1.0], [2.0]]


def test_dimension_from_settings(settings):
    svc = make_service(settings)
    assert svc.dimension == settings.embedding_dimension
