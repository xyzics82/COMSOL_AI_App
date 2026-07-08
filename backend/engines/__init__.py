"""멀티 엔진 프레임워크 (2026-07-08) — COMSOL 외 시뮬레이터 통합.

설계 원칙 (COMSOL 통합과 동일한 3-모드 패턴):
  local  : 이 PC에서 직접 실행 (프로그램 경로가 설정돼 있을 때)
  export : 입력 덱(입력 파일+실행 스크립트+절차 안내)만 생성 → 사용자가 직접/서버에서 실행
  import : 실행 결과 파일 업로드 → 파싱 → 지표·그림 (④와 동일한 회수 흐름)

엔진 상태는 /api/engines 로 노출, 경로 설정은 프로젝트 루트 settings.json (기밀 아님,
기계별 경로라 .gitignore). ⚠️ 표시는 실제 프로그램 첫 실행 때 사용자와 함께 검증할 부분.
"""
import json
import shutil
from glob import glob
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SETTINGS_FILE = ROOT / "settings.json"

# 설정 키와 의미 (전부 선택 — 없으면 해당 엔진은 export/import 모드만)
SETTING_KEYS = {
    "scaps_exe": "SCAPS 실행 파일 경로 (예: C:\\SCAPS\\scaps3310.exe)",
    "matlab_exe": "MATLAB 실행 파일 경로 (비우면 PATH의 matlab 탐색)",
    "ionmonger_path": "IonMonger 폴더 (git clone 위치)",
    "driftfusion_path": "Driftfusion 폴더 (git clone 위치)",
    "qe_distro": "QE가 설치된 WSL 배포판 이름 (비우면 자동 탐지 후 기억)",
}


def load_settings():
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_settings(updates: dict):
    s = load_settings()
    for k, v in (updates or {}).items():
        if k in SETTING_KEYS:
            s[k] = str(v or "").strip()
    SETTINGS_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    return s


def find_matlab():
    """MATLAB 실행 파일 탐색: 설정 → PATH → 표준 설치 폴더 glob."""
    s = load_settings()
    if s.get("matlab_exe") and Path(s["matlab_exe"]).exists():
        return s["matlab_exe"]
    w = shutil.which("matlab")
    if w:
        return w
    hits = sorted(glob(r"C:\Program Files\MATLAB\R20*\bin\matlab.exe"))
    return hits[-1] if hits else None


def find_scaps():
    s = load_settings()
    if s.get("scaps_exe") and Path(s["scaps_exe"]).exists():
        return s["scaps_exe"]
    hits = sorted(glob(r"C:\SCAPS*\scaps*.exe")) + sorted(glob(r"C:\Program Files*\SCAPS*\scaps*.exe"))
    return hits[-1] if hits else None


def find_repo(setting_key, default_name):
    """MATLAB 엔진 저장소 폴더: 설정 → tools/ 동봉 사본 (2026-07-08 사전 확보)."""
    s = load_settings()
    p = s.get(setting_key, "")
    if p and Path(p).exists():
        return Path(p)
    d = ROOT / "tools" / default_name
    return d if d.exists() else None


def _solcore_status():
    try:
        import solcore  # noqa
        caps = ["TMM(광학)"]
        try:
            from solcore.analytic_solar_cells import db  # noqa
            caps.append("Detailed Balance")
        except Exception:
            pass
        return True, f"solcore {solcore.__version__} — " + ", ".join(caps)
    except ImportError:
        return False, "미설치 — install_engines.bat 실행 또는 .venv\\Scripts\\pip install solcore"


def _matlab_engine_status(folder_key, repo_name, url):
    folder = find_repo(folder_key, repo_name)
    ml = find_matlab()
    if not folder:
        return False, f"{repo_name} 폴더 미설정 — {url} 를 내려받고 ② 엔진 설정에 경로 입력"
    if not ml:
        return False, (f"{repo_name} 저장소 OK ({folder}) / MATLAB 대기 — 평가판·정품 설치만 하면 "
                       "됩니다 (드라이버는 실코드 대조 완료)")
    return True, f"{repo_name} + MATLAB OK ({ml})"


