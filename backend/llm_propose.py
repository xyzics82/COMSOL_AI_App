"""자연어 → 케이스 제안 + 입력값 채움 (Phase 2: 파이프라인 S1~S3).

작업 원칙 반영:
- 임의값 금지: 사용자가 명시하지 않은 값은 지어내지 않고 기본값 유지 + assumed_defaults로 표시
- 미지원 요청은 case_id='none'으로 정직하게 알림 (템플릿 우선 전략)
- 모델: Claude Sonnet 5 (계획서 결정 3 — 파싱/폼 단계는 Sonnet 티어)
- 구조화 출력은 tool_choice 강제(JSON 스키마 준수 보장)
"""
import json
import os

from . import comsol_cases

MODEL = os.environ.get("COMSOL_AI_MODEL", "claude-sonnet-5")

# 케이스 목록은 호출 시점에 동적 조회 — 새 케이스 등록이 재시작 없이 반영되도록
def _cases():
    return comsol_cases.get_cases()


def _case_ids():
    return comsol_cases.case_ids() + ["none"]


PROPOSE_TOOL = {
    "name": "propose_case",
    "description": "사용자 요청에 맞는 시뮬레이션 케이스와 입력값을 제안한다",
    "input_schema": {
        "type": "object",
        "properties": {
            "case_id": {"type": "string"},
            "params": {"type": "object",
                       "description": "케이스 스키마 key별 값(문자열). 사용자가 명시했거나 명확히 환산 가능한 것만"},
            "assumed_defaults": {"type": "array", "items": {"type": "string"},
                                 "description": "기본값을 그대로 쓰는 항목과 그 값 (예: 'τ=38.7ns (기본값)')"},
            "missing": {"type": "array", "items": {"type": "string"},
                        "description": "실행 전 사용자 확인이 꼭 필요한 누락 정보"},
            "warnings": {"type": "array", "items": {"type": "string"},
                         "description": "모델 한계·주의사항 (예: v0는 박막 간섭 무시)"},
            "rationale": {"type": "string", "description": "케이스 선택 근거, 한국어 2~3문장"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        },
        "required": ["case_id", "params", "rationale", "confidence"],
    },
}

SYSTEM_RULES = (
    "너는 COMSOL 시뮬레이션 웹앱의 케이스 선택 도우미다. 사용자의 자연어 요청을 읽고 "
    "아래 케이스 중 하나를 골라 입력값을 채운다.\n"
    "규칙:\n"
    "1) 사용자가 명시하지 않은 값은 절대 지어내지 말 것. 기본값을 그대로 두고 assumed_defaults에 나열한다.\n"
    "2) 단위 환산은 허용한다(예: 0.5 um → 500). thicknesses_nm은 쉼표로 구분된 nm 숫자 목록 문자열이다. "
    "'300에서 800까지 100 간격' 같은 표현은 목록으로 전개한다.\n"
    "3) 케이스 목록으로 감당할 수 없는 요청(다른 물리, 2D/3D, 다른 소자 등)은 case_id='none'으로 하고 "
    "rationale에 이유와 가장 가까운 케이스를 설명한다.\n"
    "4) 페로브스카이트 케이스는 Beer-Lambert 광흡수의 1D p-i-n 이상화 모델(v0)이며 박막 간섭·수송층을 "
    "무시한다 — 관련 요청이면 warnings에 이 한계를 적는다.\n"
    "5) 모든 텍스트 필드는 한국어로 쓴다."
)


def _output_format_for_chat():
    return (
        "\n\n출력 형식: 아래 스키마의 JSON 객체 '하나만' ```json 코드블록으로 출력하라. "
        "코드블록 밖에는 아무 것도 쓰지 마라.\n"
        '{"case_id": "' + " | ".join(_case_ids()) + '", '
        '"params": {"<필드key>": "<값(문자열)>"}, '
        '"assumed_defaults": ["기본값 사용 항목"], "missing": ["확인 필요한 누락 정보"], '
        '"warnings": ["주의사항"], "rationale": "선택 근거(한국어)", '
        '"confidence": "high | medium | low"}'
    )


def _registry_json():
    registry = [{"id": c["id"], "name": c["name"], "desc": c["desc"], "fields": c["schema"]}
                for c in _cases()]
    return json.dumps(registry, ensure_ascii=False)


def build_prompt(text: str) -> str:
    """복붙 모드용 프롬프트 — 구독 챗(claude.ai 등)에 그대로 붙여넣는 용도."""
    return (SYSTEM_RULES + "\n케이스 목록(JSON):\n" + _registry_json()
            + _output_format_for_chat() + "\n\n사용자 요청:\n" + text)


def _normalize(data) -> dict:
    """API/복붙 공통 검증: case_id 확인, 알 수 없는 param 제거, 필드 형태 보정."""
    if not isinstance(data, dict):
        raise ValueError("JSON 객체가 아닙니다")
    case_id = data.get("case_id")
    valid = set(_case_ids())
    if case_id not in valid:
        raise ValueError(f"case_id가 유효하지 않습니다: {case_id!r} (허용: {sorted(valid)})")
    params = data.get("params") or {}
    warnings = [str(w) for w in (data.get("warnings") or [])]
    if case_id != "none":
        schema_keys = {f["key"] for c in _cases() if c["id"] == case_id
                       for f in c["schema"]}
        clean = {}
        for k, v in dict(params).items():
            if k in schema_keys:
                clean[k] = str(v)
            else:
                warnings.append(f"알 수 없는 입력 '{k}'은(는) 무시했습니다")
        params = clean
    conf = data.get("confidence")
    return {
        "case_id": case_id,
        "params": params,
        "assumed_defaults": [str(x) for x in (data.get("assumed_defaults") or [])],
        "missing": [str(x) for x in (data.get("missing") or [])],
        "warnings": warnings,
        "rationale": str(data.get("rationale") or ""),
        "confidence": conf if conf in ("high", "medium", "low") else "medium",
    }


def parse_response(raw: str) -> dict:
    """복붙 모드: 챗 AI 응답 텍스트에서 JSON을 찾아 검증. 코드블록/잡담 섞여도 견딤."""
    import re
    text = (raw or "").strip()
    if not text:
        raise ValueError("붙여넣은 내용이 비어 있습니다")
    candidates = []
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        candidates.append(m.group(1))
    i, j = text.find("{"), text.rfind("}")
    if i >= 0 and j > i:
        candidates.append(text[i:j + 1])
    last_err = None
    for c in candidates:
        try:
            out = _normalize(json.loads(c))
            out["source"] = "paste"
            return out
        except ValueError as e:
            last_err = e
        except json.JSONDecodeError as e:
            last_err = e
    raise ValueError(f"응답에서 유효한 JSON을 찾지 못했습니다 — AI 답변 전체를 그대로 붙여넣었는지 확인하세요 ({last_err})")


# ---------------- 케이스 생성 모드: 프롬프트 → 새 1D 케이스 초안 ----------------

CASE_DRAFT_RULES = (
    "너는 COMSOL 시뮬레이션 웹앱의 '케이스 설계자'다. 사용자의 요청을 이 앱이 바로 실행할 수 있는 "
    "1D 수직 다층 스택 반도체(태양전지) 케이스 정의(JSON)로 변환한다.\n"
    "앱의 실행 능력 (벗어나는 요청은 feasible_1d=false로 정직하게 알려라):\n"
    "- 1D 수직 스택만: 레이어를 한 방향으로 쌓는 구조. 2D/3D(IBC·패턴 전극·깍지형), 박막 간섭 광학, "
    "반도체 외 물리는 불가\n"
    "- 광흡수는 Beer-Lambert, absorber 레이어 1개에서만 광생성\n"
    "- absorber 재료는 n,k 데이터셋이 등록된 것만 가능: 현재 mapbi3 (mapbi3_nk)\n"
    "- 스윕은 grid x·y 두 축(레이어 값의 \"{자리표시자}\"). 한 축만 스윕하려면 다른 축은 값 1개 목록\n"
    "규칙:\n"
    "1) 값을 지어내지 마라. 새 재료의 물성 7개(Eg,chi,epsr,Nc,Nv,mun,mup)는 실제 문헌 출처(저자·저널·연도·DOI)와 "
    "함께 materials_new/references_new에 제시하고, 출처가 확실하지 않은 물성은 materials_missing에 넣어 "
    "사용자에게 요청하라. 가짜 논문을 만들지 마라.\n"
    "2) 기존 재료 라이브러리를 우선 재사용하라.\n"
    "3) 단위 표기(문자열): Eg/chi \"1.55[V]\", epsr \"10\", Nc/Nv \"1e18[1/cm^3]\", "
    "mun/mup \"10[cm^2/(V*s)]\", 도핑 conc \"1e18[1/cm^3]\", 두께는 nm 숫자 또는 \"{자리표시자}\"\n"
    "4) 케이스 id는 소문자/숫자/_ 3~40자 (기존과 다르게). schema에는 mode(select, options local/export, "
    "default local) 필드를 포함하라. grid의 field는 schema에 있는 text 필드(쉼표 목록)여야 한다.\n"
    "5) 스택 양끝에 acceptor(p형)와 donor(n형) 도핑이 있어 다이오드가 되게 하라 (좌우 방향은 앱이 자동 처리).\n"
    "6) 태양전지 J-V는 보통 voltage start 0, stop 1.2, step 0.02.\n"
    "7) 모든 텍스트는 한국어.\n"
)


def build_case_prompt(text: str) -> str:
    """복붙 모드용 케이스 생성 프롬프트 — 기존 케이스로 안 되는 요청을 새 케이스 초안으로."""
    from . import data_prep, library
    mats = library.load_materials()
    mat_brief = {mid: {"name": m.get("name"),
                       "props": sorted(m["versions"][m["default_version"]]["props"])}
                 for mid, m in mats.items()}
    ds_brief = [{"id": d["id"], "name": d["name"]} for d in data_prep.DATASETS]
    try:
        example = json.dumps(library.load_case("perovskite_etl_stack"), ensure_ascii=False)
    except Exception:
        example = "(예시 로드 실패)"
    out_schema = (
        '{"feasible_1d": true 또는 false, '
        '"case": <위 예시와 같은 구조의 새 케이스, feasible_1d=false면 null>, '
        '"materials_new": [{"id": "소문자id", "name": "이름", "props": {물성 7개}, '
        '"refs": {"물성": "ref_id"}, "notes": ""}], '
        '"references_new": {"ref_id": {"citation": "저자, 저널 권, 쪽 (연도)", "url": "https://doi.org/..."}}, '
        '"materials_missing": [{"id": "", "missing_props": [], "reason": ""}], '
        '"warnings": [], "rationale": "설계 근거(한국어)", "confidence": "high | medium | low"}'
    )
    return (CASE_DRAFT_RULES
            + "\n기존 재료 라이브러리: " + json.dumps(mat_brief, ensure_ascii=False)
            + "\n등록된 데이터셋: " + json.dumps(ds_brief, ensure_ascii=False)
            + "\n\n케이스 JSON 구조 예시 (실제 등록되어 작동 중인 케이스):\n" + example
            + "\n\n출력 형식: 아래 스키마의 JSON 객체 '하나만' ```json 코드블록으로 출력하라. "
              "코드블록 밖에는 아무 것도 쓰지 마라.\n" + out_schema
            + "\n\n사용자 요청:\n" + text)


def parse_case_response(raw: str) -> dict:
    """복붙 모드: 챗 AI의 케이스 초안 응답 → 초안 + 검증 결과 (등록은 별도 승인 단계)."""
    import re
    text = (raw or "").strip()
    if not text:
        raise ValueError("붙여넣은 내용이 비어 있습니다")
    candidates = []
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        candidates.append(m.group(1))
    i, j = text.find("{"), text.rfind("}")
    if i >= 0 and j > i:
        candidates.append(text[i:j + 1])
    draft, last_err = None, None
    for c in candidates:
        try:
            draft = json.loads(c)
            break
        except json.JSONDecodeError as e:
            last_err = e
    if draft is None:
        raise ValueError(f"응답에서 유효한 JSON을 찾지 못했습니다 ({last_err})")
    from . import library
    return {"draft": draft, "validation": library.validate_case_draft(draft), "source": "paste"}


def status():
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return {"ready": False,
                "reason": "anthropic 패키지 미설치 — start.bat을 다시 실행하면 자동 설치됩니다"}
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {"ready": False,
                "reason": "API 키 없음 — 프로젝트 폴더의 .env 파일에 ANTHROPIC_API_KEY를 넣고 서버를 재시작하세요 (SETUP.md 5절)"}
    return {"ready": True, "model": MODEL}


def propose(text: str) -> dict:
    st = status()
    if not st["ready"]:
        raise RuntimeError(st["reason"])
    import anthropic

    system = SYSTEM_RULES + "\n케이스 목록(JSON):\n" + _registry_json()

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        system=system,
        messages=[{"role": "user", "content": text}],
        tools=[PROPOSE_TOOL],
        tool_choice={"type": "tool", "name": "propose_case"},
    )
    block = next(b for b in resp.content if b.type == "tool_use")
    out = _normalize(dict(block.input))
    out["source"] = "api"
    out["model"] = MODEL
    out["usage"] = {"input_tokens": resp.usage.input_tokens,
                    "output_tokens": resp.usage.output_tokens}
    return out
