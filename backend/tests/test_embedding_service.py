"""services.embedding_service.EmbeddingService 的测试（Embeddings 注入替身，无网络）。"""
from core.config import Settings
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


def _patch_openai_embeddings(monkeypatch, captured: dict) -> None:
    """把 EmbeddingService 内的 OpenAIEmbeddings 换成记录初始化参数的替身（无网络）。"""

    class _Spy:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def embed_query(self, _text):
            return [0.0]

        def embed_documents(self, texts):
            return [[0.0] for _ in texts]

    monkeypatch.setattr("services.embedding_service.OpenAIEmbeddings", _Spy)


def test_real_init_uses_dedicated_base_url_and_falls_back_to_llm_key(monkeypatch):
    """embedding 走独立 base_url；EMBEDDING_API_KEY 留空时回退到 LLM_API_KEY。"""
    captured: dict = {}
    _patch_openai_embeddings(monkeypatch, captured)

    settings = Settings(
        llm_api_key="llm-key",
        embedding_api_key="",
        embedding_base_url="https://embed.example.com/v1",
    )
    EmbeddingService(settings)  # 不注入 embeddings -> 走真实初始化分支

    assert captured["base_url"] == "https://embed.example.com/v1"
    assert captured["api_key"] == "llm-key"
    assert captured["model"] == settings.embedding_model


def test_real_init_prefers_dedicated_embedding_api_key(monkeypatch):
    """EMBEDDING_API_KEY 非空时优先于 LLM_API_KEY（不回退）。"""
    captured: dict = {}
    _patch_openai_embeddings(monkeypatch, captured)

    settings = Settings(
        llm_api_key="llm-key",
        embedding_api_key="embed-key",
        embedding_base_url="https://embed.example.com/v1",
    )
    EmbeddingService(settings)

    assert captured["api_key"] == "embed-key"
