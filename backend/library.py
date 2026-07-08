"""재료 라이브러리·참고문헌·케이스(JSON) 로더 — v0.3 아키텍처 (계획서 14절).

케이스 = 데이터: cases/<id>/case.json이 레이어 스택·스윕·폼 스키마를 선언하고,
재료 물성은 data/materials.json(값마다 ref id·버전), 출처는 data/references.json.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CASES_DIR = ROOT / "cases"


def _load_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def load_materials() -> dict:
    return _load_json(DATA / "materials.json")


def load_references() -> dict:
    return _load_json(DATA / "references.json")


def material_props(mid: str, version: str | None = None) -> dict:
    mats = load_materials()
    if mid not in mats:
        raise ValueError(f"재료 라이브러리에 없음: {mid} (등록: {sorted(mats)})")
    m = mats[mid]
    ver = version or m["default_version"]
    if ver not in m["versions"]:
        raise ValueError(f"재료 {mid}에 버전 {ver} 없음 (등록: {sorted(m['versions'])})")
    v = m["versions"][ver]
    missing_ref = [k for k in v["props"] if k not in v.get("refs", {})]
    if missing_ref:
        raise ValueError(f"재료 {mid}/{ver}: 출처(ref) 없는 물성 {missing_ref} — 임의값 금지 원칙 위반")
    return {"props": dict(v["props"]), "refs": dict(v["refs"]), "version": ver,
            "name": m.get("name", mid)}


def load_case(case_id: str) -> dict:
    p = CASES_DIR / case_id / "case.json"
    if not p.exists():
        raise ValueError(f"케이스 정의 없음: {p}")
    case = _load_json(p)
    if case.get("id") != case_id:
        raise ValueError(f"case.json id 불일치: {case.get('id')} != {case_id}")
    return case


def case_summary(case_id: str) -> dict:
    """UI 케이스 목록용 (id/name/desc/schema + engine — 상단 엔진 탭 필터용)."""
    c = load_case(case_id)
    return {"id": c["id"], "name": c["name"], "desc": c["desc"], "schema": c["schema"],
            "engine": c.get("engine", "comsol")}


def _fill(value, values: dict):
    """'{t_c60}' 같은 자리표시자를 값으로 치환. 숫자는 그대로."""
    if isinstance(value, str) and "{" in value:
        out = value
        for k, v in values.items():
            out = out.replace("{" + k + "}", str(v))
        if "{" in out:
            raise ValueError(f"치환되지 않은 자리표시자: {out}")
        return out
    return value


# ---------------- 케이스 생성 모드: 초안 검증 → 등록 (사용자 승인 후 호출) ----------------

PROPS_REQUIRED = ["Eg", "chi", "epsr", "Nc", "Nv", "mun", "mup"]


def _draft_placeholders(layers) -> set:
    import re
    found = set()

    def scan(v):
        if isinstance(v, str):
            found.update(re.findall(r"\{([a-zA-Z0-9_]+)\}", v))
        elif isinstance(v, dict):
            for x in v.values():
                scan(x)
        elif isinstance(v, list):
            for x in v:
                scan(x)

    scan(layers)
    return found


def validate_case_draft(draft: dict) -> dict:
    """초안이 등록 가능한지 검사 (등록은 하지 않음). problems=구조 오류, needs=사용자가 채워야 할 것."""
    import re
    problems, needs = [], []
    if not isinstance(draft, dict):
        return {"ok": False, "problems": ["초안이 JSON 객체가 아님"], "needs": []}
    if not draft.get("feasible_1d", False):
        # 불가 판정이면 여기서 종료 — 빈 case에 대한 중복 구조 오류를 나열하지 않음
        return {"ok": False,
                "problems": ["AI가 1D 스택으로 표현 불가 판정 (feasible_1d=false) — rationale 참고. "
                             "이런 요청은 새 빌더 개발(Claude에게 요청)이 필요합니다"],
                "needs": []}
    case = draft.get("case") or {}
    for k in ("id", "name", "desc", "layers", "generation", "voltage", "grid", "schema"):
        if k not in case:
            problems.append(f"case.{k} 누락")
    cid = str(case.get("id", ""))
    if not re.fullmatch(r"[a-z0-9_]{3,40}", cid):
        problems.append(f"케이스 id 형식 오류: {cid!r} (소문자/숫자/_ 3~40자)")
    elif (CASES_DIR / cid / "case.json").exists():
        problems.append(f"이미 존재하는 케이스 id: {cid} — 방금 등록한 케이스라면 추가 등록 없이 "
                        "② 케이스 목록에서 선택해 쓰면 됩니다. 수정판을 새로 등록하려면 초안의 id를 바꿔주세요")
    layers = case.get("layers") or []
    if len(layers) < 2:
        problems.append("레이어가 2개 이상이어야 함")
    if sum(1 for l in layers if l.get("absorber")) != 1:
        problems.append("absorber 레이어가 정확히 1개여야 함")
    dops = {(l.get("doping") or {}).get("type") for l in layers if l.get("doping")}
    if not {"donor", "acceptor"} <= dops:
        problems.append("donor와 acceptor 도핑이 모두 있어야 다이오드가 됩니다")
    # 재료: 라이브러리 재사용 or materials_new(물성 7개 + 출처 전부)
    mats = load_materials()
    refs_all = load_references()
    refs_new = draft.get("references_new") or {}
    new_mats = {m.get("id"): m for m in (draft.get("materials_new") or [])}
    for l in layers:
        mid = l.get("material")
        if not mid:
            problems.append(f"레이어 '{l.get('name')}': material 누락")
            continue
        if mid in mats:
            continue
        nm = new_mats.get(mid)
        if not nm:
            needs.append(f"재료 '{mid}': 물성 정보 없음 — materials_new에 7개 물성+출처 필요")
            continue
        p_missing = [p for p in PROPS_REQUIRED if p not in (nm.get("props") or {})]
        r_missing = [p for p in PROPS_REQUIRED if p not in (nm.get("refs") or {})]
        if p_missing:
            needs.append(f"재료 '{mid}': 물성 누락 {p_missing}")
        if r_missing:
            needs.append(f"재료 '{mid}': 출처(ref) 누락 {r_missing} — 임의값 금지 원칙")
        for p, rid in (nm.get("refs") or {}).items():
            r = refs_new.get(rid)
            if rid not in refs_all and not (r and r.get("citation") and r.get("url")):
                needs.append(f"재료 '{mid}'.{p}: ref '{rid}'의 서지정보(citation+url) 없음")
    for m in (draft.get("materials_missing") or []):
        needs.append(f"재료 '{m.get('id')}': AI가 출처를 확신하지 못함 ({m.get('reason', '')}) — "
                     f"누락 물성 {m.get('missing_props', [])}을 출처와 함께 채워 다시 적용하세요")
    # 데이터셋 (새 데이터셋 등록은 아직 수동)
    from . import data_prep
    ds_ids = {d["id"] for d in data_prep.DATASETS}
    gen = case.get("generation") or {}
    for k in ("spectrum_dataset", "nk_dataset"):
        if gen.get(k) not in ds_ids:
            problems.append(f"generation.{k}='{gen.get(k)}' — 등록된 데이터셋이 아님 {sorted(ds_ids)}")
    v = case.get("voltage") or {}
    if not all(k in v for k in ("start", "stop", "step")):
        problems.append("voltage.start/stop/step 필요")
    # 자리표시자 ↔ grid/defaults/schema 일관성
    schema_keys = {f.get("key") for f in (case.get("schema") or [])}
    grid = case.get("grid") or {}
    gx, gy = grid.get("x") or {}, grid.get("y") or {}
    if not (gx and gy):
        problems.append("grid.x와 grid.y 둘 다 필요 (스윕 안 할 축은 값 1개 목록으로)")
    grid_params = {g.get("param") for g in (gx, gy) if g}
    grid_fields = {g.get("field") for g in (gx, gy) if g}
    if grid_fields - schema_keys:
        problems.append(f"grid field {sorted(grid_fields - schema_keys)}가 schema에 없음")
    unresolved = (_draft_placeholders(layers) - grid_params
                  - set((case.get("defaults") or {}).keys()) - schema_keys)
    if unresolved:
        problems.append(f"자리표시자 {sorted(unresolved)}가 grid/defaults/schema 어디에도 정의되지 않음")
    if "mode" not in schema_keys:
        problems.append("schema에 mode 필드(select: local/export) 필요")
    return {"ok": not problems and not needs, "problems": problems, "needs": needs}


def register_case_draft(draft: dict) -> dict:
    """검증 통과한 초안을 실제 등록: references/materials 병합 → case.json 저장 → 스모크 테스트."""
    import datetime
    v = validate_case_draft(draft)
    if not v["ok"]:
        return {"ok": False, **v, "logs": ["등록 거부 — 위 문제를 해결한 초안을 다시 적용하세요"]}
    case = draft["case"]
    cid = case["id"]
    logs = []
    refs = load_references()
    for rid, r in (draft.get("references_new") or {}).items():
        if rid in refs:
            continue
        refs[rid] = {"citation": str(r["citation"]), "url": str(r["url"]),
                     "used_for": [f"케이스 {cid} 신규 재료 (생성 모드, 사용자 승인)"]}
        logs.append(f"참고문헌 추가: {rid}")
    mats = load_materials()
    for m in (draft.get("materials_new") or []):
        mid = m["id"]
        if mid in mats:
            logs.append(f"재료 {mid}: 이미 라이브러리에 있음 — 기존 값 사용")
            continue
        mats[mid] = {"name": m.get("name", mid), "default_version": "v1",
                     "versions": {"v1": {
                         "date": datetime.date.today().isoformat(),
                         "source_type": "literature",
                         "props": {k: str(x) for k, x in m["props"].items()},
                         "refs": {k: str(x) for k, x in m["refs"].items()},
                         "notes": str(m.get("notes", "")) + " [케이스 생성 모드 등록 — AI 제안 문헌값, 사용자 승인. 원문 대조 권장]"}}}
        logs.append(f"재료 추가: {mid} v1 (문헌값 — 원문 대조 권장)")
    (DATA / "references.json").write_text(json.dumps(refs, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    (DATA / "materials.json").write_text(json.dumps(mats, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
    d = CASES_DIR / cid
    d.mkdir(parents=True, exist_ok=True)
    (d / "case.json").write_text(json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8")
    logs.append(f"케이스 저장: cases/{cid}/case.json")
    # 스모크 테스트: 실제 로더·물성 검증 경로로 통독 (그리드 첫 값 조합)
    try:
        summary = case_summary(cid)
        vals = {}
        gx, gy = case["grid"]["x"], case["grid"]["y"]
        for g in (gx, gy):
            fld = next(f for f in case["schema"] if f["key"] == g["field"])
            vals[g["param"]] = str(fld["default"]).split(",")[0].strip()
        for f in case["schema"]:
            if f["key"] not in ("mode", gx["field"], gy["field"]):
                vals.setdefault(f["key"], str(f["default"]))
        layers = resolve_layers(case, vals)
        logs.append(f"스모크 테스트 통과: 레이어 {len(layers)}개 해석 OK (물성·출처 검증 포함)")
    except Exception as e:
        try:
            (d / "case.json").unlink()
        except Exception:
            pass
        return {"ok": False, "problems": [f"등록 후 검증 실패 — 케이스는 롤백됨: {e}"],
                "needs": [], "logs": logs}
    logs.append("등록 완료 — ② 케이스 선택 목록에 즉시 나타납니다 (서버 재시작 불필요)")
    return {"ok": True, "case": summary, "logs": logs}


def resolve_layers(case: dict, values: dict) -> list:
    """레이어 선언 → 구체 값으로 해석된 레이어 목록 (두께 nm float, 물성 dict, 도핑/SRH)."""
    merged = dict(case.get("defaults", {}))
    merged.update(values)
    out = []
    for lay in case["layers"]:
        t = _fill(lay["thickness_nm"], merged)
        mat = material_props(lay["material"])
        item = {
            "name": lay["name"],
            "material": lay["material"],
            "material_version": mat["version"],
            "thickness_nm": float(t),
            "props": mat["props"],
            "refs": mat["refs"],
            "absorber": bool(lay.get("absorber", False)),
        }
        if "doping" in lay:
            d = lay["doping"]
            item["doping"] = {"type": d["type"], "conc": _fill(d["conc"], merged)}
        if "srh" in lay:
            item["srh"] = {k: _fill(v, merged) for k, v in lay["srh"].items()}
        out.append(item)
    if not any(l["absorber"] for l in out):
        raise ValueError("absorber 레이어가 없음")
    return out
