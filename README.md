<div align="center">

<img src="assets/logo.svg" width="120" alt="aids0-autoquiz logo"/>

# 🎗️ aids0-autoquiz

**Automated quiz-solving toolkit for the National College AIDS-Prevention Knowledge Contest**

Pure-Python · WeChat session extraction · 100-question official bank · Reverse-engineered answer API

[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Windows-lightgrey.svg)]()
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![PRs](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)]()

[English](README.md) · **简体中文**

*Reverse-engineered for research & automation study. Use only on accounts you own.*

</div>

---

## 📖 Overview

`aids0-autoquiz` automates the full answering flow of **aids0.com** (2026 National College
AIDS-Prevention Knowledge Contest), from WeChat login-state extraction to per-question
submission and scoring.

It was built by reverse-engineering the site's actual request chain — a **per-question,
stateful submission API**. Along the way we found that bulk-submit ("answer-less direct
submit") is **not possible** by design (one-question-at-a-time with rotating anti-replay
tokens), so the only viable automation is genuine question-by-question solving.

> **Real-world result:** a full run scored **92 / 100** (passing line is 60), verified
> against the site's own wrong-answer review page.

---

## ✨ Features

| | |
|---|---|
| 🍪 **WeChat cookie extraction** | Pure-Python: VSS shadow copy → DPAPI → AES-256-GCM. No external scripts, no packet capture. |
| 🧠 **3-tier answer engine** | Local 100-question bank → LLM fallback → heuristics. |
| 🎯 **Option-order-safe matching** | Answers stored as *option text*, aligned to whatever order the exam presents. |
| 🔁 **Full pipeline** | Login-state → register → start quiz → per-question submit → auto score. |
| 📇 **Official question bank** | Structured 100 Q&A (`single` / `multiple` / `judge`). |
| 🪟 **Windows-native** | Reads the local WeChat 4.x WebView cookie DB. |

---

## 🔬 How it works

### 1. Login-state extraction

The contest login is WeChat OAuth (`snsapi_base`), producing a server-side session cookie
(`PHPSESSID`). That cookie lives inside the **WeChat 4.x WebView** (Chromium) cookie store:

```
%APPDATA%\Tencent\xwechat\radium\web\profiles\*\Network\Cookies
```

The DB is locked by WeChat and `encrypted_value` is Chromium-OSCrypt encrypted. `wx_cookie.py` recovers it in pure Python:

```
Win32_ShadowCopy (WMI)  ──►  copy locked Cookies DB via shadow path
Local State -> os_crypt.encrypted_key (base64, 'DPAPI' prefix)
DPAPI (ctypes CryptUnprotectData)  ──►  AES-256 master key
AES-256-GCM  ──►  decrypt each cookie  ──►  strip WeChat binary prefix  ──►  PHPSESSID
```

> Requires Admin (VSS) + the same Windows user that is logged into WeChat (DPAPI).

### 2. Answer API (reverse-engineered)

| Stage | Request |
|-------|---------|
| Login | `mblogin.php` → WeChat OAuth (`redirect_uri=mblogin2.php`) |
| Register | `POST answer2.html` — `year/school/fullname/qq/mobile/studentid/gmajor/sex/subinfo` |
| Follow-gate | `acertpre.php → acertpre1.php?code=` |
| Start | `POST preanswer1.html?vip=X` — `getq=开始答题` (consumes 1 attempt, draws 25 Qs) |
| Fetch Q | `GET answer2.html?vip=Y` — one question per page |
| Submit Q | `POST answer2.html?vip=Y` — `judge: rightorwrong=1/0`, `single: ifright1=N`, `multiple: ifright1..n=1`, plus `questionid` & `answer=提交` |
| Score / Review | score page / `review-<id>.html` |

> ⚠️ **"Answer-less direct submit" is impossible**: each submission rotates the `vip` token (anti-replay) and the server keeps a per-question state machine.

---

## 🚀 Quick Start

```bash
pip install -r requirements.txt

# 1) Log into the contest in PC WeChat: open http://www.aids0.com/mblogin.php , authorize.
# 2) Extract the WeChat session cookie (UAC prompt -> Yes)
python wx_cookie.py fresh

# 3) Full auto: register (if needed) -> answer -> submit -> score
python -m app.auto_live --identity place
```

Generate a scannable login QR (and copy the link to clipboard):

```bash
python -m app.auto_live --qrcode
```

---

## 🗂️ Project layout

```
wx_cookie.py              ★ Pure-Python WeChat cookie extractor (VSS + DPAPI + AES-GCM)
app/
  auto_live.py            ★ One-shot pipeline + QR code
  live_engine.py          Answer-page DOM parser (type / stem / options / questionid)
  run_quiz.py             Per-question decision engine (bank -> LLM -> heuristic)
  bank.py                 Question bank + option-text alignment matcher
  parser.py, solver.py, submit.py, http_client.py, config.py
data/
  question_bank.json      Official 100 Q&A (38 single / 31 multiple / 31 judge)
  official_bank_raw.txt   Raw source
tools/
  parse_official_bank.py  Rebuild question_bank.json from raw
samples/
  sample_quiz.html        Offline demo fixture
```

---

## ⚙️ Configuration

Copy `config.example.json` → `config.json`. Key fields:

| Key | Meaning |
|-----|---------|
| `login.cookie` | Session cookie (auto-filled by `wx_cookie.py`) |
| `answer.bank_match_threshold` | Question-match similarity cutoff (default `0.62`) |
| `llm.enabled / api_key / model` | Optional LLM fallback (OpenAI-compatible) |
| `safety.dry_run` | Parse-only, no submit |

---

## 🧩 Limitations

- **Windows only** for cookie extraction (WeChat 4.x WebView + VSS + DPAPI).
- **Real WeChat authorization is mandatory** — no session, no answering. The cookie must come from an already-logged-in WeChat on the same machine.
- **Attempts are limited** per account (initial 1, up to 3 via invites, best score kept).
- **Cross-device**: a phone scan does not transfer the session to a PC — the cookie stays on the scanning device.

---

## ⚖️ Disclaimer

This project is provided for **security research, reverse-engineering study, and personal automation** on accounts you are authorized to use. Respect the platform's terms of service. The authors are not responsible for misuse.

---

## 📄 License

[MIT](LICENSE)
