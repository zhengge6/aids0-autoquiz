"""续跑当前进行中的答题轮次：不断 GET answer2.html（无 vip，进当前轮）-> 解析当题 -> 作答 -> 提交直到交卷。"""
import logging, time, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.INFO, format="%(message)s")
from app.config import load_config, PROJECT_ROOT
from app.live_engine import LiveSession, parse_question
from app.bank import QuestionBank
from app.run_quiz import decide, read_cookie

cfg = load_config()
ck = read_cookie()
s = LiveSession.new(cfg.base_url, ck, cfg.login.user_agent)
b = QuestionBank()
LOG = os.path.join(PROJECT_ROOT, "logs")

url = f"{cfg.base_url}/answer2.html"
r = s.session.get(url, timeout=20, allow_redirects=False)
print("GET", r.status_code, "loc", r.headers.get("Location"))

for step in range(30):
    if r.status_code != 200:
        loc = r.headers.get("Location") or ""
        if loc:
            nxt = loc if loc.startswith("http") else f"{cfg.base_url}/{loc.lstrip('/')}"
            r = s.session.get(nxt, timeout=20, allow_redirects=False)
            url = nxt
            continue
        print("停止", r.status_code); break
    q = parse_question(r.text)
    if not q:
        print("=== 无题，可能交卷完成 ===")
        open(os.path.join(LOG, "quiz_result.html"), "w", encoding="utf-8").write(r.text)
        print(r.text[:400])
        break
    picks = decide(q, b)
    data = {n: v for n, v in picks}
    data["questionid"] = q.qid
    data["answer"] = "提交"
    print(f"Q{q.answered} [{q.qtype}] {q.stem[:26]} -> {picks}")
    r = s.session.post(url, data=data, timeout=20, allow_redirects=False)
    if r.status_code == 302 and r.headers.get("Location"):
        loc = r.headers["Location"]
        nxt = loc if loc.startswith("http") else f"{cfg.base_url}/{loc.lstrip('/')}"
        url = nxt
        r = s.session.get(nxt, timeout=20, allow_redirects=False)
    else:
        print("提交返回", r.status_code, "len", len(r.text), r.text[:150])
        open(os.path.join(LOG, "quiz_result.html"), "w", encoding="utf-8").write(r.text)
        break
    time.sleep(0.3)
