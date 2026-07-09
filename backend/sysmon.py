"""시스템 사용량 모니터 (2026-07-09) — 좌측 탭 하단 그래프용.

- 2초 간격 샘플링: CPU%, RAM(%, GB), GPU(%·VRAM — nvidia-smi 있을 때만)
- 링버퍼: 최근 4시간(7200점) — 지난 사용량 스크롤 열람용
- 이벤트 마커: 작업 시작/종료(runner가 기록) → 그래프에 세로선 표시
- psutil 필요 (install_engines.bat에 포함). 없으면 비활성 상태를 API로 알림.
"""
import subprocess
import threading
import time
from collections import deque

_SAMPLES = deque(maxlen=7200)   # (t, cpu%, ram%, ram_gb, gpu%, vram_gb) — 2s 간격 4시간
_EVENTS = deque(maxlen=400)     # (t, "start"|"end", jid, label, status)
_started = False
_gpu_ok = None  # None=미확인, False=없음


def _gpu_sample():
    """nvidia-smi 1회 질의 → (gpu%, vram_GB) 또는 None."""
    global _gpu_ok
    if _gpu_ok is False:
        return None
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, timeout=5, text=True)
        line = out.stdout.strip().splitlines()[0]
        util, mem = [float(x.strip()) for x in line.split(",")[:2]]
        _gpu_ok = True
        return util, mem / 1024.0
    except Exception:
        _gpu_ok = False
        return None


def _loop():
    try:
        import psutil
    except ImportError:
        return  # psutil 없으면 샘플링 안 함 (status()가 안내)
    psutil.cpu_percent(None)  # 첫 호출 기준점
    while True:
        try:
            cpu = psutil.cpu_percent(None)
            vm = psutil.virtual_memory()
            g = _gpu_sample()
            _SAMPLES.append((round(time.time(), 1), round(cpu, 1),
                             round(vm.percent, 1), round(vm.used / 2**30, 2),
                             (round(g[0], 1) if g else None),
                             (round(g[1], 2) if g else None)))
        except Exception:
            pass
        time.sleep(2.0)


def start():
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, daemon=True, name="sysmon").start()


def add_event(kind, jid, label="", status=""):
    """runner가 호출: 작업 시작/종료 마커."""
    _EVENTS.append((round(time.time(), 1), kind, jid, str(label)[:60], status))


def status():
    try:
        import psutil
        vm = psutil.virtual_memory()
        return {"active": True, "ram_total_gb": round(vm.total / 2**30, 1),
                "gpu": bool(_gpu_ok)}
    except ImportError:
        return {"active": False,
                "reason": "psutil 미설치 — install_engines.bat 실행 후 앱 재시작"}


def data(since=0.0):
    """since(unix time) 이후 샘플·이벤트."""
    s = [x for x in _SAMPLES if x[0] > since]
    e = [x for x in _EVENTS if x[0] > since]
    return {"samples": s, "events": e, "now": round(time.time(), 1), **status()}
