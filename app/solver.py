"""作答引擎：题库命中 -> LLM 推理 -> 启发式兜底。

输出统一回填到 Question.answer_value（单选/判断取一个 value，多选取逗号拼接）。
"""
from __future__ import annotations

import json
import re
import logging
from typing import List, Optional

import requests

from .config import Config
from .parser import Question, Option
from .bank import QuestionBank

log = logging.getLogger("autoquiz.solver")

SYSTEM_PROMPT = (
    "你是中国预防艾滋病知识竞赛的答题助手。请根据题目与选项，选出正确答案。"
    "只输出 JSON：{\"answer\":[\"A\"]}，多选输出多个字母，判断题 A=正确 B=错误。不要解释。"
)


class Solver:
    def __init__(self, cfg: Config, bank: QuestionBank):
        self.cfg = cfg
        self.bank = bank

    def solve_all(self, questions: List[Question]) -> List[Question]:
        threshold = self.cfg.answer.bank_match_threshold
        for q in questions:
            if self.bank.match(q, threshold):
                continue
            if self.cfg.llm.ready():
                if self._llm_solve(q):
                    continue
            self._heuristic(q)
        return questions

    # ---- LLM ----
    def _llm_solve(self, q: Question) -> bool:
        opts_text = "\n".join(f"{o.label}. {o.text}" for o in q.options)
        user = f"题型：{q.qtype}\n题干：{q.stem}\n选项：\n{opts_text}"
        try:
            resp = requests.post(
                f"{self.cfg.llm.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {self.cfg.llm.api_key}", "Content-Type": "application/json"},
                json={
                    "model": self.cfg.llm.model,
                    "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                 {"role": "user", "content": user}],
                    "temperature": self.cfg.llm.temperature,
                },
                timeout=self.cfg.http.timeout,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            labels = self._parse_labels(content, q)
            if labels:
                self._apply_labels(q, labels)
                q.source = "llm"
                q.score = 1.0
                log.info("LLM 作答 q%d -> %s", q.index, labels)
                return True
        except Exception as exc:  # noqa: BLE001
            log.warning("LLM 作答失败 q%d: %s", q.index, exc)
        return False

    def _parse_labels(self, content: str, q: Question) -> List[str]:
        m = re.search(r"\{.*\}", content, re.S)
        if m:
            try:
                data = json.loads(m.group(0))
                ans = data.get("answer", [])
                if isinstance(ans, str):
                    ans = [ans]
                return [str(a).strip().upper()[:1] for a in ans if str(a).strip()]
            except Exception:  # noqa: BLE001
                pass
        letters = re.findall(r"\b([A-H])\b", content.upper())
        valid = {o.label for o in q.options}
        picked = [l for l in letters if l in valid]
        return picked[: len(q.options)]

    def _apply_labels(self, q: Question, labels: List[str]):
        values, mlabels = [], []
        for lab in labels:
            opt = next((o for o in q.options if o.label == lab), None)
            if opt:
                values.append(opt.value)
                mlabels.append(lab)
        if values:
            q.answer_value = ",".join(values) if len(values) > 1 else values[0]
            q.answer_labels = mlabels

    # ---- 启发式 ----
    def _heuristic(self, q: Question) -> None:
        """无题库无 LLM 时：判断题选"正确"，单选题选文本最长或含正向词的选项。
        仅为保证程序可跑通，正确率有限。"""
        if q.qtype == "judge":
            pos = next((o for o in q.options if any(w in o.text for w in ("正确", "对", "是"))), q.options[0])
            self._apply_labels(q, [pos.label])
        else:
            # 正向词加权
            kw = ["正确", "有效", "应该", "综合", "关爱", "不歧视", "安全", "预防", "检测", "治疗"]
            scored = sorted(
                q.options,
                key=lambda o: sum(1 for k in kw if k in o.text) * 10 + len(o.text) * 0.01,
                reverse=True,
            )
            pick = scored[0]
            if q.qtype == "multiple":
                picks = [o.label for o in scored[:2]]
                self._apply_labels(q, picks)
            else:
                self._apply_labels(q, [pick.label])
        q.source = "heuristic"
        q.score = 0.0
        log.info("启发式作答 q%d -> %s", q.index, q.answer_labels)
