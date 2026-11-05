# -*- coding: utf-8 -*-
"""
冒烟测试：验证 RAG 系统核心链路。
覆盖：健康检查 -> 分类/知识库总览 -> 设置读写 -> 上传 -> 列表/切片 -> 问答 SSE -> 会话 -> 修改分类 -> 删除。
用法：先启动后端，再运行 python tests/smoke_test.py
"""
import json
import os
import sys
import urllib.request

BASE = os.getenv("RAG_BASE_URL", "http://127.0.0.1:8080")
SAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_handbook.md")


def call(path, method="GET", data=None, headers=None, raw=False):
    req = urllib.request.Request(BASE + path, method=method)
    body = None
    if data is not None:
        if isinstance(data, dict):
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            req.add_header("Content-Type", "application/json")
        else:
            body = data
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, body, timeout=300) as res:
        content = res.read()
        return content if raw else json.loads(content)


def multipart_upload(filepath):
    boundary = "----ragboundary123"
    filename = filepath.split("/")[-1]
    with open(filepath, "rb") as f:
        filedata = f.read()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: text/markdown\r\n\r\n"
    ).encode() + filedata + f"\r\n--{boundary}--\r\n".encode()
    return call(
        "/api/documents/upload",
        method="POST",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )


def main():
    passed, failed = [], []

    def check(name, ok, detail=""):
        (passed if ok else failed).append(name)
        print(f"  {'✅' if ok else '❌'} {name} {detail}")

    print("== 1. 健康检查 ==")
    h = call("/api/health")
    check("GET /api/health", h.get("status") == "ok", str(h))

    print("== 2. 分类与知识库总览 ==")
    cats = call("/api/categories")
    check("GET /api/categories", len(cats) >= 3, f"{len(cats)} 个分类")
    ov = call("/api/knowledge/overview")
    check(
        "GET /api/knowledge/overview",
        "stats" in ov and len(ov.get("categories", [])) >= 3,
        f"{ov['stats']['total_documents']} 文档 / {ov['stats']['total_chunks']} 切片",
    )

    print("== 3. 设置读取与更新 ==")
    st = call("/api/settings")
    check("GET /api/settings", len(st.get("editable", [])) == 6, st["system"]["database"])
    # hash 向量仅用于联调，相似度偏低；真实 BGE/API 模式使用默认阈值
    threshold = 0.05 if h.get("embedding_provider") == "hash" else 0.35
    updated = call(
        "/api/settings", method="PUT", data={"top_k": 6, "score_threshold": threshold}
    )
    top_k = next(i["value"] for i in updated["editable"] if i["key"] == "top_k")
    check("PUT /api/settings", top_k == 6 and next(i["source"] for i in updated["editable"] if i["key"] == "top_k") == "custom",
          f"top_k={top_k}, threshold={threshold}")

    print("== 4. 上传文档（解析→分块→向量化→入库）==")
    up = multipart_upload(SAMPLE)
    check("POST /api/documents/upload", up.get("chunk_count", 0) > 0,
          f"{up['filename']} -> {up['chunk_count']} 切片, {up['elapsed']}s")

    print("== 5. 文档列表与过滤 ==")
    docs = call("/api/documents")
    check("GET /api/documents", any(d["id"] == up["doc_id"] and d["status"] == "ready" for d in docs))
    filtered = call("/api/documents?status=ready")
    check("GET /api/documents?status=ready", len(filtered) >= 1)

    print("== 6. 切片预览 ==")
    chunks = call(f"/api/documents/{up['doc_id']}/chunks")
    check("GET /api/documents/{id}/chunks", len(chunks) > 0 and "报销" in "".join(c["text"] for c in chunks),
          f"{len(chunks)} 个切片")

    print("== 7. 检索问答（SSE）==")
    raw = call("/api/chat", method="POST", data={"question": "一线城市住宿标准是多少？", "top_k": 3}, raw=True)
    events = [json.loads(line[6:]) for line in raw.decode("utf-8").split("\n\n") if line.startswith("data: ")]
    sources = next((e["sources"] for e in events if e["type"] == "sources"), [])
    tokens = "".join(e["content"] for e in events if e["type"] == "token")
    errors = [e["message"] for e in events if e["type"] == "error"]
    check("SSE 返回 sources", len(sources) > 0, f"{len(sources)} 条来源")
    if any("DEEPSEEK_API_KEY" in m for m in errors):
        check("SSE 流式回答非空", True, "SKIP：未配置 DeepSeek API Key，生成环节跳过")
    else:
        check("SSE 流式回答非空", bool(tokens.strip()), tokens[:60].replace("\n", " "))

    print("== 8. 会话记录 ==")
    sessions = call("/api/sessions")
    if sessions:
        msgs = call(f"/api/sessions/{sessions[0]['id']}/messages")
        check("会话与消息落库", len(msgs) >= 2 and msgs[-1]["role"] == "assistant")
    else:
        check("会话与消息落库", False, "无会话生成")

    print("== 9. 修改文档分类 ==")
    target_cat = next(c for c in cats if c["name"] != "通用文档")
    patched = call(f"/api/documents/{up['doc_id']}", method="PATCH", data={"category_id": target_cat["id"]})
    check("PATCH 文档分类", patched["category_id"] == target_cat["id"], patched["category_name"])

    print("== 10. 初始文档导入（去重）==")
    seed = call("/api/settings/seed", method="POST")
    check("POST /api/settings/seed", seed["failed"] == 0,
          f"导入 {seed['imported']}，跳过 {seed['skipped']}")

    print("== 11. 删除文档 ==")
    call(f"/api/documents/{up['doc_id']}", method="DELETE")
    docs_after = call("/api/documents")
    check("DELETE 文档", all(d["id"] != up["doc_id"] for d in docs_after))

    print("== 12. 恢复默认设置 ==")
    restored = call("/api/settings", method="PUT", data={"top_k": 5, "score_threshold": 0.35})
    ok = next(i["value"] for i in restored["editable"] if i["key"] == "top_k") == 5
    check("恢复默认检索参数", ok)

    print(f"\n结果: {len(passed)} 通过, {len(failed)} 失败")
    if failed:
        print("失败项:", failed)
        sys.exit(1)


if __name__ == "__main__":
    main()
