# -*- coding: utf-8 -*-
"""文档自动分类：按文档「实际内容」判断类型，各分类的判定标准不同。

「默认配置」上传时（未手动指定分类）启用本模块：
  代码文档   代码类元素或代码特征行占比高（def/class/import/function/{} ; 等）
  表格文档   表格元素占比高，或含 | / 制表符分列的行占比高
  流程文档   步骤标记多（第 X 步 / 步骤 X / Step N / Stage N / ① / 1.）
  制度文档   条款标记多（第 X 条 / 必须 / 应当 / 不得 / 严禁 / 罚则 / 附则）
  Word 文档  标题元素多且正文长（层级分块更适合长手册、长报告）
  通用文档   以上均未命中时的兜底

每类先算归一化得分（0~1），取最高且达到阈值者；阈值不同即「判定标准不一样」。
"""
import re

DEFAULT_NAME = "通用文档"

CODE_EL_RATIO = 0.18
CODE_LINE_RE = re.compile(
    r"(^\s*(def|class|import|from|async\s+def|function|const|let|var|return|if|else|elif|for|while|try|catch|switch|case|public|private|static|void|int|str|bool|package|using|namespace|#include|@\w+|</?\w+[^>]*>)\b)"
    r"|(^\s*[{}();]+\s*$)"
    r"|(=>|\+\+|===|!==|::)"
)

TABLE_LINE_RE = re.compile(r"(\|.+\||\t.*\t.*\t)")

STEP_RE = re.compile(
    r"(第\s*[一二三四五六七八九十百0-9]+\s*步|步骤\s*[0-9一二三四五六七八九十]+|"
    r"Step\s*[0-9]+|Stage\s*[0-9]+|阶段\s*[0-9一二三四五六七八九十]+|"
    r"[①-⑩]|^\s*[0-9]{1,2}\s*[.、)）]\s)",
    re.MULTILINE,
)

CLAUSE_RE = re.compile(
    r"(第\s*[一二三四五六七八九十百0-9]+\s*条|第\s*[一二三四五六七八九十]+\s*章|"
    r"应当|不得|必须|严禁|禁止|违反|处以|罚款|罚则|附则|生效日期|本办法|本条例)"
)

SENTENCE_RE = re.compile(r"[。！？；]")


def _text_of(elements) -> str:
    return "\n".join((el.get("text") or "") for el in elements)


def _lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.strip()]


def _ratio(part: int, total: int) -> float:
    return part / total if total else 0.0


def _score_code(elements, lines, total_lines) -> tuple[float, str]:
    code_els = sum(1 for el in elements if el.get("type") == "code")
    code_lines = sum(1 for ln in lines if CODE_LINE_RE.search(ln))
    element_score = _ratio(code_els, len(elements) or 1)
    line_score = _ratio(code_lines, total_lines)
    score = max(element_score * 1.8, line_score * 1.3)
    if score >= 0.5:
        return min(1.0, score), f"代码元素 {code_els} 个 / 代码特征行 {code_lines} 行"
    return 0.0, ""


def _score_table(elements, lines, total_lines) -> tuple[float, str]:
    table_els = sum(1 for el in elements if el.get("type") == "table")
    table_lines = sum(1 for ln in lines if TABLE_LINE_RE.search(ln))
    element_score = _ratio(table_els, len(elements) or 1)
    line_score = _ratio(table_lines, total_lines)
    score = max(element_score * 2.2, line_score * 1.2)
    if score >= 0.5:
        return min(1.0, score), f"表格元素 {table_els} 个 / 表格行 {table_lines} 行"
    return 0.0, ""


def _score_flow(text, total_lines) -> tuple[float, str]:
    steps = len(STEP_RE.findall(text))
    # 步骤密度：每 15 行出现 1 个步骤标记视为显著
    density = steps / max(1.0, total_lines / 15)
    score = min(1.0, steps / 6.0) * 0.7 + min(1.0, density) * 0.3
    if steps >= 3 and score >= 0.5:
        return min(1.0, score), f"步骤标记 {steps} 处"
    return 0.0, ""


def _score_clause(text, total_lines) -> tuple[float, str]:
    clauses = len(CLAUSE_RE.findall(text))
    sentences = max(1, len(SENTENCE_RE.findall(text)))
    # 条款密度：平均每 5 句出现 1 个条款用语
    density = clauses / max(1.0, sentences / 5)
    score = min(1.0, clauses / 10.0) * 0.6 + min(1.0, density) * 0.4
    if clauses >= 3 and score >= 0.5:
        return min(1.0, score), f"条款标记 {clauses} 处"
    return 0.0, ""


def _score_longdoc(elements, chars) -> tuple[float, str]:
    headings = sum(1 for el in elements if el.get("type") == "heading")
    # 长手册特征：多级标题 + 正文字数足够
    score = min(1.0, headings / 8.0) * 0.5 + min(1.0, chars / 6000.0) * 0.5
    if headings >= 5 and chars >= 3000 and score >= 0.5:
        return min(1.0, score), f"标题 {headings} 个 / 正文 {chars} 字"
    return 0.0, ""


def classify(elements: list[dict], filename: str = "") -> dict:
    """按内容判定文档类型，返回 {name, strategy, score, reason}。"""
    text = _text_of(elements)
    lines = _lines(text)
    total_lines = len(lines) or 1
    chars = len(text)

    candidates: list[tuple[str, str, float, str]] = []

    score, reason = _score_code(elements, lines, total_lines)
    if score:
        candidates.append(("代码文档", "code", score, reason))

    score, reason = _score_table(elements, lines, total_lines)
    if score:
        candidates.append(("表格文档", "table", score, reason))

    score, reason = _score_flow(text, total_lines)
    if score:
        candidates.append(("流程文档", "flow", score * 1.05, reason))

    score, reason = _score_clause(text, total_lines)
    if score:
        candidates.append(("制度文档", "semantic", score, reason))

    score, reason = _score_longdoc(elements, chars)
    if score:
        candidates.append(("Word 文档", "hierarchical", score, reason))

    if candidates:
        name, strategy, score, reason = max(candidates, key=lambda x: x[2])
        return {"name": name, "strategy": strategy, "score": round(score, 2), "reason": reason}

    return {
        "name": DEFAULT_NAME,
        "strategy": "standard",
        "score": 0,
        "reason": "未命中代码/表格/流程/制度/长文档特征，按通用文档处理",
    }
