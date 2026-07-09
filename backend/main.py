"""COMSOL AI 앱 백엔드 (FastAPI).

UI(frontend/index.html)와 REST API 제공. 이 API는 이후 연구 AI 에이전트가
도구로 직접 호출하는 것을 전제로 설계 (UI는 교체 가능한 껍데기).

API 요약 (에이전트 연동용):
  GET  /api/health                       서버/환경 상태
  GET  /api/data/status                  입력 데이터 준비 상태
  POST /api/data/nk/fetch                MAPbI3 n,k 자동 다운로드 시도
  POST /api/data/nk/upload               n,k 파일 업로드 (csv/yml)
  GET  /api/cases                        케이스 목록 + 입력 스키마
  POST /api/diagnostics                  환경 점검 작업 생성 → job id
  POST /api/cases/{case_id}/run          케이스 실행 작업 생성 → job id
  GET  /api/jobs                         작업 목록
  GET  /api/jobs/{jid}                   작업 상태
  GET  /api/jobs/{jid}/log               작업 로그 (text)
  GET  /api/jobs/{jid}/artifacts         산출물 목록
  GET  /api/jobs/{jid}/artifacts/{name}  산출물 다운로드 (.mph/.png/.csv)
  POST /api/jobs/{jid}/upload_solved     오프라인 서버 솔브본 업로드 → 결과 추출 작업
  GET  /api/engines                      엔진(COMSOL/Solcore/SCAPS/IonMonger/Driftfusion/QE) 상태
  POST /api/engines/settings             엔진 경로 설정 (settings.json)
  POST /api/engines/{eid}/import         외부 실행 결과 업로드 → 판독 작업 (docs/ENGINES.md)
"""
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse

ROOT = Path(__file__).resolve().parents[1]
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")  # ANTHROPIC_API_KEY 등 (서버 재시작 시 반영)
except ImportError:
    pass

from . import comsol_cases, data_prep, jobs, llm_propose, runner  # noqa: E402

app = FastAPI(title="COMSOL AI App", version="0.2")


@app.on_event("startup")
def _startup():
    runner.start_worker()
    from . import sysmon
    sysmon.start()


@app.get("/api/sysmon")
def sysmon_data(since: float = 0.0):
    """시스템 사용량 샘플(2s 간격, 최근 4h)·작업 시작/종료 이벤트 — 좌측 모니터 패널용."""
    from . import sysmon
    return sysmon.data(since)


@app.get("/")
def index():
    return FileResponse(ROOT / "frontend" / "index.html")


@app.get("/api/health")
def health():
    try:
        import mph  # noqa
        mph_ok = True
    except Exception:
        mph_ok = False
    return {"ok": True, "mph_importable": mph_ok,
            "comsol_session": runner._client is not None,
            "llm": llm_propose.status()}


@app.get("/api/data/status")
def data_status():
    return data_prep.status()


@app.post("/api/data/nk/fetch")
def nk_fetch():
    return data_prep.try_fetch()


@app.post("/api/data/fetch/{dataset_id}")
def data_fetch(dataset_id: str):
    """데이터셋별 자동 다운로드 (am15: G-173 정밀본, NLR(구 NREL) / mapbi3_nk: Phillips n,k)."""
    return data_prep.try_fetch_generic(dataset_id)


@app.post("/api/data/nk/upload")
async def nk_upload(file: UploadFile):
    content = await file.read()
    return data_prep.save_upload(file.filename or "upload.csv", content)


@app.post("/api/data/upload/{dataset_id}")
async def data_upload(dataset_id: str, file: UploadFile):
    return data_prep.save_upload_generic(dataset_id, file.filename or "", await file.read())


@app.get("/api/data/uploads")
def data_uploads():
    """업로드 원본 보관함 목록 (데이터셋별, 최신 먼저)."""
    return data_prep.list_uploads()


@app.post("/api/data/uploads/{dataset_id}/apply")
def data_apply_archived(dataset_id: str, body: dict):
    """보관함 파일을 현재 데이터로 재적용 (재업로드 불필요)."""
    return data_prep.apply_archived(dataset_id, str((body or {}).get("name", "")))


@app.get("/api/data/uploads/{dataset_id}/{name}")
def data_download_archived(dataset_id: str, name: str):
    p = data_prep.UPLOADS / Path(dataset_id).name / Path(name).name  # 경로 탈출 방지
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, filename=p.name)


@app.get("/api/cases")
def cases():
    return comsol_cases.get_cases()


@app.post("/api/cases/register")
def register_case(body: dict):
    """케이스 생성 모드: 검토 완료된 초안을 등록 (임의값 금지 검증 + 스모크 테스트 포함)."""
    from . import library
    try:
        return library.register_case_draft((body or {}).get("draft") or {})
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/references")
def references():
    """참고문헌 저장소 (v0.3 — ⑤ 참고문헌 탭은 M2에서 UI 추가 예정)."""
    from . import library
    return library.load_references()


@app.get("/api/nl/status")
def nl_status():
    return llm_propose.status()


