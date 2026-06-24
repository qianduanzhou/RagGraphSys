"""graph.build_graph 的测试：流式 e2e + 非流式反思路径。"""
import asyncio

from graph import build_graph
from tests.conftest import MockLLM, MockNeo4j, MockQdrant, MockRag


def test_streaming_pipeline_events_and_live_tokens(settings):
    """真实 graph.astream(stream_mode=["updates","custom"])：节点事件按序到达，
    token 作为 custom 事件在 merge 与 llm 之间实时产生。"""
    llm = MockLLM(stream_tokens=["答", "案"])
    rag = MockRag(
        MockQdrant(hits=[{"text": "v", "score": 0.9, "source": "d"}]),
        MockNeo4j(rels=[{"head": "X", "rel": "R", "tail": "Y"}]),
    )
    graph = build_graph(llm, rag, settings)

    async def drive():
        initial = {"question": "q", "history": [], "iterations": 0, "streaming": True}
        events = []
        async for mode, payload in graph.astream(initial, stream_mode=["updates", "custom"]):
            if mode == "updates":
                for node in payload:
                    events.append(("node", node))
            elif mode == "custom":
                # 节点写入的负载，形如 {"type": "delta", "text": "答"}
                events.append(("delta", payload.get("text")))
        return events

    events = asyncio.run(drive())
    nodes = [e[1] for e in events if e[0] == "node"]
    deltas = [e[1] for e in events if e[0] == "delta"]

    # qdrant/neo4j 并行执行 -> 相对顺序不确定，因此断言拓扑结构而非精确序列
    assert nodes[0] == "router_node"
    assert nodes[-1] == "llm_node"
    assert {"qdrant_node", "neo4j_node", "merge_node"}.issubset(nodes)
    assert nodes.index("merge_node") > nodes.index("qdrant_node")
    assert nodes.index("merge_node") > nodes.index("neo4j_node")
    assert nodes.index("llm_node") > nodes.index("merge_node")
    assert deltas == ["答", "案"], deltas
    assert "reflection_node" not in nodes  # 流式路径跳过反思

    find = lambda p: next(i for i, e in enumerate(events) if p(e))
    merge_i = find(lambda e: e == ("node", "merge_node"))
    llm_i = find(lambda e: e == ("node", "llm_node"))
    first_delta = find(lambda e: e[0] == "delta")
    # token 是实时的：严格位于 merge 完成与 llm 完成之间
    assert merge_i < first_delta < llm_i


def test_non_stream_runs_full_pipeline_with_reflection(settings):
    """非流式 invoke 走完 router->retrieve->merge->llm->reflection->END。"""
    llm = MockLLM(chat_resp="RAG")  # router 为 True，llm 答案为 "RAG"，反思通过
    graph = build_graph(llm, MockRag(), settings)

    res = graph.invoke({"question": "q", "history": [], "iterations": 0})
    assert res["answer"] == "RAG"
    assert res["needs_rag"] is True
    assert res["reflection_passed"] is True
    assert res["iterations"] >= 1
