"""단일 워커 스레드: COMSOL 작업을 순차 실행 (동시 솔브 1개 정책).

MPh 클라이언트는 프로세스당 1개를 지연 생성해 재사용한다.
이 모듈만 COMSOL을 만진다 — 나중에 연구 에이전트가 백엔드를 도구로 쓸 때도 동일 경로.
"""
import queue
import threading
import traceback

from . import jobs

_q: "queue.Queue[str]" = queue.Queue()
_client = None
_client_lock = threading.Lock()


def get_client(log):
    """MPh/COMSOL 세션 지연 시작 (최초 1회, 수십 초 걸릴 수 있음).

    라이선스(동글) 실패는 사용자에게 명확한 안내가 가도록 메시지를 가공한다.
    """
    global _client
    with _client_lock:
        if _client is None:
            log("COMSOL 세션 시작 중 (최초 1회, 시간이 걸립니다)...")
            import mph
            try:
                _client = mph.start(cores=2)
            except Exception as e:
                msg = str(e)
                low = msg.lower()
                if any(k in low for k in ("license", "hasp", "dongle", "flexnet", "lmgrd")):
                    raise RuntimeError(
                        "[동글 필요] COMSOL 라이선스를 얻지 못했습니다. "
                        "USB 동글이 꽂혀 있는지 확인한 뒤 작업을 다시 실행하세요. "
                        f"(원인: {msg[:200]})") from e
                raise RuntimeError(
                    "COMSOL 세션 시작 실패 — COMSOL 설치/동글 상태를 확인하세요. "
                    f"(원인: {msg[:300]})") from e
            log(f"COMSOL 세션 시작 완료: version={_client.version}")
        return _client


def submit(jid: str):
    _q.put(jid)


def _work_loop():
    from . import comsol_cases, diagnostics  # 지연 import (기동 빠르게)
    handlers = {
        "diagnostics": diagnostics.run,
        "quick_check": diagnostics.quick,
        "spike_nd": diagnostics.spike_nd,
        "spike_ibc": diagnostics.spike_ibc,
        "spike_wo": diagnostics.spike_wo,
        "spike_wo2": diagnostics.spike_wo2,
        "spike_wo3": diagnostics.spike_wo3,
        "case_run": comsol_cases.run_case,
        "extract_solved": comsol_cases.run_extract_solved,
    }
    from . import engines  # 멀티 엔진 결과 가져오기·환경 점검 (2026-07-08)
    handlers["engine_import"] = engines.run_import
    handlers["engine_check"] = engines.run_check
    while True:
        jid = _q.get()
        job = jobs.get_job(jid)
        if not job:
            continue
        log = lambda t, _j=jid: jobs.log(_j, t)  # noqa: E731
        if jobs.cancel_requested(jid):  # 대기열에서 이미 취소된 작업
            jobs.set_status(jid, "cancelled", "시작 전 취소됨")
            log("작업이 시작 전에 취소되었습니다")
            _q.task_done()
            continue
        jobs.set_status(jid, "running")
        from . import sysmon  # 사용량 그래프의 작업 시작/종료 마커 (2026-07-09)
        _label = job["params"].get("case_id") or job["params"].get("engine") or job["kind"]
        sysmon.add_event("start", jid, _label)
        _final = "done"
        try:
            handler = handlers.get(job["kind"])
            if handler is None:
                raise ValueError(f"알 수 없는 작업 종류: {job['kind']}")
            handler(jid, job["params"], log, get_client)
            jobs.set_status(jid, "done")
            log("작업 완료")
        except jobs.Cancelled:
            _final = "cancelled"
            jobs.set_status(jid, "cancelled", "사용자 요청으로 중단 — 완료분 결과는 산출물에 보존")
            log("작업 중단됨 (사용자 요청) — 그때까지 완료된 결과는 산출물에 남아 있습니다")
        except Exception as e:
            _final = "failed"
            jobs.set_status(jid, "failed", str(e)[:500])
            log("작업 실패:\n" + traceback.format_exc())
        finally:
            sysmon.add_event("end", jid, _label, _final)
            _q.task_done()


_thread = threading.Thread(target=_work_loop, daemon=True, name="comsol-worker")


def start_worker():
    if not _thread.is_alive():
        try:  # 서버 재시작으로 고아가 된 작업 정리 (좀비 'running' 방지, 2026-07-08)
            for jb in jobs.list_jobs(300):
                if jb["status"] in ("running", "queued"):
                    jobs.set_status(jb["id"], "failed", "서버 재시작으로 중단됨 — 다시 실행하세요")
        except Exception:
            pass
        _thread.start()
