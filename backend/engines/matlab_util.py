"""MATLAB 배치 실행 공용 러너 (IonMonger·Driftfusion 공용).

matlab -batch "명령" (R2019a+) 사용: GUI 없이 실행, 종료 코드 반환, 스크립트 오류 시 비0.
취소: 작업 큐의 CANCEL 센티널을 폴링하다 프로세스 트리 종료.
⚠️ MATLAB 미설치 상태에서 작성됨(구입 예정) — 첫 실행 때 함께 검증할 것:
   -batch 인자 이스케이프, 한글 경로, 종료 코드 전달.
"""
import subprocess
import time
from pathlib import Path

from .. import jobs
from . import find_matlab


def run_matlab(jid, log, workdir: Path, command: str, timeout_s=3600):
    """workdir에서 matlab -batch command 실행. stdout/stderr → matlab_console.txt.
    반환: 종료 코드. 실패해도 예외 대신 코드 반환(호출부가 로그 안내)."""
    ml = find_matlab()
    if not ml:
        raise RuntimeError("MATLAB을 찾지 못했습니다 — ② 엔진 설정에서 matlab.exe 경로를 지정하세요 "
                           "(예: C:\\Program Files\\MATLAB\\R2025a\\bin\\matlab.exe)")
    con = workdir / "matlab_console.txt"
    log(f"  MATLAB 실행: -batch (작업 폴더 {workdir.name}, 타임아웃 {timeout_s}s)")
    # -sd: 시작 폴더 지정 (cd 이스케이프 문제 회피)
    proc = subprocess.Popen([ml, "-batch", command, "-sd", str(workdir)],
                            cwd=str(workdir),
                            stdout=open(con, "w", encoding="utf-8", errors="replace"),
                            stderr=subprocess.STDOUT)
    t0 = time.time()
    try:
        while True:
            rc = proc.poll()
            if rc is not None:
                break
            if time.time() - t0 > timeout_s:
                proc.kill()
                raise RuntimeError(f"MATLAB 타임아웃({timeout_s}s) — 콘솔 로그 확인: {con.name}")
            try:
                jobs.check_cancel(jid)
            except jobs.Cancelled:
                proc.kill()
                raise
            time.sleep(2.0)
    finally:
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass
    log(f"  MATLAB 종료 코드 {rc} ({time.time()-t0:.0f}s) — 콘솔: {con.name}")
    return rc


def matlab_str(s):
    """MATLAB 문자열 리터럴 이스케이프."""
    return "'" + str(s).replace("'", "''") + "'"