@app.post("/api/nl/propose")
def nl_propose(body: dict):
    text = str((body or {}).get("text", "")).strip()
    if not text:
        raise HTTPException(400, "요청 문장이 비어 있습니다")
    try:
        return llm_propose.propose(text)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/nl/prompt")
def nl_prompt(body: dict):
    """복붙 모드 1단계: 구독 챗에 붙여넣을 프롬프트 생성 (API 불필요)."""
    text = str((body or {}).get("text", "")).strip()
    if not text:
        raise HTTPException(400, "요청 문장이 비어 있습니다")
    return {"prompt": llm_propose.build_prompt(text)}


@app.post("/api/nl/apply")
def nl_apply(body: dict):
    """복붙 모드 2단계: 챗 AI의 JSON 응답 검증·적용 (API 불필요)."""
    try:
        return llm_propose.parse_response(str((body or {}).get("response_text", "")))
    except Exception as e:
        raise HTTPException(400, str(e))


@app.delete("/api/cases/{case_id}")
def delete_case(case_id: str):
    """데이터 케이스 삭제 — 완전 삭제 대신 cases/_deleted/로 이동 (복구 가능)."""
    import shutil
    import time
    from . import library
    p = library.CASES_DIR / Path(case_id).name
    if not (p / "case.json").exists():
        raise HTTPException(400, "삭제할 수 없는 케이스입니다 (코드 내장 케이스이거나 존재하지 않음)")
    dst = library.CASES_DIR / "_deleted" / f"{p.name}_{time.strftime('%Y%m%d_%H%M%S')}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(p), str(dst))
    return {"ok": True,
            "message": f"케이스 '{case_id}'를 보관함(cases/_deleted)으로 이동했습니다. "
                       "재료·참고문헌·실행 이력은 남습니다. 복구하려면 폴더를 되돌리면 됩니다."}


@app.post("/api/nl/case_prompt")
def nl_case_prompt(body: dict):
    """케이스 생성 모드 1단계: 새 케이스 초안용 프롬프트 생성 (복붙, API 불필요)."""
    text = str((body or {}).get("text", "")).strip()
    if not text:
        raise HTTPException(400, "요청 문장이 비어 있습니다")
    return {"prompt": llm_propose.build_case_prompt(text)}


@app.post("/api/nl/case_apply")
def nl_case_apply(body: dict):
    """케이스 생성 모드 2단계: 챗 AI의 초안 JSON 검증 (등록은 /api/cases/register)."""
    try:
        return llm_propose.parse_case_response(str((body or {}).get("response_text", "")))
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/diagnostics")
def run_diagnostics():
    jid = jobs.create_job("diagnostics", {})
    runner.submit(jid)
    return {"job_id": jid}


@app.post("/api/diagnostics/quick")
def run_quick_check():
    jid = jobs.create_job("quick_check", {})
    runner.submit(jid)
    return {"job_id": jid}


@app.post("/api/diagnostics/spike/{dim}")
def run_spike(dim: int):
    """v2 차원 확장 스파이크 (2D/3D 빌더 API 확정용)."""
    if dim not in (2, 3):
        raise HTTPException(400, "dim은 2 또는 3")
    jid = jobs.create_job("spike_nd", {"dim": dim})
    runner.submit(jid)
    return {"job_id": jid}


@app.post("/api/diagnostics/spike_wo")
def run_spike_wo():
    """Wave Optics API 확정 스파이크 (v3 광학·전기 결합 — 예제 덤프)."""
    jid = jobs.create_job("spike_wo", {})
    runner.submit(jid)
    return {"job_id": jid}


@app.post("/api/diagnostics/spike_wo2")
def run_spike_wo2(body: dict | None = None):
    """WO-2: ewfd 슬랩 vs TMM 대조 검증."""
    jid = jobs.create_job("spike_wo2", body or {})
    runner.submit(jid)
    return {"job_id": jid}


@app.post("/api/diagnostics/spike_wo3")
def run_spike_wo3(body: dict | None = None):
    """WO-3: λ 스윕 → G(x,y) 합성 (슬랩 vs Beer-Lambert)."""
    jid = jobs.create_job("spike_wo3", body or {})
    runner.submit(jid)
    return {"job_id": jid}


@app.post("/api/diagnostics/spike_ibc")
def run_spike_ibc(body: dict | None = None):
    """IBC 2D 빌더 스파이크 (Union 선택·수렴·전하분포·ETL 판별 실험용)."""
    jid = jobs.create_job("spike_ibc", body or {})
    runner.submit(jid)
    return {"job_id": jid}


