"""작업(Job) 저장소: SQLite + 작업별 폴더(work/jobs/{id}/ 로그·산출물)."""
import json
import sqlite3
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work"
JOBS_DIR = WORK / "jobs"
DB = WORK / "app.sqlite3"

JOBS_DIR.mkdir(parents=True, exist_ok=True)


def _conn():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS jobs(
        id TEXT PRIMARY KEY, kind TEXT, params TEXT,
        status TEXT, message TEXT, created REAL, updated REAL)""")
    return c


def create_job(kind: str, params: dict) -> str:
    jid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    with _conn() as c:
        c.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?)",
                  (jid, kind, json.dumps(params, ensure_ascii=False),
                   "queued", "", time.time(), time.time()))
    (JOBS_DIR / jid).mkdir(parents=True, exist_ok=True)
    log(jid, f"작업 생성: kind={kind} params={params}")
    return jid


def set_status(jid: str, status: str, message: str = ""):
    with _conn() as c:
        c.execute("UPDATE jobs SET status=?, message=?, updated=? WHERE id=?",
                  (status, message, time.time(), jid))


def get_job(jid: str):
    with _conn() as c:
        r = c.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    return _row(r) if r else None


def list_jobs(limit=50):
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM jobs ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
    return [_row(r) for r in rows]


def _row(r):
    return {"id": r[0], "kind": r[1], "params": json.loads(r[2] or "{}"),
            "status": r[3], "message": r[4], "created": r[5], "updated": r[6]}


def job_dir(jid: str) -> Path:
    d = JOBS_DIR / jid
    d.mkdir(parents=True, exist_ok=True)
    return d


def log(jid: str, text: str):
    line = f"[{time.strftime('%H:%M:%S')}] {text}"
    print(f"({jid}) {line}", flush=True)
    with open(job_dir(jid) / "log.txt", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def read_log(jid: str) -> str:
    p = job_dir(jid) / "log.txt"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def artifacts(jid: str):
    d = job_dir(jid)
    out = []
    for p in sorted(d.iterdir()):
        if p.name not in ("log.txt", "CANCEL") and p.is_file():
            st = p.stat()
            out.append({"name": p.name, "size": st.st_size, "mtime": int(st.st_mtime)})
    return out


# --- 중간 멈춤(취소): 센티널 파일 방식 — 진행 중 스텝(조합) 사이에서 감지 ---

class Cancelled(Exception):
    """사용자 요청으로 작업 중단."""


def request_cancel(jid: str):
    (job_dir(jid) / "CANCEL").write_text("1", encoding="ascii")


def cancel_requested(jid: str) -> bool:
    return (job_dir(jid) / "CANCEL").exists()


def check_cancel(jid: str):
    if cancel_requested(jid):
        raise Cancelled()
