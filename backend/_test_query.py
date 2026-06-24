import json
import urllib.request

# 直接发 UTF-8 中文 body，避免 shell 编码问题
data = json.dumps(
    {"message": "消息结构的核心是什么", "history": []}, ensure_ascii=False
).encode("utf-8")
req = urllib.request.Request(
    "http://127.0.0.1:8000/api/chat/stream",
    data=data,
    headers={"Content-Type": "application/json; charset=utf-8"},
    method="POST",
)
chunks = []
with urllib.request.urlopen(req, timeout=90) as resp:
    for raw in resp:
        chunks.append(raw.decode("utf-8"))
text = "".join(chunks)
with open(r"D:\project\customer\AI\RagGraphSys\backend\_resp.txt", "w", encoding="utf-8") as f:
    f.write(text)
print("OK", len(text), "chars")