@app.post("/api/cases/{case_id}/run")
def run_case(case_id: str, params: dict):
    if case_id not in comsol_cases.case_ids():  # 동적 목록 (등록된 데이터 케이스 포함)
        raise HTTPException(404, "no such case")
    # schema 기본값 병합 (요청 값이 우선) — API 직접 호출에서도 hpc_only 잠금·
    # s_ifc_cms 등 기본 파라미터가 유지되게 (2026-07-08: 누락 시 증발 버그)
    p = comsol_cases.schema_defaults(case_id)
    p.update(params or {})
    p["case_id"] = case_id
    # 사용 엔진을 작업 기록에 명시 (④ 목록·상세 표기용 — 케이스 삭제 후에도 이력 유지)
    p["engine"] = next((c.get("engine", "comsol") for c in comsol_cases.get_cases()
                        if c.get("id") == case_id), "comsol")
    jid = jobs.create_job("case_run", p)
    runner.submit(jid)
    return {"job_id": jid}


# ---------- 멀티 엔진 (2026-07-08): 상단 엔진 탭 + 경로 설정 + 결과 가져오기 ----------

@app.get("/api/engines")
def engines_list():
    from . import engines
    return {"engines": engines.engines_status(),
            "settings": engines.load_settings(),
            "setting_keys": engines.SETTING_KEYS}


@app.post("/api/engines/settings")
def engines_settings(body: dict):
    from . import engines
    engines.save_settings(body or {})
    return {"ok": True, "engines": engines.engines_status(),
            "settings": engines.load_settings()}


@app.post("/api/engines/{engine_id}/check")
def engine_check(engine_id: str):
    """엔진별 환경 점검 작업 (COMSOL 외 — 동글 불필요 엔진은 동글 없이 완료)."""
    from . import engines
    if engine_id not in [e["id"] for e in engines.engines_status()]:
        raise HTTPException(404, "no such engine")
    jid = jobs.create_job("engine_check", {"engine": engine_id})
    runner.submit(jid)
    return {"job_id": jid}


@app.post("/api/engines/{engine_id}/import")
async def engine_import(engine_id: str, case_id: str, files: list[UploadFile]):
    """외부 프로그램 실행 결과 파일 업로드 → 파싱 작업 생성 (SCAPS .iv, MATLAB csv, QE .out 등)."""
    from . import engines
    if engine_id not in [e["id"] for e in engines.engines_status()]:
        raise HTTPException(404, "no such engine")
    p = comsol_cases.schema_defaults(case_id) if case_id else {}
    p.update({"engine": engine_id, "case_id": case_id})
    jid = jobs.create_job("engine_import", p)
    jd = jobs.job_dir(jid)
    names = []
    for f in files:
        safe = Path(f.filename or "upload.dat").name
        (jd / safe).write_bytes(await f.read())
        names.append(safe)
    jobs.log(jid, f"결과 파일 업로드됨: {', '.join(names)}")
    runner.submit(jid)
    return {"job_id": jid, "files": names}


@app.get("/api/jobs")
def list_jobs(limit: int = 500):
    """기본 500건 — 이전 기본 50은 활발한 날 하루치에 밀려 이전 날짜가 사라졌음 (2026-07-08).
    날짜별 접기 UI라 수백 건도 가볍다."""
    return jobs.list_jobs(limit)


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    j = jobs.get_job(jid)
    if not j:
        raise HTTPException(404)
    return j


@app.post("/api/jobs/{jid}/replot")
def replot(jid: str, body: dict):
    """결과 그림 재생성 — 축 범위(x/y min·max)·범례 위치 조정 (jv_curves.csv 기반)."""
    if not jobs.get_job(jid):
        raise HTTPException(404)
    try:
        return comsol_cases.replot_jv(jid, body or {})
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/jobs/{jid}/cancel")
def cancel_job(jid: str):
    """실행 중간 멈춤 — 진행 중인 스텝(현재 조합)이 끝나는 대로 정지, 완료분은 보존."""
    j = jobs.get_job(jid)
    if not j:
        raise HTTPException(404)
    if j["status"] in ("done", "failed", "cancelled"):
        return {"ok": False, "message": "이미 끝난 작업입니다"}
    jobs.request_cancel(jid)
    return {"ok": True, "message": "중단 요청됨 — 진행 중인 조합이 끝나는 대로 멈춥니다 (완료분 결과 보존)"}


@app.get("/api/jobs/{jid}/log", response_class=PlainTextResponse)
def get_log(jid: str):
    return jobs.read_log(jid)


@app.get("/api/jobs/{jid}/artifacts")
def get_artifacts(jid: str):
    return jobs.artifacts(jid)


@app.get("/api/jobs/{jid}/artifacts/{name}")
def download(jid: str, name: str):
    p = jobs.job_dir(jid) / Path(name).name  # 경로 탈출 방지
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, filename=p.name)


@app.post("/api/jobs/{jid}/upload_solved")
async def upload_solved(jid: str, file: UploadFile):
    if not jobs.get_job(jid):
        raise HTTPException(404)
    name = Path(file.filename or "solved.mph").name
    if not name.endswith(".mph"):
        raise HTTPException(400, ".mph 파일만 업로드 가능")
    dst_job = jobs.create_job("extract_solved", {"filename": name, "source_job": jid})
    (jobs.job_dir(dst_job) / name).write_bytes(await file.read())
    runner.submit(dst_job)
    return {"job_id": dst_job}
