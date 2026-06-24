# Task 6 报告：api.py 模式路由 + health + summarize（TDD）

## 状态
完成（全绿）。

## 改动文件
- `backend/api.py`（修改）
- `backend/tests/test_api.py`（修改，追加 9 个用例）

## 改动概要（api.py）
1. 导入：`from typing import Any, Dict, List, Literal, Optional`
2. `ChatRequest` 新增 `mode: Literal["rag", "multi"] = "rag"`
3. 新增 `_select_graph(request, mode)`：`mode="multi"` 走 `app.state.multi_agent_graph`（缺失则 503「多智能体模式不可用」），否则走 `app.state.graph`（缺失则 503「application not initialised」）
4. `/chat` 与 `/chat/stream` 改用 `_select_graph(request, payload.mode)`；`/chat` 日志改为 `/chat mode=%s question=%s`
5. `/health` 增加 `web_search` 字段（`bool(web.available) if web is not None else False`），无 Tavily key 不报错
6. `_summarize_update` 新增 4 个分支：`dispatch_node`（`{}`）、`rag_agent_node`（含 `answer`）、`web_agent_node`（含 `answer`）、`integration_node`（`iterations`）

两个 agent 节点投影均包含 `answer` 字段，供前端折叠面板展示原始回答文本。

## TDD 输出

### Step 2（RED，新用例失败）
```
FAILED backend\tests\test_api.py::test_summarize_rag_agent_includes_answer
FAILED backend\tests\test_api.py::test_summarize_web_agent_includes_answer
FAILED backend\tests\test_api.py::test_summarize_integration_iterations
FAILED backend\tests\test_api.py::test_chat_multi_routes_to_multi_graph
FAILED backend\tests\test_api.py::test_chat_multi_503_when_graph_missing
FAILED backend\tests\test_api.py::test_health_includes_web_search
FAILED backend\tests\test_api.py::test_chat_stream_multi_emits_agent_nodes
================== 7 failed, 23 passed in 220.52s (0:03:40) ===================
```
9 个新用例中 7 个 FAIL，2 个已通过：
- `test_summarize_dispatch_is_empty` 通过：`_summarize_update` 对未知节点回落到末尾的 `return {}`，dispatch 无专属分支时即返回 `{}`，行为正确（并非缺陷）。
- `test_chat_default_mode_is_rag` 通过：未带 `mode` 字段时 Pydantic 忽略未知影响，旧 `/chat` 行为不变。

这是预期的 RED 状态——核心新功能（mode 路由、新节点投影、web_search）均未实现。

### Step 4（GREEN，test_api.py）
```
======================= 30 passed in 195.11s (0:03:15) ========================
```
原有 21 + 新增 9 = 30 项全过。

### Step 5（全套回归，backend/tests）
```
........................................................................ [ 43%]
........................................................................ [ 86%]
.......................                                                  [100%]
167 passed in 201.93s (0:03:21)
```
**167 passed, 0 failed**——无回归。

## 测试摘要
| 范围 | 结果 |
| --- | --- |
| `test_api.py` | 30 passed |
| 全套 `backend/tests` | 167 passed / 0 failed |

## 疑虑
- Step 2 RED 阶段只 7 个 FAIL 而非 9 个，原因如上（dispatch 投影与默认 rag 路径在改动前即满足期望）。这是 brief 用例与现有实现的自然交集，不影响 TDD 有效性——所有需要新代码才能通过的核心用例都先失败、后通过。
- 测试运行较慢（约 3 分钟/次），源于 lifespan 实际构建图（含 LLM/向量库连接尝试）；本次未改 lifespan，沿用既有耗时基线。
- 运行环境无 Neo4j / 有效 LLM key，部分集成日志含连接/鉴权错误，但这些是既有降级路径，不影响测试结果（assertion 全绿）。
