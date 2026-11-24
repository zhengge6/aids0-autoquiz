"""本地题库 + 相似度匹配引擎。

匹配流程：
  1) 题干归一化（去标点/空白/全半角统一）；
  2) 用字符 n-gram Jaccard + difflib 比率混合打分；
  3) 命中阈值则采用题库答案，并把题库的选项标签映射到当前题目真实的 option.value。
"""
from __future__ import annotations

import json
import os
import re
import logging
import unicodedata
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

from .config import PROJECT_ROOT, APP_DIR
from .parser import Question, Option

log = logging.getLogger("autoquiz.bank")

BANK_PATH = os.path.join(PROJECT_ROOT, "data", "question_bank.json")
LEARNED_PATH = os.path.join(APP_DIR, "logs", "question_bank.learned.json")


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "")
    t = re.sub(r"[\s\u3000]+", "", t)
    t = re.sub(r"[，。？！、；：“”‘’（）《》【】,.\?!;:\"'()<>\[\]]", "", t)
    return t.lower()


def _ngrams(s: str, n: int) -> set:
    return {s[i:i + n] for i in range(max(0, len(s) - n + 1))} if len(s) >= n else {s}


def similarity(a: str, b: str) -> float:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    seq = SequenceMatcher(None, na, nb).ratio()
    ga, gb = _ngrams(na, 2), _ngrams(nb, 2)
    jac = len(ga & gb) / len(ga | gb) if (ga | gb) else 0.0
    contain = 1.0 if (na in nb or nb in na) and min(len(na), len(nb)) >= 6 else 0.0
    return max(seq, jac, contain * 0.95)


def _judge_dir_score(ans_text: str, opt_text: str) -> float:
    """判断题方向对齐：答案'正确'应匹配含'正确/对/是'的选项，反之亦然。"""
    pos_words = ("正确", "对", "是", "可以", "会")
    neg_words = ("错误", "错", "否", "不", "不会", "不能")
    a_pos = any(w in ans_text for w in pos_words)
    a_neg = any(w in ans_text for w in neg_words)
    o_pos = any(w in opt_text for w in pos_words)
    o_neg = any(w in opt_text for w in neg_words)
    if a_pos and not a_neg and o_pos and not o_neg:
        return 0.9
    if a_neg and not a_pos and o_neg and not o_pos:
        return 0.9
    return 0.0


class QuestionBank:
    def __init__(self):
        self.items: List[Dict] = []
        self._load(BANK_PATH)
        self._load(LEARNED_PATH)

    def _load(self, path: str):
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            items = data.get("items", data if isinstance(data, list) else [])
            for it in items:
                if it.get("stem") and it.get("answer"):
                    self.items.append(it)
            log.info("题库加载 %s -> 累计 %d 条", os.path.basename(path), len(self.items))
        except Exception as exc:  # noqa: BLE001
            log.warning("题库加载失败 %s: %s", path, exc)

    def best_match(self, stem: str) -> Tuple[Optional[Dict], float]:
        best, best_s = None, 0.0
        for it in self.items:
            s = similarity(stem, it["stem"])
            if s > best_s:
                best, best_s = it, s
        return best, best_s

    def match(self, q: Question, threshold: float) -> bool:
        """命中则回填 q.answer_value/answer_labels，返回是否命中。

        关键：题库答案用【选项文本】存储，这里把每个答案文本对齐到本题
        真实选项（取文本相似度最高者），从而不受选项乱序影响。
        """
        item, s = self.best_match(q.stem)
        if not item or s < threshold:
            return False

        ans_texts = [str(x) for x in item["answer"]]
        values: List[str] = []
        matched_labels: List[str] = []
        used = set()

        for atext in ans_texts:
            best_opt, best_os = None, 0.0
            for idx, opt in enumerate(q.options):
                if idx in used:
                    continue
                os_ = similarity(atext, opt.text)
                # 判断题方向词归一
                if q.qtype == "judge":
                    os_ = max(os_, _judge_dir_score(atext, opt.text))
                if os_ > best_os:
                    best_opt, best_os, best_idx = opt, os_, idx
            if best_opt is not None and best_os >= 0.45:
                values.append(best_opt.value)
                matched_labels.append(best_opt.label)
                used.add(best_idx)

        if not values:
            # 文本对齐失败：判断题兜底
            if q.qtype == "judge":
                want_pos = any(w in "".join(ans_texts) for w in ("正确", "对", "是"))
                pick = next((o for o in q.options
                             if (want_pos and any(w in o.text for w in ("正确", "对", "是")))
                             or (not want_pos and any(w in o.text for w in ("错误", "错", "否", "不")))),
                            None)
                if pick:
                    values, matched_labels = [pick.value], [pick.label]
            if not values:
                return False

        q.answer_value = ",".join(values) if len(values) > 1 else values[0]
        q.answer_labels = matched_labels
        q.source = "bank"
        q.score = s
        return True

    def learn(self, q: Question):
        """把新题（含已定答案的选项文本）学习进 learned 题库（供下次命中）。"""
        texts = [o.text for o in q.options if o.label in q.answer_labels]
        if not texts:
            return
        item = {"stem": q.stem, "type": q.qtype, "answer": texts, "auto": True}
        self.items.append(item)
        try:
            existing = []
            if os.path.exists(LEARNED_PATH):
                with open(LEARNED_PATH, "r", encoding="utf-8") as f:
                    existing = json.load(f).get("items", [])
            existing.append(item)
            with open(LEARNED_PATH, "w", encoding="utf-8") as f:
                json.dump({"items": existing}, f, ensure_ascii=False, indent=2)
        except Exception as exc:  # noqa: BLE001
            log.warning("题库学习写入失败: %s", exc)
