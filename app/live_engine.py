"""aids0 逐题答题引擎（逆向验证版）。

流程：
  POST preanswer1.html?vip=<vip>  body: getq=开始答题
   -> 302 answer2.html?vip=<quizvip>  (第1题)
  循环题目:
    解析当前 answer2.html?vip=<quizvip> 页面:
       - 题干 .qtitle1
       - 题型 .qtips1_1 (判断题/单选题/多选题)
       - 选项 input radio/checkbox  name/value + label 文本
       - hidden questionid
    作答 (题库/LLM/启发式)
    POST answer2.html?vip=<quizvip>  body: <name>=<value>&questionid=<id>&answer=提交
    -> 返回答题页: 下一题 / 或 交卷完成页
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("autoquiz.live")


@dataclass
class LiveQuestion:
    stem: str
    qtype: str
    qid: str
    options: List[Dict]   # {label, value, name, text}
    answered: str = "0/25"


@dataclass
class LiveSession:
    base: str
    cookie: str
    ua: str
    session: requests.Session

    @classmethod
    def new(cls, base, cookie, ua):
        s = requests.Session()
        s.headers.update({"User-Agent": ua, "Referer": base.rstrip("/") + "/answer2.html"})
        for part in cookie.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                s.cookies.set(k.strip(), v.strip())
        return cls(base, cookie, ua, s)


def parse_question(html: str) -> Optional[LiveQuestion]:
    soup = BeautifulSoup(html, "html.parser")
    qtitle = soup.select_one(".qtitle1")
    if not qtitle:
        return None
    stem = re.sub(r"^\s*\d+[\.\、]\s*", "", qtitle.get_text(" ", strip=True))
    type_el = soup.select_one(".qtips1_1")
    qtype_txt = type_el.get_text(strip=True) if type_el else ""
    qtype = "judge" if "判断" in qtype_txt else ("multiple" if "多选" in qtype_txt or "不定项" in qtype_txt else "single")

    qid_el = soup.find("input", {"name": "questionid"})
    qid = qid_el["value"] if qid_el else ""

    options = []
    for inp in soup.find_all("input", {"type": re.compile("^(radio|checkbox)$", re.I)}):
        name = inp.get("name", "rightorwrong")
        val = inp.get("value", "")
        lid = inp.get("id")
        text = ""
        if lid:
            lab = soup.find("label", {"for": lid})
            if lab:
                text = lab.get_text(" ", strip=True)
        if not text:
            lab = inp.find_parent("div")
            if lab:
                text = lab.get_text(" ", strip=True)
        text = re.sub(r"^[A-Ha-h][\.\、\)]\s*", "", text).strip()
        label = ""
        m = re.search(r"([A-H])[\.\、]", (lab.get_text(strip=True) if (lab := soup.find("label", {"for": lid})) else ""))
        if m:
            label = m.group(1)
        options.append({"name": name, "value": val, "label": label, "text": text})

    answered_el = soup.select_one(".qtips1_2 span")
    answered = answered_el.get_text(strip=True) if answered_el else ""
    return LiveQuestion(stem=stem, qtype=qtype, qid=qid, options=options, answered=answered)
