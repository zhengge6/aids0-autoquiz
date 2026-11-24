"""解析 official_bank_raw.txt -> data/question_bank.json (结构化，答案以选项文本存储)。"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RAW = os.path.join(ROOT, "data", "official_bank_raw.txt")
OUT = os.path.join(ROOT, "data", "question_bank.json")

LABELS = "ABCDE"
items = []
with open(RAW, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line or "|" not in line:
            continue
        parts = line.split("|")
        if len(parts) < 5:
            continue
        try:
            idx = int(parts[0])
        except ValueError:
            continue
        category = parts[1]
        stem = parts[2]
        qtype_raw = parts[3]
        answer_letters = parts[4].strip().upper()
        opt_texts = parts[5:10]  # up to 5 options
        # 选项 = 非空的前若干
        opts = [t.strip() for t in opt_texts]
        # 生成 answer 文本列表
        ans_texts = []
        for letter in answer_letters:
            li = LABELS.find(letter)
            if 0 <= li < len(opts) and opts[li]:
                ans_texts.append(opts[li])
        qtype = "multiple" if "多选" in qtype_raw else ("judge" if "判断" in qtype_raw else "single")
        if not ans_texts:
            continue
        items.append({
            "idx": idx, "category": category, "stem": stem,
            "type": qtype, "answer": ans_texts,
            "options": [o for o in opts if o],
        })

with open(OUT, "w", encoding="utf-8") as f:
    json.dump({"items": items}, f, ensure_ascii=False, indent=2)
print(f"parsed {len(items)} questions -> {OUT}")
from collections import Counter
print("types:", Counter(i["type"] for i in items))
