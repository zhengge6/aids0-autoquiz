"""题目解析：从 answer2.php 渲染的 HTML 中提取题干、选项、题型与字段名。

该站点基于自定义 PHP 答题模板，不同年份 DOM 略有差异。
采用多策略解析：
  1) 优先用 BeautifulSoup 按常见容器选择器扫描题目块；
  2) 提取 radio/checkbox 的 name/option 值；
  3) 兼容纯文本兜底（题干 + A/B/C/D 行）。
"""
from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from bs4 import BeautifulSoup

log = logging.getLogger("autoquiz.parser")


@dataclass
class Option:
    label: str        # A/B/C/D...
    value: str        # input value
    text: str         # 选项文本


@dataclass
class Question:
    qid: str                      # 字段名/题目 id
    index: int                    # 序号
    qtype: str                    # single | multiple | judge
    stem: str                     # 题干
    options: List[Option] = field(default_factory=list)
    answer_value: Optional[str] = None       # 最终选择的 option.value
    answer_labels: List[str] = field(default_factory=list)  # 选择标签
    source: str = "none"          # bank | llm | heuristic
    score: float = 0.0

    def to_dict(self):
        return {
            "qid": self.qid, "index": self.index, "qtype": self.qtype,
            "stem": self.stem,
            "options": [{"label": o.label, "value": o.value, "text": o.text} for o in self.options],
            "answer_value": self.answer_value,
            "answer_labels": self.answer_labels,
            "source": self.source, "score": self.score,
        }


# 常见题目块选择器（从最具体到最宽泛）
BLOCK_SELECTORS = [
    ".question", ".quiz-item", ".exam-item", ".subject", ".qitem", ".timu",
    "div[id^=q]", "div[class*=question]", "div[class*=timu]", "li[class*=question]",
]

OPTION_LABELS = "ABCDEFGH"


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _guess_type(block) -> str:
    inputs = block.find_all("input")
    radio = [i for i in inputs if (i.get("type") or "").lower() == "radio"]
    checkbox = [i for i in inputs if (i.get("type") or "").lower() == "checkbox"]
    if checkbox:
        return "multiple"
    if radio:
        # 判断题常见：只有两个选项且含"正确/错误/对/错"
        txt = block.get_text(" ", strip=True)
        if any(w in txt for w in ("正确", "错误", "对错", "判断题")) and len(radio) <= 2:
            return "judge"
        return "single"
    return "single"


def _extract_options(block) -> List[Option]:
    opts: List[Option] = []
    inputs = block.find_all("input", {"type": re.compile("^(radio|checkbox)$", re.I)})
    for i, inp in enumerate(inputs):
        val = inp.get("value")
        if val is None:
            val = inp.get("data-value") or inp.get("id") or ""
        # 选项文本：优先关联 label，其次父节点文本
        text = ""
        lid = inp.get("id")
        if lid:
            lbl = block.find("label", {"for": lid})
            if lbl:
                text = lbl.get_text(" ", strip=True)
        if not text:
            parent = inp.find_parent(["label", "li", "div", "span", "p"])
            if parent:
                text = _clean(parent.get_text(" ", strip=True))
        if not text:
            text = _clean(inp.get("title", ""))
        # 去掉开头的 A. A、A) 等标签
        stripped = re.sub(r"^[A-Ha-h][\s\.、\):：]+", "", text).strip()
        opts.append(Option(label=OPTION_LABELS[i] if i < len(OPTION_LABELS) else str(i),
                           value=str(val), text=stripped or text))
    return opts


def _stem_text(block) -> str:
    # 题干：优先 .title/.stem/.q-title/.timu-title 等，否则取块内第一段非选项文本
    for sel in (".q-title", ".title", ".stem", ".timu-title", ".question-title", "h3", "h4", "p"):
        el = block.select_one(sel)
        if el:
            t = _clean(el.get_text(" ", strip=True))
            if len(t) >= 4:
                return t
    # 兜底：去掉所有选项后的剩余文本
    clone = BeautifulSoup(str(block), "html.parser")
    for i in clone.find_all("input"):
        i.decompose()
    for lab in clone.find_all("label"):
        lab.decompose()
    txt = _clean(clone.get_text(" ", strip=True))
    return txt[:400]


def _field_name(block, qtype: str) -> str:
    inputs = block.find_all("input", {"type": re.compile("^(radio|checkbox)$", re.I)})
    if inputs:
        name = inputs[0].get("name")
        if name:
            return name
    # 尝试 data 属性或容器 id
    if block.get("id"):
        return block["id"]
    return ""


def parse_questions(html: str) -> List[Question]:
    soup = BeautifulSoup(html, "html.parser")
    blocks = []
    for sel in BLOCK_SELECTORS:
        found = soup.select(sel)
        if len(found) >= 3:
            blocks = found
            break
        if found and not blocks:
            blocks = found

    questions: List[Question] = []
    if blocks:
        for idx, blk in enumerate(blocks):
            opts = _extract_options(blk)
            if len(opts) < 2:
                continue
            qtype = _guess_type(blk)
            stem = _stem_text(blk)
            if not stem:
                continue
            questions.append(Question(
                qid=_field_name(blk, qtype) or f"q{idx+1}",
                index=idx + 1, qtype=qtype, stem=stem, options=opts,
            ))

    if not questions:
        questions = _fallback_parse(soup)

    log.info("解析到 %d 道题目", len(questions))
    return questions


def _fallback_parse(soup: BeautifulSoup) -> List[Question]:
    """纯文本兜底：按 '1. 题干 ... A. xx B. xx' 之类切分。"""
    text = soup.get_text("\n", strip=True)
    lines = [l for l in text.split("\n") if l.strip()]
    qs: List[Question] = []
    cur: Optional[Question] = None
    for line in lines:
        m = re.match(r"^(\d+)[\.\、\s]+(.+)", line)
        if m and len(m.group(2)) > 5 and not re.match(r"^[A-D][\.\、]", line):
            if cur:
                qs.append(cur)
            cur = Question(qid=f"q{len(qs)+1}", index=len(qs) + 1, qtype="single", stem=_clean(m.group(2)))
            continue
        om = re.match(r"^([A-Da-d])[\.\、\):：\s]+(.+)", line)
        if om and cur is not None:
            label = om.group(1).upper()
            cur.options.append(Option(label=label, value=label, text=_clean(om.group(2))))
    if cur:
        qs.append(cur)
    return [q for q in qs if len(q.options) >= 2]
