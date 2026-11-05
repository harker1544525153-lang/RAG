# -*- coding: utf-8 -*-
"""文档管理接口：上传、列表（分类/状态/关键词过滤）、切片预览、修改、删除、统计、原文与片段定位页。"""
import html
import logging
import re
from pathlib import Path
from string import Template

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app import crud
from app.services import ingest
from app.services.doc_preview import MAX_HTML_CHARS, render_doc_html
from app.services.vector import get_vector_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/documents", tags=["documents"])


class DocumentUpdate(BaseModel):
    filename: str | None = None
    category_id: str | None = None


def _check_file(filename: str, size: int) -> None:
    suffix = Path(filename).suffix.lower()
    if suffix not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(
            400,
            f"不支持的文件类型 {suffix}，仅支持: {', '.join(sorted(settings.ALLOWED_EXTENSIONS))}",
        )
    if size > settings.MAX_FILE_SIZE_MB * 1024 * 1024:
        raise HTTPException(400, f"文件超过大小限制 {settings.MAX_FILE_SIZE_MB}MB")


@router.post("/upload")
def upload_document(
    file: UploadFile = File(...),
    category_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """上传并同步处理文档（解析→清洗→分块→向量化→入库）。"""
    content = file.file.read()
    _check_file(file.filename, len(content))
    if category_id and not crud.get_category(db, category_id):
        raise HTTPException(400, "分类不存在")
    try:
        return ingest.ingest(
            db,
            filename=file.filename,
            content=content,
            source="upload",
            category_id=category_id,
        )
    except Exception as e:
        raise HTTPException(500, f"文档处理失败: {e}")


@router.get("")
def get_documents(
    category_id: str | None = None,
    status: str | None = None,
    keyword: str | None = None,
    db: Session = Depends(get_db),
):
    return crud.list_documents(db, category_id=category_id, status=status, keyword=keyword)


@router.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    stats = crud.document_stats(db)
    stats["vector_chunks"] = get_vector_store().count()
    return stats


@router.get("/{doc_id}/chunks")
def get_document_chunks(doc_id: str, limit: int = 100, db: Session = Depends(get_db)):
    """预览某文档的切片内容。"""
    doc = crud.get_document(db, doc_id)
    if not doc:
        raise HTTPException(404, "文档不存在")
    return get_vector_store().get_chunks(doc_id, limit=limit)


@router.patch("/{doc_id}")
def update_document(doc_id: str, payload: DocumentUpdate, db: Session = Depends(get_db)):
    """修改文档名称或所属分类（知识库动态整理）。"""
    doc = crud.get_document(db, doc_id)
    if not doc:
        raise HTTPException(404, "文档不存在")
    fields = {}
    if payload.filename and payload.filename.strip():
        fields["filename"] = payload.filename.strip()
    if payload.category_id:
        if not crud.get_category(db, payload.category_id):
            raise HTTPException(400, "分类不存在")
        fields["category_id"] = payload.category_id
    if not fields:
        raise HTTPException(400, "没有可更新的字段")
    crud.update_document(db, doc_id, **fields)
    return crud.document_to_dict(crud.get_document(db, doc_id))


@router.delete("/{doc_id}")
def remove_document(doc_id: str, db: Session = Depends(get_db)):
    if not ingest.remove(db, doc_id):
        raise HTTPException(404, "文档不存在")
    return {"ok": True}


@router.get("/{doc_id}/file")
def download_document(doc_id: str, db: Session = Depends(get_db)):
    """在浏览器中打开文档原文件（正文预览内嵌 PDF 时复用此接口）。"""
    doc = crud.get_document(db, doc_id)
    if not doc:
        raise HTTPException(404, "文档不存在")
    path = settings.UPLOAD_DIR / f"{doc.id}_{doc.filename}"
    if not path.exists():
        raise HTTPException(404, "原始文件不存在")
    return FileResponse(path, filename=doc.filename, content_disposition_type="inline")


# ---------------- 片段定位页（参考来源的 [编号] 链接到这里） ----------------
view_router = APIRouter(tags=["doc-view"])

_PREVIEW_TPL = Template("""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>$filename</title>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
         background: #eef1f6; color: #1d2129; }
  header { position: sticky; top: 0; z-index: 9; display: flex; gap: 12px; align-items: center;
           flex-wrap: wrap; padding: 12px 20px; background: #fff; border-bottom: 1px solid #e5e8ef; }
  h1 { margin: 0; font-size: 15px; font-weight: 600; word-break: break-all; }
  .meta { margin-top: 2px; font-size: 12px; color: #86909c; }
  .paper { max-width: 820px; margin: 22px auto 60px; padding: 44px 56px; background: #fff;
           border-radius: 6px; box-shadow: 0 1px 3px rgba(0, 0, 0, .08); }
  /* 内嵌在问答页右侧预览面板时：去掉外框留白，贴合面板宽度 */
  body.embed header { position: static; padding: 10px 14px; }
  body.embed .paper { max-width: none; margin: 0; padding: 22px 26px 48px;
                      border-radius: 0; box-shadow: none; min-height: calc(100vh - 51px); }
  body.embed .pdf { height: calc(100vh - 51px); }
  .doc { font-size: 15px; line-height: 1.9; word-break: break-word; }
  .doc h1 { font-size: 22px; margin: 18px 0 10px; }
  .doc h2 { font-size: 18px; margin: 16px 0 8px; }
  .doc h3, .doc h4 { font-size: 16px; margin: 14px 0 8px; }
  .doc p { margin: 8px 0; }
  .doc p.plain { white-space: pre-wrap; }
  .doc ul, .doc ol { margin: 8px 0 8px 22px; padding: 0; }
  .doc table { width: 100%; border-collapse: collapse; margin: 12px 0; }
  .doc th, .doc td { border: 1px solid #dcdfe6; padding: 6px 10px; font-size: 14px; }
  .doc th { background: #f5f7fa; }
  .doc img { max-width: 100%; }
  .doc a { color: #165dff; }
  .doc pre { margin: 10px 0; padding: 12px 14px; background: #f7f8fa;
             border: 1px solid #e8ebf0; border-radius: 6px; overflow-x: auto; }
  .doc pre code, .doc code { font-family: Consolas, "Courier New", monospace; font-size: 13px; }
  .doc code { padding: 1px 5px; background: #f2f3f5; border-radius: 4px; }
  .doc .slide { margin: 22px 0; padding: 18px 24px; background: #fbfcfe;
                border: 1px solid #e8ecf2; border-left: 3px solid #165dff; border-radius: 8px; }
  .doc .slide h3 { margin: 0 0 10px; font-size: 14px; color: #165dff; font-weight: 600; }
  .doc .slide p { margin: 6px 0; }
  .doc .hit { background: #fff3b0; box-shadow: 0 0 0 3px #fff3b0; border-radius: 3px; }
  .pdf { width: 100%; height: calc(100vh - 57px); border: 0; display: block; }
</style>
</head>
<body$body_class>
<header>
  <div>
    <h1>$filename</h1>
    <div class="meta">$meta</div>
  </div>
</header>
$body
<script>
  (function () {
    var doc = document.querySelector('.doc');
    if (!doc) return;
    var quote = (doc.getAttribute('data-quote') || '').replace(/\s+/g, '');
    var anchor = (doc.getAttribute('data-anchor') || '').replace(/\s+/g, '');
    if (!quote && !anchor) return;

    // 把正文文本节点拼成「去空白」的全文索引，实现按字符位置精确高亮
    function indexText(root) {
      var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
      var map = [], full = '', n;
      while ((n = walker.nextNode())) {
        var p = n.parentNode, skip = false;
        while (p && p !== root) {
          var tag = (p.tagName || '').toLowerCase();
          if (tag === 'script' || tag === 'style') { skip = true; break; }
          p = p.parentNode;
        }
        if (skip) continue;
        var v = n.nodeValue;
        for (var i = 0; i < v.length; i++) {
          if (/\s/.test(v[i])) continue;
          map.push([n, i]);
          full += v[i];
        }
      }
      return { text: full, map: map };
    }

    function findQuote(index, q) {
      var pos = index.text.indexOf(q);
      if (pos >= 0) return [pos, q.length];
      // 原文渲染可能与切片有细微差异：逐步缩短尾部再匹配
      while (q.length > 16) {
        q = q.slice(0, Math.floor(q.length * 0.8));
        pos = index.text.indexOf(q);
        if (pos >= 0) return [pos, q.length];
      }
      return null;
    }

    // 判断字符所属的块级容器：跨单元格/段落时不能整体包裹，否则会搬走表格结构
    var BLOCKS = { p: 1, li: 1, td: 1, th: 1, tr: 1, h1: 1, h2: 1, h3: 1, h4: 1, pre: 1, table: 1 };
    function blockOf(node, root) {
      var el = node.nodeType === 3 ? node.parentNode : node;
      while (el && el !== root) {
        if (BLOCKS[(el.tagName || '').toLowerCase()]) return el;
        el = el.parentNode;
      }
      return root;
    }

    function paint(start, endExclusive, map) {
      var a = map[start], b = map[endExclusive - 1];
      if (!a || !b) return false;
      var range = document.createRange();
      range.setStart(a[0], a[1]);
      var blockA = blockOf(a[0], doc);
      var blockB = blockOf(b[0], doc);
      if (blockA !== blockB) {
        // 引用片段跨了表格单元格/段落：只高亮起点所在块内剩余的连续文字，
        // 避免把相邻 <td> 的内容 extractContents 到一起导致表格列错位
        range.setEnd(blockA, blockA.childNodes.length);
      } else {
        range.setEnd(b[0], b[1] + 1);
      }
      var mark = document.createElement('span');
      mark.className = 'hit';
      try {
        range.surroundContents(mark);
      } catch (e) {
        // 同一块内跨多个文本节点（如加粗/普通混排）：先取出内容再整体包一层
        mark.appendChild(range.extractContents());
        range.insertNode(mark);
      }
      mark.scrollIntoView({ block: 'center' });
      return true;
    }

    var index = indexText(doc);
    if (quote) {
      var found = findQuote(index, quote);
      if (found && paint(found[0], found[0] + found[1], index.map)) return;
    }

    // 回退：按切片锚点做粗定位（旧链接或原文与切片差异过大时）
    if (!anchor) return;
    var nodes = doc.querySelectorAll('h1, h2, h3, h4, p, li, td, tr, pre');
    var hit = null, i, j, t;
    for (i = 0; i < nodes.length && !hit; i++) {
      t = nodes[i].textContent.replace(/\s+/g, '');
      if (t && t.length >= 6 && anchor.indexOf(t.slice(0, 12)) !== -1) hit = nodes[i];
    }
    for (i = 0; i < nodes.length && !hit; i++) {
      t = nodes[i].textContent.replace(/\s+/g, '');
      if (!t) continue;
      for (j = 0; j + 12 <= anchor.length; j++) {
        if (t.indexOf(anchor.slice(j, j + 12)) !== -1) { hit = nodes[i]; break; }
      }
    }
    if (hit) {
      hit.classList.add('hit');
      hit.scrollIntoView({ block: 'center' });
    }
  })();
</script>
</body>
</html>
""")


@view_router.get("/doc/{doc_id}", response_class=HTMLResponse)
def view_document(
    doc_id: str,
    chunk: int = 0,
    span: int = 0,
    span_len: int = 0,
    embed: int = 0,
    db: Session = Depends(get_db),
):
    """按原文格式展示整篇文档，并定位 / 高亮被引用的那段文字（embed=1 为问答页右侧内嵌）。"""
    doc = crud.get_document(db, doc_id)
    if not doc:
        raise HTTPException(404, "文档不存在")
    path = settings.UPLOAD_DIR / f"{doc.id}_{doc.filename}"

    chunks = get_vector_store().get_chunks(doc_id, limit=10000)
    chunks.sort(key=lambda c: c["metadata"].get("chunk_index", 0))
    target = next(
        (c for c in chunks if c["metadata"].get("chunk_index", 0) == int(chunk or 0)), None
    )
    anchor = target["text"][:160] if target else ""
    # 细粒度引用片段：优先高亮问答时绑定的句子 span，而不是整段切片
    quote = ""
    if target and span_len > 0:
        quote = target["text"][int(span or 0) : int(span or 0) + int(span_len)]
        quote = quote.strip()[:240]
    page = (target["metadata"].get("page") or 0) if target else 0
    suffix = Path(doc.filename).suffix.lower()

    if suffix == ".pdf" and path.exists():
        body = f'<iframe class="pdf" src="/api/documents/{doc.id}/file#page={page or 1}"></iframe>'
        meta = f"PDF 原文预览 · 第 {page or 1} 页"
    else:
        # 优先用入库时保存的原文 HTML：云端原文件丢失也能展示原文并锚定引用片段
        inner = doc.preview_html or ""
        # 旧版本可能存了内嵌图片的巨型 HTML（2MB+），重新渲染成精简版
        if len(inner) > MAX_HTML_CHARS and path.exists():
            inner = ""
        if not inner and path.exists():
            inner = render_doc_html(path, suffix, chunks)
            if inner:
                try:
                    crud.update_document(db, doc.id, preview_html=inner)
                except Exception:
                    logger.warning("保存原文预览失败: %s", doc.id, exc_info=True)
        if inner:
            meta = f"{suffix.lstrip('.') or '文本'} 原文 · 已锚定并高亮引用片段"
        else:
            inner = "".join(
                f'<p class="plain">{html.escape(c["text"])}</p>'
                for c in chunks if c.get("text")
            ) or "<p>（文档内容为空）</p>"
            meta = "原文暂不可用，按知识库切片展示"
        body = (
            '<main class="paper"><article class="doc" data-quote="'
            f'{html.escape(quote, quote=True)}" data-anchor="'
            f'{html.escape(anchor, quote=True)}">{inner}</article></main>'
        )
    return HTMLResponse(
        _PREVIEW_TPL.substitute(
            filename=html.escape(doc.filename),
            meta=html.escape(meta),
            body=body,
            body_class=' class="embed"' if embed else "",
        )
    )