def engines_status():
    """엔진 목록 + 가용성. 프론트 상단 탭이 이걸 그대로 그린다."""
    out = []
    # COMSOL (기존)
    try:
        import mph  # noqa
        ok, detail = True, "MPh 가용 (동글 필요 작업은 ① 참조)"
    except ImportError:
        ok, detail = False, "MPh 미설치"
    out.append({"id": "comsol", "name": "COMSOL", "kind": "device",
                "available": ok, "detail": detail, "import_accept": ".mph",
                "modes": ["local", "export"],
                "requires": ["COMSOL 6.4+ (Semiconductor·Wave Optics 모듈)",
                             "🔑 USB 동글(라이선스) — 솔브·점검·결과 추출 시 필수"]})
    ok, detail = _solcore_status()
    out.append({"id": "solcore", "name": "Solcore", "kind": "device+optics",
                "available": ok, "detail": detail, "import_accept": "",
                "modes": ["local"],
                "requires": ["파이썬 패키지만 (install_engines.bat 1회) — 동글·외부 프로그램 불필요"]})
    sc = find_scaps()
    out.append({"id": "scaps", "name": "SCAPS-1D", "kind": "device",
                "available": bool(sc),
                "detail": (f"실행 파일 OK: {sc} (⚠️ CLI 구동은 첫 실행 검증 필요)" if sc else
                           "미탐지 — 겐트대에 이메일 신청 후 설치, ② 엔진 설정에 scaps 경로 입력. 설치 전에도 export(레시피·스크립트 생성)와 import(.iv 업로드)는 사용 가능"),
                "import_accept": ".iv,.qe,.txt,.dat", "modes": ["local", "export", "import"],
                "requires": ["SCAPS 설치 (겐트대 이메일 신청, 무료) — 동글 불필요",
                             "설치 전에도 레시피 생성·.iv 판독은 사용 가능"]})
    ok, detail = _matlab_engine_status("ionmonger_path", "IonMonger",
                                       "github.com/PerovskiteSCModelling/IonMonger")
    out.append({"id": "ionmonger", "name": "IonMonger", "kind": "device+ions",
                "available": ok, "detail": detail, "import_accept": ".csv,.mat",
                "modes": ["local", "export", "import"],
                "requires": ["MATLAB (툴박스 불필요, -batch 사용)",
                             "IonMonger 저장소 (tools/ 동봉 — 설정 불필요)", "동글 불필요"]})
    ok, detail = _matlab_engine_status("driftfusion_path", "Driftfusion",
                                       "github.com/barnesgroupICL/Driftfusion")
    out.append({"id": "driftfusion", "name": "Driftfusion", "kind": "device+ions",
                "available": ok, "detail": detail, "import_accept": ".csv,.mat",
                "modes": ["local", "export", "import"],
                "requires": ["MATLAB (툴박스 불필요)", "Driftfusion 저장소 (tools/ 동봉)",
                             "동글 불필요"]})
    try:
        import ase  # noqa
        qe_extra = f" · CIF 변환 가능(ase {ase.__version__})"
    except ImportError:
        qe_extra = " · CIF 변환은 ase 설치 필요(install_engines.bat)"
    from .qe import wsl_exe
    if wsl_exe():
        qe_detail = "local(WSL) 실행 가능 — 덱 생성→pw.x 실행→판독 자동" + qe_extra + \
                    " (pw.x 유무는 실행 시 확인)"
    else:
        qe_detail = "반출 전용: 입력 덱 생성→서버 실행→결과 업로드 (WSL 설치 시 local 가능)" + qe_extra
    out.append({"id": "qe", "name": "QE (계면·원자)", "kind": "atomistic",
                "available": True, "detail": qe_detail,
                "import_accept": ".out,.dat,.txt,.xml,.cif", "modes": ["local", "export", "import"],
                "requires": ["WSL + quantum-espresso (또는 서버 반출) — 동글 불필요",
                             "의사퍼텐셜 UPF (Si는 자동 다운로드, 그 외 SSSP에서)",
                             "CIF 변환 시 ase (install_engines.bat)"]})
    return out


def run_check(jid, params, log, get_client=None):
    """엔진별 실점검 작업 (kind=engine_check) — COMSOL의 '빠른 점검'에 대응.
    각 엔진의 실제 실행 경로를 가볍게 두드려 본다 (동글 불필요 엔진은 동글 없이 완료)."""
    eid = str(params.get("engine"))
    log(f"[{eid}] 환경 점검 시작")
    if eid == "solcore":
        from . import solcore_engine
        return solcore_engine.check(jid, params, log)
    if eid == "scaps":
        from . import scaps
        return scaps.check(jid, params, log)
    if eid == "ionmonger":
        from . import ionmonger
        return ionmonger.check(jid, params, log)
    if eid == "driftfusion":
        from . import driftfusion
        return driftfusion.check(jid, params, log)
    if eid == "qe":
        from . import qe
        return qe.check(jid, params, log)
    raise RuntimeError(f"점검 미지원 엔진: {eid} (COMSOL은 ①의 빠른/전체 점검 사용)")


def run_case(engine_id, jid, params, log, case):
    """엔진별 케이스 실행 디스패치 (comsol은 comsol_cases가 직접 처리)."""
    if engine_id == "solcore":
        from . import solcore_engine
        return solcore_engine.run(jid, params, log, case)
    if engine_id == "scaps":
        from . import scaps
        return scaps.run(jid, params, log, case)
    if engine_id == "ionmonger":
        from . import ionmonger
        return ionmonger.run(jid, params, log, case)
    if engine_id == "driftfusion":
        from . import driftfusion
        return driftfusion.run(jid, params, log, case)
    if engine_id == "qe":
        from . import qe
        return qe.run(jid, params, log, case)
    raise RuntimeError(f"알 수 없는 엔진: {engine_id}")


def run_import(jid, params, log, get_client=None):
    """결과 파일 가져오기 작업 (kind=engine_import). 파일은 엔드포인트가 작업 폴더에 저장해 둠."""
    eid = str(params.get("engine"))
    if eid == "scaps":
        from . import scaps
        return scaps.import_results(jid, params, log)
    if eid == "ionmonger":
        from . import ionmonger
        return ionmonger.import_results(jid, params, log)
    if eid == "driftfusion":
        from . import driftfusion
        return driftfusion.import_results(jid, params, log)
    if eid == "qe":
        from . import qe
        return qe.import_results(jid, params, log)
    raise RuntimeError(f"이 엔진은 결과 가져오기를 지원하지 않습니다: {eid}")
