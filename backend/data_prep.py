"""입력 데이터 준비: MAPbI3 n,k 확보(자동 다운로드 시도 → 실패 시 업로드 안내) + 변환."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CSV = DATA / "mapbi3_nk_phillips.csv"
TXT = DATA / "mapbi3_n_k.txt"
AM15 = DATA / "am15_approx.txt"

# Phillips 2015 (refractiveindex.info) — 후보 URL들 (사이트 구조 변동 대비)
NK_URLS = [
    "https://refractiveindex.info/database/data/other/CH3NH3PbI3/Phillips.yml",
    "https://raw.githubusercontent.com/polyanskiy/refractiveindex.info-database/master/database/data/other/CH3NH3PbI3/Phillips.yml",
    "https://raw.githubusercontent.com/polyanskiy/refractiveindex.info-database/master/database/data-nk/other/CH3NH3PbI3/Phillips.yml",
]
MANUAL_URL = "https://refractiveindex.info/?shelf=other&book=CH3NH3PbI3&page=Phillips"

# ASTM G-173 정밀본. 발행 기관: NLR(National Laboratory of the Rockies, 구 NREL — 2025-12 개명).
# 입수 경로는 pvlib 프로젝트의 GitHub 공개 사본 (2026-07-07 내용 검증: 500nm global=1.5451 등
# pvlib 문서 기준값과 일치) + 기관 사이트 직링크. 세 곳 순차 시도.
AM15_URLS = [
    "https://raw.githubusercontent.com/pvlib/pvlib-python/main/pvlib/data/ASTMG173.csv",
    "https://cdn.jsdelivr.net/gh/pvlib/pvlib-python@main/pvlib/data/ASTMG173.csv",
    # NLR 공식 스프레드시트는 xls라 파서가 다름 — csv 미러 실패 시 수동 안내로 유도
]
AM15_PAGE = "https://www.nlr.gov/grid/solar-resource/spectra-am1.5"  # 2026-07-07 접속·직링크 확인
AM15_XLS = "https://www.nlr.gov/media/docs/libraries/grid/astmg173.xls"  # 공식 스프레드시트 직링크


# 데이터셋 레지스트리 — 새 케이스가 데이터를 요구하면 여기에 항목만 추가하면
# ② 데이터 준비 탭에 자동 표시되고, 케이스 실행 전 검증도 자동으로 걸린다.
DATASETS = [
    {"id": "am15", "name": "태양광 스펙트럼 AM1.5", "file": "am15_approx.txt",
     "columns": 2, "format": "텍스트 2열: 파장[nm], 스펙트럼 조도[W/m^2/nm]",
     "required_by": ["si_demo", "perovskite_thickness", "perovskite_etl_stack"],
     "note": "기본본은 COMSOL 예제 동봉 근사(39점). '자동 다운로드 시도'를 누르면 ASTM G-173 "
             "global tilt 정밀본(280-4000nm, 2002점)으로 교체됨 — 발행: NLR(구 NREL, 2025-12 개명), "
             "입수: pvlib 공개 사본. 수동 교체 시 링크 페이지의 astmg173 스프레드시트를 CSV로 저장해 업로드",
     "source_url": AM15_PAGE,
     "auto_fetch": True},
    {"id": "mapbi3_nk", "name": "MAPbI3 굴절률 n,k (Phillips 2015)", "file": "mapbi3_n_k.txt",
     "columns": 3, "format": "텍스트 3열: 파장[um], n, k — CSV/YML 업로드 시 자동 변환",
     "required_by": ["perovskite_thickness", "perovskite_etl_stack"],
     "note": "흡수계수 α=4πk/λ 계산에 사용",
     "source_url": MANUAL_URL,
     "auto_fetch": True},
]


def dataset(did):
    return next((d for d in DATASETS if d["id"] == did), None)


# --- 데이터 이력(어떤 파일이 언제 어떻게 들어왔나) + 목적 적합성 검사 ---
META = DATA / "datasets_meta.json"
UPLOADS = DATA / "uploads"  # 업로드 원본 보관함 — 변환 전 파일을 그대로 보존, 언제든 재적용 가능


def _archive_upload(did, filename, content: bytes):
    """업로드 원본을 data/uploads/{did}/{타임스탬프}_{파일명}으로 보관."""
    import datetime
    safe = Path(filename).name or "upload.dat"
    folder = UPLOADS / did
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    p = folder / f"{stamp}_{safe}"
    p.write_bytes(content)
    return p


def list_uploads():
    """데이터셋별 보관함 목록 (최신 먼저)."""
    out = {}
    for d in DATASETS:
        folder = UPLOADS / d["id"]
        out[d["id"]] = [{"name": p.name, "size": p.stat().st_size}
                        for p in sorted(folder.iterdir(), key=lambda q: q.name, reverse=True)
                        if p.is_file()] if folder.exists() else []
    return out


def apply_archived(did, name):
    """보관함의 원본 파일을 다시 검증·변환해 현재 데이터로 적용 (재업로드 불필요)."""
    p = UPLOADS / Path(did).name / Path(name).name
    if not p.exists():
        return {"ok": False, "logs": [f"보관 파일 없음: {name}"]}
    orig = p.name.split("_", 2)[-1]  # 'YYYYMMDD_HHMMSS_원본명' → 원본명
    res = save_upload_generic(did, orig, p.read_bytes(), archive=False)
    res["logs"] = [f"보관함에서 적용: {p.name}"] + res.get("logs", [])
    return res


def _load_meta():
    import json
    try:
        return json.loads(META.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_meta(did, origin, filename, rows, detail="", warnings=None, archived=None):
    import datetime
    import json
    m = _load_meta()
    m[did] = {"origin": origin, "filename": filename, "rows": rows, "detail": detail,
              "warnings": warnings or [], "archived": archived,
              "saved_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}
    META.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")


def _purpose_check(did, pts):
    """업로드/다운로드된 데이터가 '이 목적'에 맞는지 — 단위·범위 휴리스틱 경고 목록 반환."""
    import numpy as np
    try:
        a = np.asarray(pts, dtype=float)
    except Exception:
        return []
    w = []
    if a.ndim != 2 or a.shape[0] < 5:
        return ["데이터 점이 너무 적습니다 (5행 미만) — 파일 확인 필요"]
    wl = a[:, 0]
    if did == "am15":
        if wl.max() < 100:
            w.append("파장이 100 미만 — 단위가 um으로 보입니다. nm 단위 파일이 필요합니다")
        elif wl.min() > 305 or wl.max() < 845:  # ±5nm 허용 (경계 끝 기여 미미)
            w.append(f"파장 범위 {wl.min():g}-{wl.max():g}nm — 케이스가 쓰는 300-850nm를 전부 덮지 못합니다")
        if (a[:, 1] < 0).any():
            w.append("음수 조도 값이 있습니다 — 열 순서(파장, 조도) 확인 필요")
    elif did == "mapbi3_nk":
        if wl.max() > 100:
            w.append("파장이 100 초과 — 단위가 nm로 보입니다. um 단위(예: 0.55)가 필요합니다")
        elif wl.min() > 0.305 or wl.max() < 0.845:  # ±5nm 허용 (경계 끝 기여 미미)
            w.append(f"파장 범위 {wl.min():g}-{wl.max():g}um — 광생성 적분 0.30-0.85um를 전부 덮지 못합니다")
        if a.shape[1] >= 3 and ((a[:, 1] <= 0).any() or (a[:, 2] < 0).any()):
            w.append("n<=0 또는 k<0 값이 있습니다 — 열 순서(파장, n, k) 확인 필요")
    return w


def status():
    out = []
    meta = _load_meta()
    for d in DATASETS:
        p = DATA / d["file"]
        rows = 0
        if p.exists():
            try:
                rows = sum(1 for ln in open(p, encoding="utf-8", errors="ignore")
                           if ln.strip() and not ln.lstrip().startswith("#"))
            except Exception:
                rows = -1
        out.append({"id": d["id"], "name": d["name"], "format": d["format"],
                    "required_by": d["required_by"], "note": d["note"],
                    "source_url": d["source_url"], "auto_fetch": d["auto_fetch"],
                    "ready": p.exists() and rows > 0, "rows": rows,
                    "meta": meta.get(d["id"])})
    return out


def missing_for(case_id):
    """케이스 실행 전 필수 데이터 검증용 (레지스트리 required_by + case.json data_requirements)."""
    st = status()
    need = {s["id"] for s in st if case_id in s["required_by"]}
    try:  # 동적 등록된 데이터 케이스의 요구 데이터
        from . import library
        need |= set(library.load_case(case_id).get("data_requirements") or [])
    except Exception:
        pass
    return [s for s in st if s["id"] in need and not s["ready"]]


def save_upload_generic(did, filename, content: bytes, archive=True):
    d = dataset(did)
    if not d:
        return {"ok": False, "logs": [f"알 수 없는 데이터셋: {did}"]}
    if did == "mapbi3_nk":
        return save_upload(filename, content, archive=archive)  # 기존 CSV/YML 변환 경로 재사용
    import io
    import numpy as np
    if did == "am15":  # G-173 원본 CSV(4열+헤더)를 그대로 올려도 변환 수용
        rows = _parse_g173(content.decode("utf-8", errors="ignore"))
        if len(rows) > 100:
            warns = _purpose_check("am15", rows)
            AM15.write_text("# uploaded by user, converted from G-173 CSV (global tilt column)\n"
                            + "\n".join(f"{wl:g} {g:.6g}" for wl, g in rows) + "\n",
                            encoding="utf-8")
            ap = _archive_upload("am15", filename, content) if archive else None
            _save_meta("am15", "업로드", filename, len(rows), "G-173 CSV에서 global tilt 열 추출",
                       warns, archived=(f"am15/{ap.name}" if ap else None))
            logs = [f"✔ '{filename}' 업로드 완료 → {d['file']} ({len(rows)}행)",
                    "검증: G-173 CSV 형식 인식, global tilt 열 변환"]
            logs += [f"⚠️ {x}" for x in warns] or ["목적 적합성: 문제 없음 (케이스 필요 범위 커버)"]
            if ap:
                logs.append(f"원본 보관: data/uploads/am15/{ap.name} — 보관함에서 언제든 재적용 가능")
            return {"ok": True, "logs": logs}
    try:
        arr = np.loadtxt(io.BytesIO(content), encoding="utf-8")
    except Exception as e:
        return {"ok": False, "logs": [f"✖ '{filename}' 거부 — 숫자 표로 읽지 못했습니다: {e}",
                                      "필요 형식: " + d["format"]]}
    if arr.ndim != 2 or arr.shape[1] != d["columns"]:
        return {"ok": False, "logs": [f"✖ '{filename}' 거부 — 열 수가 맞지 않습니다: {arr.shape} "
                                      f"(필요: {d['columns']}열)", "필요 형식: " + d["format"]]}
    warns = _purpose_check(did, arr)
    (DATA / d["file"]).write_bytes(content)
    ap = _archive_upload(did, filename, content) if archive else None
    _save_meta(did, "업로드", filename, int(arr.shape[0]), f"{d['columns']}열 숫자표 검증 통과",
               warns, archived=(f"{did}/{ap.name}" if ap else None))
    logs = [f"✔ '{filename}' 업로드 완료 → {d['file']} ({arr.shape[0]}행)",
            f"검증: {d['columns']}열 숫자표 확인"]
    logs += [f"⚠️ {x}" for x in warns] or ["목적 적합성: 문제 없음"]
    if ap:
        logs.append(f"원본 보관: data/uploads/{did}/{ap.name} — 보관함에서 언제든 재적용 가능")
    return {"ok": True, "logs": logs}


def _parse_g173(text):
    """ASTMG173.csv(NLR/구 NREL 배포) → [(파장 nm, global tilt W/m^2/nm)]. 헤더 2줄은 float 실패로 자동 스킵."""
    rows = []
    for ln in text.splitlines():
        parts = ln.strip().split(",")
        if len(parts) < 3:
            continue
        try:
            wl, g = float(parts[0]), float(parts[2])  # 열: wavelength, extraterrestrial, global, direct
        except ValueError:
            continue
        rows.append((wl, g))
    return rows


def _fetch_am15():
    """AM1.5 정밀본 다운로드 → 검증 → am15_approx.txt 교체 (기존 근사본 덮어씀)."""
    import numpy as np
    import requests
    logs = []
    for url in AM15_URLS:
        try:
            r = requests.get(url, timeout=30)
            if not (r.ok and len(r.text) > 10000):
                logs.append(f"실패: {url} status={r.status_code} len={len(r.text)}")
                continue
            rows = _parse_g173(r.text)
            # 검증 1: 규모·범위 (G-173은 280-4000nm 2002점)
            if len(rows) < 1900 or rows[0][0] != 280.0 or rows[-1][0] != 4000.0:
                logs.append(f"검증 실패: 점수/범위 이상 ({len(rows)}점, {rows[0][0]}-{rows[-1][0]}nm)")
                continue
            # 검증 2: 기준값 대조 (pvlib 문서: 500nm→1.5451, 800nm→1.0725 W/m^2/nm)
            v = dict(rows)
            for wl, ref in [(500.0, 1.5451), (800.0, 1.0725)]:
                if abs(v[wl] - ref) > 1e-3:
                    raise ValueError(f"기준값 불일치 {wl}nm: {v[wl]} != {ref}")
            # 검증 3: 적분 = ASTM 공칭 100.04 mW/cm² 근처
            arr = np.array(rows)
            _trapz = getattr(np, "trapezoid", None) or np.trapz
            pin = float(_trapz(arr[:, 1], arr[:, 0]) * 0.1)
            if not (99.0 < pin < 101.0):
                logs.append(f"검증 실패: 적분 Pin={pin:.2f} mW/cm² (기대 ~100.04)")
                continue
            # 주의: 데이터 파일 헤더는 ASCII만 — 한국어 Windows에서 cp949 디코딩 사고 방지 (2026-07-07 교훈)
            head = ("# ASTM G-173-03 global tilt 37deg (NLR, formerly NREL) - wavelength[nm], irradiance[W/m^2/nm]\n"
                    f"# source: {url}\n"
                    f"# original: {AM15_PAGE} (pvlib public copy, verified 2026-07-07)\n")
            AM15.write_text(head + "\n".join(f"{wl:g} {g:.6g}" for wl, g in rows) + "\n",
                            encoding="utf-8")
            _save_meta("am15", "자동 다운로드", "ASTMG173.csv (pvlib 사본)", len(rows),
                       f"3중 검증 통과 (2002점/기준값/적분 {pin:.2f} mW/cm²)")
            logs.append(f"OK: {url}")
            logs.append(f"검증 통과: {len(rows)}점, 280-4000nm, 적분 Pin={pin:.2f} mW/cm² (ASTM 공칭 100.04)")
            logs.append(f"저장됨: {AM15.name} — 이후 실행부터 Pin과 스펙트럼 모양이 정밀본 기준으로 바뀝니다")
            return {"ok": True, "logs": logs}
        except Exception as e:
            logs.append(f"오류: {url} -> {type(e).__name__}: {e}")
    logs.append(f"자동 다운로드 실패 — 수동: {AM15_PAGE} 의 astmg173 시트에서 CSV 저장 후 업로드")
    return {"ok": False, "logs": logs}


def try_fetch_generic(did):
    """데이터셋별 자동 다운로드 진입점 (프론트 '자동 다운로드 시도' 버튼)."""
    if did == "am15":
        return _fetch_am15()
    if did == "mapbi3_nk":
        return try_fetch()
    return {"ok": False, "logs": [f"자동 다운로드 미지원 데이터셋: {did}"]}


def try_fetch():
    """서버(사용자 PC)에서 직접 다운로드 시도. 성공 시 변환까지."""
    import requests
    logs = []
    for url in NK_URLS:
        try:
            r = requests.get(url, timeout=20)
            if r.ok and len(r.text) > 500:
                logs.append(f"OK: {url} ({len(r.text)} bytes)")
                if url.endswith(".yml"):
                    rows = _parse_yml(r.text)
                else:
                    CSV.write_text(r.text, encoding="utf-8")
                    rows = _parse_csv(r.text)
                if rows:
                    _write_txt(rows)
                    warns = _purpose_check("mapbi3_nk", rows)
                    _save_meta("mapbi3_nk", "자동 다운로드", url.rsplit("/", 1)[-1], len(rows),
                               "Phillips 2015 (refractiveindex.info)", warns)
                    logs.append(f"변환 완료: {TXT} ({len(rows)}행)")
                    logs += [f"⚠️ {x}" for x in warns]
                    return {"ok": True, "logs": logs}
                logs.append("파싱 실패 (0행)")
            else:
                logs.append(f"실패: {url} status={r.status_code} len={len(r.text)}")
        except Exception as e:
            logs.append(f"오류: {url} -> {type(e).__name__}: {e}")
    logs.append(f"자동 다운로드 실패 — 브라우저에서 수동 다운로드 후 업로드: {MANUAL_URL}")
    return {"ok": False, "logs": logs}


def save_upload(filename: str, content: bytes, archive=True):
    text = content.decode("utf-8", errors="ignore")
    rows = _parse_yml(text) if filename.endswith((".yml", ".yaml")) else _parse_csv(text)
    if not rows:
        return {"ok": False, "logs": [f"✖ '{filename}' 파싱 실패 — 파일 앞부분을 Claude에게 보여주세요",
                                      text[:400]]}
    CSV.write_bytes(content)
    _write_txt(rows)
    warns = _purpose_check("mapbi3_nk", rows)
    ap = _archive_upload("mapbi3_nk", filename, content) if archive else None
    _save_meta("mapbi3_nk", "업로드", filename, len(rows), "CSV/YML → 3열(um, n, k) 변환",
               warns, archived=(f"mapbi3_nk/{ap.name}" if ap else None))
    logs = [f"✔ '{filename}' 업로드 완료 → {TXT.name} ({len(rows)}행)"]
    logs += [f"⚠️ {x}" for x in warns] or ["목적 적합성: 문제 없음 (0.30-0.85um 커버)"]
    if ap:
        logs.append(f"원본 보관: data/uploads/mapbi3_nk/{ap.name} — 보관함에서 언제든 재적용 가능")
    return {"ok": True, "logs": logs}


def _parse_csv(text):
    """refractiveindex.info CSV: 'wl,n' 블록 + 'wl,k' 블록 세로 적층 또는 wl,n,k 단일표."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    blocks, cur = [], None
    for ln in lines:
        low = ln.lower().replace(" ", "")
        if low.startswith("wl,") or low.startswith("wavelength"):
            cur = []
            blocks.append(cur)
            continue
        parts = ln.split(",")
        if len(parts) == 3 and cur is None:
            try:
                blocks.append([(float(parts[0]), float(parts[1]), float(parts[2]))])
                cur = blocks[-1]
                continue
            except ValueError:
                pass
        if cur is not None and len(parts) >= 2:
            try:
                cur.append(tuple(float(x) for x in parts[: 3 if len(parts) >= 3 else 2]))
            except ValueError:
                pass
    if len(blocks) == 1 and blocks[0] and len(blocks[0][0]) == 3:
        return [(r[0], r[1], r[2]) for r in blocks[0]]
    if len(blocks) >= 2:
        nmap = {r[0]: r[1] for r in blocks[0]}
        kmap = {r[0]: r[1] for r in blocks[1]}
        return [(wl, nmap[wl], kmap[wl]) for wl in sorted(set(nmap) & set(kmap))]
    return []


def _parse_yml(text):
    """refractiveindex.info YAML: 'data: |' 아래 'wl n k' 공백 구분 표."""
    rows = []
    in_data = False
    for ln in text.splitlines():
        if "data:" in ln:
            in_data = True
            continue
        if in_data:
            parts = ln.split()
            if len(parts) >= 3:
                try:
                    rows.append((float(parts[0]), float(parts[1]), float(parts[2])))
                    continue
                except ValueError:
                    pass
            if rows:
                break
    return rows


def _write_txt(rows):
    with open(TXT, "w", encoding="utf-8") as f:
        for wl, n, k in rows:
            f.write(f"{wl}\t{n}\t{k}\n")
