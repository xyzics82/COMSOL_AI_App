"""케이스 러너.

- si_demo: 설치된 Si 예제를 그대로 실행 → J-V/지표 추출. 앱 파이프라인 전체 검증용
  (COMSOL API 리스크 최소 — 이것부터 통과시켜 추출·플롯·다운로드 경로를 확정).
- perovskite_thickness: CASE_SPEC.md의 v0 (1D p-i-n) 빌드 초안.
  ⚠️ COMSOL Java API의 일부 속성명은 [초안]이며, '환경 점검'의 모델 덤프로 확정 후
  1~2회 수정 반복을 전제로 한다. 실패 시 로그가 원인 줄을 특정한다.

모든 임의성 배제: 물리 값은 cases/perovskite_thickness/params.py (출처 포함)에서만 온다.
"""
import shutil
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "cases" / "perovskite_thickness"))
import params as PRM  # noqa: E402  (cases/perovskite_thickness/params.py)

from . import jobs  # noqa: E402

import os as _os
_COMSOL_ROOT = Path(_os.environ.get("COMSOL_ROOT",
                                    r"C:\Program Files\COMSOL\COMSOL64\Multiphysics_copy1"))
SI_MPH = (_COMSOL_ROOT / "applications" / "Semiconductor_Module"
          / "Photonic_Devices_and_Sensors" / "si_solar_cell_1d.mph")

CASES = [
    {
        "id": "si_demo",
        "name": "Si 태양전지 데모 (파이프라인 검증)",
        "desc": "설치된 COMSOL 예제를 그대로 솔브해 J-V와 성능지표 추출. 문서 기대값: Voc≈0.61V, Isc≈33mA.",
        "schema": [
            {"key": "mode", "label": "실행 방식", "type": "select",
             "options": ["local", "export"], "default": "local"},
        ],
    },
    {
        "id": "perovskite_thickness",
        "name": "페로브스카이트 두께 스윕 (v0 초안)",
        "desc": "MAPbI3 1D p-i-n, 두께별 Jsc/Voc/FF/PCE. 사양: cases/perovskite_thickness/CASE_SPEC.md",
        "schema": [
            {"key": "thicknesses_nm", "label": "흡수층 두께 목록 [nm]", "type": "text",
             "default": ",".join(str(t) for t in PRM.SWEEP["thickness_nm"])},
            {"key": "taun_ns", "label": "SRH 수명 τ [ns] (출처: CASE_SPEC 4절)", "type": "number",
             "default": 38.7},
            {"key": "mode", "label": "실행 방식", "type": "select",
             "options": ["local", "export"], "default": "local"},
        ],
    },
]


def get_cases():
    """전체 케이스 목록 = 코드 케이스(위 CASES) + cases/*/case.json 데이터 케이스 (동적 탐색).

    새 케이스를 등록(폴더에 case.json 저장)하면 서버 재시작 없이 목록·실행에 바로 반영된다.
    """
    out = list(CASES)
    from . import library
    for p in sorted((ROOT / "cases").glob("*/case.json")):
        try:
            out.append(library.case_summary(p.parent.name))
        except Exception as e:  # 케이스 정의 파일 문제로 앱 전체가 죽지 않게
            print(f"[경고] 케이스 로드 실패 {p.parent.name}:", e)
    return out


def case_ids():
    return [c["id"] for c in get_cases()]


def schema_defaults(case_id):
    """케이스 schema의 default 값 dict. API 직접 호출 시에도 폼 기본값이 적용되게
    (누락 시 hpc_only 가드·s_ifc_cms 기본 등이 증발하는 문제 방지, 2026-07-08)."""
    for c in get_cases():
        if c.get("id") == case_id:
            return {f["key"]: f["default"] for f in c.get("schema", [])
                    if "default" in f and f.get("key")}
    return {}


def run_case(jid, params, log, get_client):
    case_id = str(params.get("case_id"))
    from . import data_prep
    missing = data_prep.missing_for(case_id)
    if missing:
        raise RuntimeError("필요한 입력 데이터가 없습니다: "
                           + ", ".join(m["name"] for m in missing)
                           + " — '데이터 준비' 탭에서 준비 후 다시 실행하세요")
    if str(params.get("dim", "1")).lower().replace("d", "") not in ("", "1") \
            and case_id in ("si_demo", "perovskite_thickness"):
        raise RuntimeError("이 케이스는 1D 전용(코드 내장)입니다 — 2D/3D 평판 검증은 "
                           "데이터 케이스(예: perovskite_etl_stack)에서 차원을 선택하세요")
    if case_id == "si_demo":
        _run_si_demo(jid, params, log, get_client)
    elif case_id == "perovskite_thickness":
        _run_perovskite(jid, params, log, get_client)
    elif (ROOT / "cases" / case_id / "case.json").exists():
        from . import library
        case = library.load_case(case_id)
        eng = case.get("engine", "comsol")
        if eng != "comsol":  # 멀티 엔진 디스패치 (2026-07-08) — COMSOL 세션 불필요
            from . import engines
            return engines.run_case(eng, jid, params, log, case)
        if case.get("builder") == "ibc2d":
            _run_ibc(jid, params, log, get_client)   # IBC 전용 2D 빌더
        elif case.get("builder") == "ibc3d":
            _run_ibc3d(jid, params, log, get_client)  # IBC 3D 단위 셀 (핑거 끝단)
        else:
            _run_stack(jid, params, log, get_client)  # 제네릭 1D/평판 스택
    else:
        raise ValueError(f"알 수 없는 케이스: {case_id}")


# ---------------- 공통: J-V 추출/지표/플롯 ----------------

def _v_vals(v_max=1.3, step=0.05, knee=0.9):
    """전압 스윕 값 목록(float): 0→knee는 step, knee→v_max는 step/2 (턴온 구간 미세화,
    2026-07-10 — 발산 지점이 전부 1.0~1.15V 부근이라 그 구간만 촘촘히)."""
    if not all(np.isfinite(x) for x in (v_max, step, knee)):
        raise ValueError("전압 스윕 값은 유한값이어야 합니다")
    if v_max < 0 or knee < 0 or step <= 0:
        raise ValueError("전압 상한·knee는 0 이상, 스윕 간격은 0보다 커야 합니다")
    vals = list(np.arange(0.0, min(knee, v_max) + 1e-9, step))
    if v_max > knee:
        vals += list(np.arange(knee + step / 2, v_max + 1e-9, step / 2))
    return vals


def _v_plist(v_max=1.3, step=0.05, knee=0.9):
    """_v_vals를 COMSOL plistarr 문자열로."""
    return " ".join(f"{v:.4g}" for v in _v_vals(v_max, step, knee))


def _diagnose_fail(label, hist, outcome=None, last_v=None):
    """실패 셀 진단문 — '조건만 조금 다른데 왜 실패했나'를 시도 이력 기반으로 설명.

    hist 항목: (hmax(None=기본), stage, 스윕점수, 도달V, 오류요지)
    stage: mesh(메시 생성 실패) / eq(평형 발산) / sweep(스윕 도중 발산) /
           eval(해 평가 실패) / 성공
    """
    lines = [f"■ {label} — 시도 {len(hist)}회 이력:"]
    for n, (hm, stage, pts, lv, err) in enumerate(hist, 1):
        mesh = f"{hm:g}nm" if hm else "기본(요청값)"
        if stage == "성공":
            lines.append(f"  {n}) 메시 {mesh}: 솔브 성공")
            continue
        if stage == "mesh":
            what = ("메시 생성 자체가 실패 — 이 치수 조합과 이 hmax의 상성 후보"
                    "(Swept 분할이 만들어지지 않음)")
        elif stage == "eq":
            what = "평형(Study 1)에서 뉴턴 발산 — 초기해·메시·물리 설정을 분리 진단해야 함"
        elif stage == "sweep":
            vtxt = f"{lv:.2f}" if lv is not None else "?"
            what = f"스윕(Study 2)이 V≈{vtxt}V({pts or 0}점 확보)에서 발산"
        elif stage == "eval":
            what = "솔브 뒤 결과 데이터셋 평가 또는 지표 추출 실패"
        else:
            what = stage
        lines.append(f"  {n}) 메시 {mesh}: {what}" + (f" [{err}]" if err else ""))
    lines.append(
        "  해석: 메시 사다리로 11건 이상 구제되어 메시-뉴턴 상성이 주원인임은 실증됐습니다."
        " 다만 W=2um 계열의 1.13V 반복 발산처럼 구조적 원인이 아직 분리되지 않은 사례가"
        " 있으므로 특정 셀의 원인을 메시 하나로 단정하지 않습니다. 메시가 달라진 결과는"
        " 별도의 메시 독립성 확인 전까지 정량적으로 동등하다고 단정할 수 없습니다.")
    if hist and hist[-1][1] == "성공":
        lines.append(f"  결말: 메시 사다리 {len(hist)}번째 시도에서 솔브 성공 — 지표는 정상값. "
                     "(지표 유효성은 별도 추출·메시 독립성 검사 대상)")
    elif outcome == "accepted":
        vtxt = f"~{last_v:.2f}V" if last_v is not None else "Voc 이후"
        lines.append(f"  조치: 기준을 통과한 부분 스윕({vtxt}) 회수 — J-V 곡선의 '*' 라벨이 이 셀입니다.")
    elif outcome == "preserved":
        vtxt = f"~{last_v:.2f}V" if last_v is not None else "기준 미달"
        lines.append(f"  조치: 부분 해({vtxt})는 보존했지만 수락 전압 미달이라 완전한 J-V 지표로 집계하지 않습니다.")
    else:
        lines.append("  조치: 메시 사다리 전 단계 소진 — 실패 단계와 서버 로그를 기준으로"
                     " 평형/스윕/메시 원인을 분리 실험해야 합니다.")
    return "\n".join(lines)


def _dataset_names(model):
    names = [None]
    try:
        names.extend(d.name() for d in (model / "datasets").children())
    except Exception:
        pass
    # 기본 데이터셋과 named 데이터셋이 같은 문자열로 중복되는 경우를 제거한다.
    return list(dict.fromkeys(names))


def _eval_array(model, expr, dataset):
    arr = (model.evaluate(expr) if dataset is None
           else model.evaluate(expr, dataset=dataset))
    return np.atleast_1d(np.asarray(arr, dtype=float)).ravel()


def _sweep_candidates(model, min_points=3):
    """실제 V0 값으로 식별한 스윕 데이터셋 목록(도달 V, 점 수 내림차순)."""
    out = []
    for ds in _dataset_names(model):
        try:
            v = _eval_array(model, "V0", ds)
            v = v[np.isfinite(v)]
            # 파라미터 배열이 아니라 한 전압이 공간 자유도 수만큼 반복된 평가는 제외.
            unique_v = np.unique(v)
            if v.size >= min_points and unique_v.size == v.size:
                out.append((float(np.max(v)), int(v.size), ds, v))
        except Exception:
            continue
    out.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return out


def _sweep_progress(model, min_points=3):
    """가장 멀리 도달한 실제 스윕의 (점 수, 최대 V, dataset)을 반환."""
    candidates = _sweep_candidates(model, min_points=min_points)
    if not candidates:
        return 0, None, None
    last_v, points, dataset, _ = candidates[0]
    return points, last_v, dataset


def _extract_iv(model, log, preferred_dataset=None):
    """전압 스윕 결과에서 (V, I) 배열 추출.

    스터디가 여러 개면 데이터셋도 여러 개 → V0가 스윕 배열(size>=3)로 나오는
    데이터셋을 자동 탐색한다. 전류 표현식도 후보 탐색.
    """
    sweeps = _sweep_candidates(model, min_points=3)
    if preferred_dataset is not None:
        sweeps.sort(key=lambda item: item[2] != preferred_dataset)
    if not sweeps:
        raise RuntimeError("V0 스윕 배열을 주는 데이터셋을 찾지 못함 — 로그 회신 요청")

    candidates = ["semi.I0_1", "semi.I0_2", "semi.mc1.I0", "semi.mc2.I0"]
    for _last_v, _points, chosen, V in sweeps:
        log(f"V0 후보: {V.size}점 [{V.min():.3g}..{V.max():.3g}] V "
            f"(dataset={chosen or '기본'})")
        for expr in candidates:
            try:
                I = _eval_array(model, expr, chosen)
                if I.size == V.size and np.ptp(np.abs(I)) > 0:
                    log(f"전류 표현식 채택: {expr} (dataset={chosen or '기본'})")
                    return V, I, expr
                log(f"  후보 {expr}: size={I.size} (부적합)")
            except Exception as e:
                log(f"  후보 {expr}: {type(e).__name__}")
    raise RuntimeError("터미널 전류 표현식을 찾지 못함 — 로그 회신 요청")


def _pin_mw_cm2(log):
    """스펙트럼 파일 적분으로 입사 전력 계산 (100mW/cm² 가정하지 않음 — CASE_SPEC 5절)."""
    arr = np.loadtxt(DATA / "am15_approx.txt", encoding="utf-8")  # cp949 기본값 회피 (한국어 Windows)
    wl_nm, F = arr[:, 0], arr[:, 1]          # nm, W/m^2/nm
    _trapz = getattr(np, "trapezoid", None) or np.trapz  # numpy 2.x에서 trapz가 trapezoid로 개명
    pin = _trapz(F, wl_nm) * 0.1             # W/m^2 -> mW/cm^2
    log(f"입사 전력 Pin = {pin:.2f} mW/cm² (AM1.5 근사 파일 적분값)")
    return pin


def _metrics(V, I_A, area_cm2, pin_mw_cm2, log):
    J = I_A * 1000.0 / area_cm2              # mA/cm^2
    j0 = float(J[np.argmin(np.abs(V))])
    Jgen = -J if j0 < 0 else J               # 발전 전류를 양수 좌표로 (부호 관례 자동 판별)
    log(f"J(V≈0) = {j0:.3f} mA/cm² (부호 관례 자동 처리)")
    Jsc = float(Jgen[np.argmin(np.abs(V))])
    # Voc: Jgen이 0으로 떨어지는 교차점 보간
    idx = np.where(Jgen <= 0)[0]
    if idx.size and idx[0] > 0:
        i2, i1 = idx[0], idx[0] - 1
        Voc = float(V[i1] + (V[i2] - V[i1]) * Jgen[i1] / (Jgen[i1] - Jgen[i2]))
    else:
        Voc = float("nan")
        log("⚠️ Voc 교차점 없음 — 스윕 범위 확대 필요할 수 있음")
    P = Jgen * V                             # mW/cm^2
    k = int(np.nanargmax(P))
    Pmax = float(P[k])
    FF = Pmax / (Jsc * Voc) if Jsc > 0 and Voc == Voc and Voc > 0 else float("nan")
    if Voc == Voc:
        PCE = 100.0 * Pmax / pin_mw_cm2
    else:
        # Voc 미도달 = MPP 미도달 — 스윕 끝점 전력을 PCE로 보고하면 허위 과대값이 됨
        Pmax, PCE = float("nan"), float("nan")
        log("⚠️ Voc 없음 → Pmax/PCE 무효(NaN) 처리")
    m = {"Jsc_mA_cm2": round(Jsc, 3), "Voc_V": round(Voc, 4), "FF": round(FF, 4),
         "PCE_pct": round(PCE, 3), "Pmax_mW_cm2": round(Pmax, 3),
         "Pin_mW_cm2": round(pin_mw_cm2, 2), "Vmpp_V": round(float(V[k]), 3)}
    log(f"지표: {m}")
    return m, Jgen


def _plot_jv(jid, curves, fname="jv.png", xlim=None, ylim=None, legend="best"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(6, 4.2))
    for label, V, Jgen in curves:
        ax.plot(V, Jgen, label=label)
    ax.set_xlabel("Voltage [V]")
    ax.set_ylabel("Current density [mA/cm$^2$]")
    ax.axhline(0, lw=0.6, color="k")
    if legend == "outside":
        ax.legend(fontsize=8, loc="center left", bbox_to_anchor=(1.02, 0.5))
    else:
        ax.legend(fontsize=8, loc=legend)
    ax.grid(alpha=0.3)
    if xlim:
        ax.set_xlim(xlim)
    if ylim:
        ax.set_ylim(ylim)
    fig.tight_layout()
    out = jobs.job_dir(jid) / fname
    fig.savefig(out, dpi=140)
    return out


def _save_curves(jid, curves):
    """J-V 원자료 보존 — 재플롯(축 범위·범례 조정)의 원천 데이터."""
    p = jobs.job_dir(jid) / "jv_curves.csv"
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write("label,V,J\n")
        for label, V, J in curves:
            for v, jv in zip(V, J):
                f.write(f"{label},{float(v):.6g},{float(jv):.6g}\n")


_LEGEND_LOCS = {"best", "upper right", "upper left", "lower right", "lower left",
                "center right", "center left", "outside"}


def replot_jv(jid, opts):
    """저장된 jv_curves.csv로 그림 재생성 (COMSOL 불필요, 즉시)."""
    import csv
    p = jobs.job_dir(jid) / "jv_curves.csv"
    if not p.exists():
        raise ValueError("이 작업에는 재플롯 원자료(jv_curves.csv)가 없습니다 — 이 기능 추가 이후 실행부터 지원")
    groups = {}
    with open(p, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            g = groups.setdefault(row["label"], ([], []))
            g[0].append(float(row["V"]))
            g[1].append(float(row["J"]))
    curves = [(k, np.array(v), np.array(j)) for k, (v, j) in groups.items()]
    fname = Path(str(opts.get("fname") or "jv.png")).name
    if not (fname.startswith("jv") and fname.endswith(".png")):
        raise ValueError("jv*.png 그림만 재플롯 가능합니다")

    def _pair(lo_k, hi_k):
        lo, hi = opts.get(lo_k), opts.get(hi_k)
        lo = float(lo) if lo not in (None, "") else None
        hi = float(hi) if hi not in (None, "") else None
        return None if lo is None and hi is None else (lo, hi)

    legend = str(opts.get("legend") or "best")
    if legend not in _LEGEND_LOCS:
        legend = "best"
    _plot_jv(jid, curves, fname,
             xlim=_pair("xmin", "xmax"), ylim=_pair("ymin", "ymax"), legend=legend)
    return {"ok": True, "fname": fname}


def _plot_metrics_vs_t(jid, rows):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    t = [r["t_nm"] for r in rows]
    fig, axes = plt.subplots(2, 2, figsize=(8, 6))
    for ax, key, lab in zip(axes.ravel(),
                            ["Jsc_mA_cm2", "Voc_V", "FF", "PCE_pct"],
                            ["Jsc [mA/cm²]", "Voc [V]", "FF", "PCE [%]"]):
        ax.plot(t, [r[key] for r in rows], "o-")
        ax.set_xlabel("absorber thickness [nm]")
        ax.set_ylabel(lab)
        ax.grid(alpha=0.3)
    fig.suptitle("Perovskite thickness sweep (v0: Beer-Lambert, no interference)")
    fig.tight_layout()
    out = jobs.job_dir(jid) / "metrics_vs_thickness.png"
    fig.savefig(out, dpi=140)
    return out


def _save_csv(jid, rows, fname="summary.csv"):
    keys = list(rows[0].keys())
    p = jobs.job_dir(jid) / fname
    with open(p, "w", encoding="utf-8") as f:
        f.write(",".join(keys) + "\n")
        for r in rows:
            f.write(",".join(str(r[k]) for k in keys) + "\n")
    return p


# ---------------- 케이스 1: Si 데모 ----------------

def _run_si_demo(jid, params, log, get_client):
    client = get_client(log)
    dst = jobs.job_dir(jid) / "si_solar_cell_1d.mph"
    shutil.copy(SI_MPH, dst)
    model = client.load(str(dst))
    if params.get("mode") == "export":
        model.save(str(jobs.job_dir(jid) / "si_unsolved.mph"))
        _write_server_script(jid, ["si_unsolved.mph"], log)
        log("반출용 파일 생성 완료 — 작업 상세에서 다운로드")
        return
    for s in (model / "studies").children():
        log(f"솔브: {s.name()}")
        model.solve(s.name())
    V, I, expr = _extract_iv(model, log)
    pin = _pin_mw_cm2(log)
    m, Jgen = _metrics(V, I, 1.0, pin, log)
    _plot_jv(jid, [("Si demo", V, Jgen)])
    _save_csv(jid, [dict(case="si_demo", I_expr=expr, **m)])
    model.save(str(jobs.job_dir(jid) / "si_solved.mph"))
    log("문서 기대값 대비 확인: Voc≈0.61 V, Isc≈33 mA (면적 1cm²) — 위 지표와 비교해 주세요")


# ---------------- 케이스 2: 페로브스카이트 두께 스윕 (v0 초안) ----------------

def _try_set(node, pairs, log, label):
    """여러 후보 속성명을 순차 시도 — 성공/실패를 로그로 남겨 API를 자기발견."""
    for prop, val in pairs:
        try:
            node.set(prop, val)
            log(f"    set {label}.{prop} = {val}  [OK]")
            return True
        except Exception as e:
            log(f"    set {label}.{prop}: {type(e).__name__} (다음 후보 시도)")
    return False


def _build_perovskite(client, name, prm, log):
    """1D p-i-n 빌드 — 2026-07-06 진단 덤프에서 확정한 COMSOL 6.4 속성명 사용.

    근거(Si 예제 덤프): smm1의 *_mat='userdef' 패턴, ADM의 impurityType/NDc/NAc,
    tar1의 taun/taup(+_mat), UDGeneration의 Gn/Gp, MetalContact의 V0,
    스터디 스텝 타입 'SemiconductorEquilibrium', stat의 useparam/pname/plistarr/punit/
    pcontinuation 및 initmethod=sol·initstudy·initstudystep 연결, Interval의 specify/coord.
    """
    model = client.create(name)
    j = model.java

    # 컴포넌트/지오메트리 (3구간: n+ | 흡수층 | p+)
    try:
        j.component().create("comp1", True)
    except Exception:
        j.modelNode().create("comp1")
    comp = j.component("comp1")
    geom = comp.geom().create("geom1", 1)
    geom.lengthUnit("nm")
    for k, v in [("t_n", prm["t_nplus"]), ("t_abs", prm["t_abs"]),
                 ("t_p", prm["t_pplus"]), ("V0", "0[V]")]:
        j.param().set(k, v)
    i1 = geom.create("i1", "Interval")
    i1.set("specify", "coord")
    i1.set("coord", ["0", "t_n", "t_n+t_abs", "t_n+t_abs+t_p"])
    geom.run()
    log("  지오메트리 OK: 도메인 1=n+, 2=흡수층, 3=p+ / 경계 1=전면(입사·접지), 4=후면(V0)")

    # 보간 함수 — 데이터 파일을 파이썬에서 읽어 테이블로 직접 주입 (경로 이슈 회피)
    import numpy as np
    am15 = np.loadtxt(DATA / "am15_approx.txt", encoding="utf-8")
    f1 = j.func().create("int1", "Interpolation")
    f1.set("source", "table")
    f1.set("table", [[str(r[0]), str(r[1])] for r in am15])
    f1.set("funcname", "F")
    _try_set(f1, [("argunit", ["nm"]), ("argunit", "nm")], log, "int1")
    _try_set(f1, [("fununit", ["W/m^2/nm"]), ("fununit", "W/m^2/nm")], log, "int1")
    nk = np.loadtxt(DATA / "mapbi3_n_k.txt", encoding="utf-8")
    f2 = j.func().create("int2", "Interpolation")
    f2.set("source", "table")
    f2.set("table", [[str(r[0]), str(r[2])] for r in nk])  # (파장 um, k)
    f2.set("funcname", "kref")
    _try_set(f2, [("argunit", ["um"]), ("argunit", "um")], log, "int2")
    _try_set(f2, [("fununit", ["1"]), ("fununit", "1")], log, "int2")
    log(f"  보간 함수 OK: F(AM1.5) {len(am15)}행, kref(MAPbI3) {len(nk)}행 "
        f"(k범위 {nk[:,2].min():.3g}~{nk[:,2].max():.3g})")

    # 광생성 변수 G_ph
    try:
        var = comp.variable().create("var1")
    except Exception:
        var = j.variable().create("var1")
        var.model("comp1")
    var.set("G_ph", PRM.GENERATION["expr"])
    log("  변수 G_ph 정의 (Beer–Lambert, 300–850 nm)")

    # 물리: Semiconductor + 재료 물성(userdef)
    semi = comp.physics().create("semi", "Semiconductor", "geom1")
    area_ok = _try_set(semi, [("A", PRM.STRUCTURE["area"])], log, "semi.A")
    if not area_ok:
        try:
            semi.prop("d").set("A", PRM.STRUCTURE["area"])
            area_ok = True
            log("    set semi.prop('d').A OK")
        except Exception as e:
            log(f"    단면적 설정 실패 → 기본 1 m² 가정, 지표 계산에서 보정 ({type(e).__name__})")

    smm = semi.feature("smm1")
    for prop, val in [("Eg0", PRM.MATERIAL["Eg"]), ("chi0", PRM.MATERIAL["chi"]),
                      ("Nc", PRM.MATERIAL["Nc"]), ("Nv", PRM.MATERIAL["Nv"]),
                      ("mun", PRM.MATERIAL["mun"]), ("mup", PRM.MATERIAL["mup"])]:
        smm.set(prop + "_mat", "userdef")
        smm.set(prop, val)
    smm.set("epsilonr_mat", "userdef")
    _try_set(smm, [("epsilonr", [PRM.MATERIAL["epsr"]]),
                   ("epsilonr", PRM.MATERIAL["epsr"])], log, "smm1.epsilonr")
    log("  재료 물성 OK (MAPbI3, CASE_SPEC 4절 출처값)")

    # 도핑: n+(도메인1) / p+(도메인3) — 덤프 확인: impurityType, NDc/NAc
    adm1 = semi.create("adm1", "AnalyticDopingModel", 1)
    adm1.selection().set(1)
    adm1.set("impurityType", "donor")
    adm1.set("NDc", PRM.STRUCTURE["Nd_nplus"])
    adm2 = semi.create("adm2", "AnalyticDopingModel", 1)
    adm2.selection().set(3)
    adm2.set("impurityType", "acceptor")
    adm2.set("NAc", PRM.STRUCTURE["Na_pplus"])

    # SRH 재결합 (전 도메인)
    tar = semi.create("tar1", "TrapAssistedRecombination", 1)
    tar.selection().all()
    tar.set("taun_mat", "userdef")
    tar.set("taun", prm["taun"])
    tar.set("taup_mat", "userdef")
    tar.set("taup", prm["taup"])

    # 광생성 (전 도메인) — 덤프 확인: 타입 UDGeneration, 속성 Gn/Gp
    udg = semi.create("udg1", "UDGeneration", 1)
    udg.selection().all()
    udg.set("Gn", "G_ph")
    udg.set("Gp", "G_ph")

    # 접촉: 전면(경계1) 접지, 후면(경계4) V0
    mc1 = semi.create("mc1", "MetalContact", 0)
    mc1.selection().set(1)
    mc2 = semi.create("mc2", "MetalContact", 0)
    mc2.selection().set(4)
    mc2.set("V0", "V0")
    log("  물리 피처 OK (도핑 / SRH / 광생성 / 접촉)")

    j.mesh().create("mesh1", "geom1")

    # 스터디: 평형 → 정상상태 + V0 스윕 (평형해를 초기값·비해석변수 값으로)
    std1 = j.study().create("std1")
    std1.create("semie", "SemiconductorEquilibrium")
    std2 = j.study().create("std2")
    stat = std2.create("stat", "Stationary")
    sweep = PRM.SWEEP
    plist = f"range({sweep['V_start']},{sweep['V_step']},{sweep['V_stop']})"
    stat.set("useparam", "on")
    stat.set("pname", ["V0"])
    stat.set("plistarr", [plist])
    stat.set("punit", ["V"])
    _try_set(stat, [("sweeptype", "sparse")], log, "stat")
    _try_set(stat, [("pcontinuation", "V0")], log, "stat")
    for p, v in [("initmethod", "sol"), ("initstudy", "std1"), ("initstudystep", "semie"),
                 ("notsolmethod", "sol"), ("notstudy", "std1"), ("notstudystep", "semie")]:
        _try_set(stat, [(p, v)], log, "stat.init")
    log("  스터디 OK (Equilibrium → Stationary V0 스윕)")
    return model, area_ok


def _run_perovskite(jid, params, log, get_client):
    client = get_client(log)
    ts = [int(float(x)) for x in str(params.get("thicknesses_nm", "")).replace(" ", "").split(",") if x]
    if not ts:
        ts = PRM.SWEEP["thickness_nm"]
    tau = f"{float(params.get('taun_ns', 38.7))}[ns]"
    mode = params.get("mode", "local")
    log(f"두께 목록: {ts} nm / τ={tau} / mode={mode}")

    prm = dict(PRM.STRUCTURE)
    prm.update({"taun": tau, "taup": tau})
    rows, curves, exported = [], [], []
    pin = _pin_mw_cm2(log)

    for t in ts:
        jobs.check_cancel(jid)  # 중간 멈춤: 두께 사이에서 감지 (완료분 파일은 보존)
        log(f"\n===== t = {t} nm =====")
        prm["t_abs"] = f"{t}[nm]"
        model, area_ok = _build_perovskite(client, f"pvk_{t}nm", prm, log)
        area_cm2 = 1.0 if area_ok else 1.0e4  # 단면적 미설정 시 기본 1 m² 보정
        fname = f"pvk_{t}nm_unsolved.mph"
        model.save(str(jobs.job_dir(jid) / fname))
        if mode == "export":
            exported.append(fname)
            client.remove(model)
            continue
        for s in (model / "studies").children():
            log(f"  솔브: {s.name()}")
            model.solve(s.name())
        V, I, _ = _extract_iv(model, log)
        m, Jgen = _metrics(V, I, area_cm2, pin, log)
        rows.append(dict(t_nm=t, **m))
        curves.append((f"{t} nm", V, Jgen))
        model.save(str(jobs.job_dir(jid) / f"pvk_{t}nm_solved.mph"))
        client.remove(model)

    if mode == "export":
        _write_server_script(jid, exported, log)
        log("반출용 .mph 생성 완료 — 다운로드 후 오프라인 서버에서 run_server.bat 실행")
        return
    _save_csv(jid, rows)
    _save_curves(jid, curves)
    _plot_jv(jid, curves, "jv_all_thicknesses.png")
    _plot_metrics_vs_t(jid, rows)
    log("\n[검증 기준 대조 — CASE_SPEC 6절] Jsc 20~26, Voc 1.0~1.1, FF 0.7~0.85, PCE 17~22%, 최적 t≈550-650nm")


def _write_server_script(jid, mph_files, log):
    """오프라인 서버용 배치 (ASCII only). COMSOL 경로는 파일 상단 SET 줄에서 편집."""
    lines = [
        "@echo off",
        "rem Offline batch solve (needs COMSOL 6.4+ and license dongle; no Python).",
        "rem If comsolbatch is NOT on PATH, edit the next line to your COMSOL bin folder:",
        "rem   example: set COMSOLBIN=C:\\Program Files\\COMSOL\\COMSOL64\\Multiphysics\\bin\\win64",
        "set COMSOLBIN=",
        "if defined COMSOLBIN set PATH=%COMSOLBIN%;%PATH%",
        "where comsolbatch >nul 2>nul",
        "if errorlevel 1 goto nocomsol",
    ]
    for f in mph_files:
        out = f.replace("_unsolved", "_solved")
        lines.append(f'echo Solving {f} ...')
        lines.append(f'comsolbatch -inputfile "{f}" -outputfile "{out}"')
    lines += [
        "echo.",
        "echo Done. Copy the *_solved.mph files back and upload them in tab 4 of the app.",
        "pause",
        "exit /b 0",
        ":nocomsol",
        "echo [ERROR] comsolbatch not found.",
        "echo Edit this file: set COMSOLBIN=...your COMSOL bin\\win64 folder... then run again.",
        "pause",
        "exit /b 1",
    ]
    p = jobs.job_dir(jid) / "run_server.bat"
    p.write_text("\r\n".join(lines) + "\r\n", encoding="ascii")
    log(f"서버 실행 스크립트: {p.name} (COMSOL 경로는 파일 상단 SET 줄에서 지정 가능)")


# ---------------- 솔브된 파일 업로드 → 결과 추출 ----------------

def run_extract_solved(jid, params, log, get_client):
    client = get_client(log)
    path = jobs.job_dir(jid) / params["filename"]
    log(f"솔브된 파일 로드: {path.name}")
    model = client.load(str(path))
    V, I, expr = _extract_iv(model, log)
    pin = _pin_mw_cm2(log)
    m, Jgen = _metrics(V, I, 1.0, pin, log)
    _save_curves(jid, [(path.stem, V, Jgen)])
    _plot_jv(jid, [(path.stem, V, Jgen)])
    _save_csv(jid, [dict(file=path.name, I_expr=expr, **m)])
    log("업로드 파일에서 결과 추출 완료 (재솔브 없음)")

# ---------------- v0.3: 데이터 케이스 (JSON 정의 + 제네릭 스택 빌더) ----------------

def _run_stack(jid, params, log, get_client):
    """case.json의 grid 선언대로 2-파라미터 그리드 스윕 실행."""
    from . import library, stack_builder
    case = library.load_case(params.get("case_id"))
    client = get_client(log)

    def _default(key):
        return next(f["default"] for f in case["schema"] if f["key"] == key)

    def _list(key):
        raw = str(params.get(key) or _default(key))
        return [float(v) for v in raw.replace(" ", "").split(",") if v]

    xg, yg = case["grid"]["x"], case["grid"]["y"]
    xs, ys = _list(xg["field"]), _list(yg["field"])
    mode = params.get("mode", "local")
    dim = int(str(params.get("dim", "1")).lower().replace("d", "") or "1")
    if dim not in (1, 2, 3):
        raise ValueError(f"지원하지 않는 차원: {dim}")
    log(f"그리드: {xg['label']} {xs} × {yg['label']} {ys} → {len(xs)*len(ys)}회 솔브 "
        f"/ mode={mode} / 차원={dim}D")
    if dim > 1:
        log(f"{dim}D 평판 모드 (2026-07-07 1D 대조 검증 통과 — 편차 ~1%대, 메시 밀도 차이). "
            "주의: 평판 압출이므로 IBC 같은 가로 방향 구조는 아직 아님 (별도 빌더 개발 중)")

    base_vals = {}
    for f in case["schema"]:
        if f["key"] not in (xg["field"], yg["field"], "mode"):
            base_vals[f["key"]] = str(params.get(f["key"]) or f["default"])

    pin = _pin_mw_cm2(log)
    jd = jobs.job_dir(jid)
    combos = [(x, y) for y in ys for x in xs]

    # [1단계] 전 조합 unsolved 생성 — 생성 즉시 ④에서 내려받아 다른 컴퓨터에서 병렬 솔브 가능
    log(f"\n[1단계] unsolved 모델 {len(combos)}개 생성 (생성 즉시 ④ 산출물에서 다운로드 가능)")
    files = {}
    for i, (x, y) in enumerate(combos, 1):
        jobs.check_cancel(jid)  # 중단 요청 시 여기서 멈춤 (이미 만든 unsolved는 보존)
        vals = dict(base_vals)
        vals[xg["param"]] = f"{x:g}"
        vals[yg["param"]] = f"{y:g}"
        layers = library.resolve_layers(case, vals)
        if dim == 1:
            model, area_ok = stack_builder.build(
                client, f"stk_{x:g}_{y:g}", layers,
                case["generation"], case["voltage"], "1[cm^2]", log)
            area_cm2 = 1.0 if area_ok else 1.0e4
        else:
            from . import stack_builder_nd
            model, area_cm2 = stack_builder_nd.build(
                client, f"stk{dim}d_{x:g}_{y:g}", layers,
                case["generation"], case["voltage"], log, dim=dim)
        dtag = "" if dim == 1 else f"_{dim}d"
        fname = f"stack{dtag}_{xg['param']}{x:g}_{yg['param']}{y:g}_unsolved.mph"
        model.save(str(jd / fname))
        client.remove(model)
        files[(x, y)] = (fname, area_cm2)
        log(f"  [{i}/{len(combos)}] unsolved 저장: {fname}")

    _write_server_script(jid, [files[c][0] for c in combos], log)  # local이어도 생성 (병렬 솔브용)
    if mode == "export":
        log("반출용 .mph 생성 완료 — 다운로드 후 오프라인 서버에서 run_server.bat 실행")
        return
    log(f"[1단계 완료] unsolved {len(combos)}개 전부 준비됨 — 지금 unsolved+run_server.bat을 "
        "내려받으면 다른 컴퓨터에서 병렬 솔브 가능 (Python 불필요, COMSOL 6.4+와 동글만)")

    # [2단계] 이 PC에서 순차 솔브: unsolved를 열어 솔브 → solved 저장
    log(f"\n[2단계] 이 PC에서 순차 솔브 시작 ({len(combos)}건)")
    rows, curves = [], []
    cancelled = False
    for i, (x, y) in enumerate(combos, 1):
        if jobs.cancel_requested(jid):
            log(f"\n[중단 요청 감지] 남은 {len(combos) - i + 1}건 건너뜀 — 완료된 {len(rows)}건으로 결과 생성")
            cancelled = True
            break
        fname, area_cm2 = files[(x, y)]
        log(f"\n===== [{i}/{len(combos)}] {xg['param']}={x:g}nm, {yg['param']}={y:g}nm =====")
        model = client.load(str(jd / fname))
        for s in (model / "studies").children():
            log(f"  솔브: {s.name()}")
            model.solve(s.name())
        V, I, _ = _extract_iv(model, log)
        m, Jgen = _metrics(V, I, area_cm2, pin, log)
        rows.append({xg["param"]: x, yg["param"]: y, **m})
        curves.append((f"{xg['param']}={x:g} {yg['param']}={y:g}", V, Jgen))
        model.save(str(jd / fname.replace("_unsolved", "_solved")))
        client.remove(model)
    if rows:
        _save_csv(jid, rows)
        _save_curves(jid, curves)
        try:
            _plot_jv(jid, curves, "jv_grid.png")
            _plot_heatmap(jid, xs, ys, rows, xg, yg)
        except Exception as e:
            log(f"⚠️ 그림 생성 실패 (솔브 결과는 CSV로 보존됨): {type(e).__name__}: {e}")
    finite = [r for r in rows if r["PCE_pct"] == r["PCE_pct"]]  # NaN 제외
    if finite:
        best = max(finite, key=lambda r: r["PCE_pct"])
        part = " (부분 그리드 — 중단됨)" if cancelled else ""
        log(f"\n최적 조합{part}: {xg['param']}={best[xg['param']]:g}nm, {yg['param']}={best[yg['param']]:g}nm "
            f"→ PCE {best['PCE_pct']}%")
        log("[검증 기준 — v1 CASE_SPEC 4절] C60/BCP 두꺼울수록 FF·PCE 감소 경향, Jsc 둔감, 수렴 실패 0건")
    elif rows:
        log("\n⚠️ 유효한 PCE 없음 (전 조합 Voc 미도달) — 최적 조합 판정 불가, 로그 회신 요청")
    if cancelled:
        raise jobs.Cancelled()


def _plot_heatmap(jid, xs, ys, rows, xg, yg,
                  keys=(("PCE_pct", "PCE [%]"), ("FF", "FF")),
                  suptitle="ETL stack grid sweep (v1: idealized p-side, Beer-Lambert)",
                  fname="metrics_heatmap.png"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    for ax, (key, lab) in zip(axes, list(keys) + [keys[-1]]):
        Z = np.array([[next((r[key] for r in rows
                             if r[xg["param"]] == x and r[yg["param"]] == y), float("nan"))
                       for x in xs] for y in ys], dtype=float)
        im = ax.pcolormesh(xs, ys, Z, shading="nearest")
        fig.colorbar(im, ax=ax)
        ax.set_xlabel(xg["label"])
        ax.set_ylabel(yg["label"])
        ax.set_title(lab)
        if np.any(np.isfinite(Z)):
            iy, ix = np.unravel_index(np.nanargmax(Z), Z.shape)
            ax.plot(xs[ix], ys[iy], "r*", markersize=14)
    fig.suptitle(suptitle)
    fig.tight_layout()
    fig.savefig(jobs.job_dir(jid) / fname, dpi=140)


# ---------------- v2: IBC 2D 케이스 러너 ----------------

def _plot_charge2d(jid, model, fname, title, log):
    """2D 전하분포(전자/정공, 평형 상태) — 스파이크로 확정된 노드 평가 경로 사용."""
    try:
        X = np.ravel(model.evaluate("x")) / 1000.0   # nm → um (스파이크에서 nm 반환 확인)
        Y = np.ravel(model.evaluate("y")) / 1000.0
        N = np.ravel(model.evaluate("semi.N"))
        P = np.ravel(model.evaluate("semi.P"))
        m = min(X.size, Y.size, N.size, P.size)
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(2, 1, figsize=(8, 5.4), sharex=True)
        for ax, F, lab in ((axes[0], N, "electron"), (axes[1], P, "hole")):
            tc = ax.tripcolor(X[:m], Y[:m], np.log10(np.clip(F[:m], 1e6, None)),
                              shading="gouraud")
            fig.colorbar(tc, ax=ax, label=f"log10({lab} [1/m^3])")
            ax.set_ylabel("y [um]")
            ax.set_title(f"{lab} — {title} (equilibrium, dark)", fontsize=9)
        axes[1].set_xlabel("x [um]  (0=n전극 중앙, 오른끝=p전극 중앙)")
        fig.tight_layout()
        fig.savefig(jobs.job_dir(jid) / fname, dpi=140)
        log(f"  전하분포 저장: {fname}")
    except Exception as e:
        log(f"  전하분포 플롯 실패 ({type(e).__name__}: {str(e)[:120]}) — 계속 진행")


def _run_ibc(jid, params, log, get_client):
    """IBC 2D 그리드: 전극 폭 W × 간격 gap. 사양: cases/perovskite_ibc_2d/CASE_SPEC.md"""
    from . import ibc_builder, library
    client = get_client(log)

    def _list(key, default):
        raw = str(params.get(key) or default)
        return [float(v) for v in raw.replace(" ", "").split(",") if v]

    ws = _list("w_list_um", "1,2,3,4,5")
    gs = _list("gap_list_um", "1,3,5")
    t_abs = float(params.get("t_abs_nm") or 800)
    taun_ns = float(params.get("taun_ns") or 38.7)
    s_ifc_cms = float(params.get("s_ifc_cms") or 0)
    if (not ws or not gs or not all(np.isfinite(v) for v in (*ws, *gs))
            or any(v <= 0 for v in (*ws, *gs))):
        raise ValueError("W와 gap 목록은 0보다 큰 숫자를 하나 이상 포함해야 합니다")
    if (not all(np.isfinite(v) for v in (t_abs, taun_ns, s_ifc_cms))
            or t_abs <= 0 or taun_ns <= 0 or s_ifc_cms < 0):
        raise ValueError("흡수층 두께·SRH 수명은 0보다 커야 하고 계면 S는 0 이상이어야 합니다")
    taun = f"{taun_ns:g}[ns]"
    mode = params.get("mode", "local")
    gen_mode = str(params.get("gen_mode", "wave_optics"))
    if gen_mode not in ("wave_optics", "beer_lambert"):
        raise ValueError(f"지원하지 않는 광생성 모델: {gen_mode}")
    log(f"IBC 그리드: W {ws}um × gap {gs}um → {len(ws)*len(gs)}회 솔브 / t_abs={t_abs:g}nm "
        f"/ mode={mode} / 광생성={gen_mode}")
    g_profile = None
    jsc_ref = None
    if gen_mode == "wave_optics":
        from . import wo_optics
        client_early = get_client(log)
        g_profile = wo_optics.compute_G_profile(client_early, t_abs, log,
                                                nlam=int(float(params.get("nlam") or 15)))
        jsc_ref = g_profile.get("jsc_injected", g_profile["jsc_wave"])
        jd0 = jobs.job_dir(jid)
        np.savetxt(jd0 / "g_profile.csv",
                   np.column_stack([g_profile["depth_nm"], g_profile["G"]]),
                   header="depth_nm G_per_m3s", comments="")
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(figsize=(6.5, 3.6))
            ax.plot(g_profile["depth_nm"], g_profile["G"])
            ax.set_xlabel("depth from illuminated face [nm]")
            ax.set_ylabel("G [1/(m^3 s)]")
            ax.set_title(f"Wave-optics generation (Jsc,opt {g_profile['jsc_wave']:.2f} mA/cm2)",
                         fontsize=10)
            ax.grid(alpha=0.3)
            fig.tight_layout()
            fig.savefig(jd0 / "g_profile.png", dpi=140)
        except Exception as e:
            log(f"  G 프로파일 그림 실패({type(e).__name__}) — CSV는 저장됨")
        log(f"⚠️ 참고: 파동광학 모드 — Beer-Lambert 대비 반사 손실 반영 "
            f"(Jsc 상한 {g_profile['jsc_wave']:.2f}, BL이었다면 {g_profile['jsc_bl']:.2f} mA/cm²)")
    else:
        from . import wo_optics
        jsc_ref = wo_optics.beer_lambert_jsc(t_abs)
        log(f"Beer-Lambert 주입 생성률 적분 상한: {jsc_ref:.3f} mA/cm²")
    mats = {
        "absorber": library.material_props("mapbi3")["props"],
        "sno2": library.material_props("sno2")["props"],
        "niox": library.material_props("niox")["props"],
        "sno2_nd": "1e19[1/cm^3]",   # meskini2024 Table 1
        "niox_na": "1e18[1/cm^3]",   # sahu2018 Table 1
    }
    # IBC v0: SnO2 주입장벽(0.49eV)+NiOx 전자차단+계면 재결합 없음(이상화)이 겹쳐
    # Voc가 Eg(1.55V)를 넘는 인공물이 생김 (2026-07-07 C60 대조 스파이크로 판별).
    # 교차점 포착 위해 2.0V까지 스윕. Voc·PCE 절대값은 비교용으로만 — v1에서 계면 재결합 추가 예정.
    vcfg = {"start": 0.0, "stop": 2.0, "step": 0.02}
    log("⚠️ v0 한계: Voc는 이상화(계면 재결합 없음) 인공물로 Eg를 넘을 수 있음 — "
        "기하(W·gap) 경향 비교용. Jsc·수집 효율이 이 케이스의 신뢰 지표")
    pin = _pin_mw_cm2(log)
    jd = jobs.job_dir(jid)
    combos = [(w, g) for g in gs for w in ws]

    log(f"\n[1단계] unsolved 모델 {len(combos)}개 생성")
    files = {}
    for i, (w, g) in enumerate(combos, 1):
        jobs.check_cancel(jid)
        model, area_cm2 = ibc_builder.build(
            client, f"ibc_{w:g}_{g:g}", mats, w * 1000.0, g * 1000.0, t_abs, taun, vcfg, log,
            g_profile=g_profile, s_ifc_cms=s_ifc_cms,
            mesh_hmax_nm=float(params.get("mesh_hmax_nm") or 0) or None)
        fname = f"ibc_W{w:g}_g{g:g}_unsolved.mph"
        model.save(str(jd / fname))
        client.remove(model)
        files[(w, g)] = (fname, area_cm2)
        log(f"  [{i}/{len(combos)}] unsolved 저장: {fname}")
    _write_server_script(jid, [files[c][0] for c in combos], log)
    if mode == "export":
        log("반출용 .mph 생성 완료 — 오프라인 서버에서 run_server.bat 실행")
        return
    log(f"[1단계 완료] 지금 unsolved+run_server.bat을 내려받으면 다른 컴퓨터 병렬 솔브 가능")

    log(f"\n[2단계] 순차 솔브 ({len(combos)}건)")
    rows, curves = [], []
    cancelled = False
    for i, (w, g) in enumerate(combos, 1):
        if jobs.cancel_requested(jid):
            log(f"\n[중단 요청] 남은 {len(combos)-i+1}건 건너뜀 — 완료 {len(rows)}건으로 결과 생성")
            cancelled = True
            break
        fname, area_cm2 = files[(w, g)]
        log(f"\n===== [{i}/{len(combos)}] W={w:g}um, gap={g:g}um =====")
        model = None
        try:
            model = client.load(str(jd / fname))
            for s in (model / "studies").children():
                log(f"  솔브: {s.name()}")
                model.solve(s.name())
            V, I, _ = _extract_iv(model, log)
            m, Jgen = _metrics(V, I, area_cm2, pin, log)
            eta = m["Jsc_mA_cm2"] / jsc_ref
            rows.append({"W_um": w, "gap_um": g, "eta_col": round(eta, 4), **m})
            log(f"  수집효율(1D 800nm 대비): {eta:.1%}")
            curves.append((f"W={w:g} g={g:g}", V, Jgen))
            _plot_charge2d(jid, model, f"charge2d_W{w:g}_g{g:g}.png", f"W={w:g}um gap={g:g}um", log)
            model.save(str(jd / fname.replace("_unsolved", "_solved")))
        except Exception as e:  # 한 조합 실패가 배치를 죽이지 않게 (2026-07-07 W3/g5 교훈)
            log(f"  ✖ 조합 실패 ({type(e).__name__}: {str(e)[:180]}) — 다음 조합 진행")
            rows.append({"W_um": w, "gap_um": g, "eta_col": float("nan"),
                         "Jsc_mA_cm2": float("nan"), "Voc_V": float("nan"), "FF": float("nan"),
                         "PCE_pct": float("nan"), "Pmax_mW_cm2": float("nan"),
                         "Pin_mW_cm2": pin, "Vmpp_V": float("nan")})
        finally:
            try:
                if model is not None:
                    client.remove(model)
            except Exception:
                pass

    if rows:
        _save_csv(jid, rows)
        _save_curves(jid, curves)
        try:
            _plot_jv(jid, curves, "jv_ibc_grid.png")
            _plot_heatmap(jid, ws, gs, rows,
                          {"param": "W_um", "field": "w_list_um", "label": "전극 폭 W [um]"},
                          {"param": "gap_um", "field": "gap_list_um", "label": "간격 gap [um]"},
                          keys=(("Jsc_mA_cm2", "Jsc [mA/cm2]"), ("eta_col", "수집효율 vs 1D(800nm)")),
                          suptitle="IBC 2D grid (v0: Jsc/collection — Voc는 이상화 인공물)")
        except Exception as e:
            log(f"⚠️ 그림 생성 실패 (CSV는 보존): {type(e).__name__}: {e}")
    finite = [r for r in rows if r["PCE_pct"] == r["PCE_pct"]]
    ok_jsc = [r for r in rows if r["Jsc_mA_cm2"] == r["Jsc_mA_cm2"]]
    part = " (부분 그리드 — 중단됨)" if cancelled else ""
    if finite:
        best = max(finite, key=lambda r: r["PCE_pct"])
        log(f"\n최적 조합{part}: W={best['W_um']:g}um, gap={best['gap_um']:g}um → PCE {best['PCE_pct']}%")
    elif ok_jsc:
        best = max(ok_jsc, key=lambda r: r["Jsc_mA_cm2"])
        log(f"\n최적 조합(Jsc 기준{part}): W={best['W_um']:g}um, gap={best['gap_um']:g}um "
            f"→ Jsc {best['Jsc_mA_cm2']} mA/cm² (수집효율 {best['eta_col']:.1%})")
        log("Voc/FF/PCE는 v0 이상화(계면 재결합 없음) 인공물로 판정 불가 — CASE_SPEC 6.5절")
    else:
        log("\n⚠️ 유효한 결과 없음 — 로그 회신 요청")
    if ok_jsc:
        log("[검증 기준 — IBC CASE_SPEC 6절] W↑ 또는 gap↑ → Jsc 감소(수집 손실) / Jsc ≤ 26.26 / 실패 조합 로그 확인")
    if cancelled:
        raise jobs.Cancelled()


def _review_svg(w_um, gap_um, t_abs_nm, lz_um, tip_um, has_idl, t_sno2, t_niox):
    """모델 개략도 SVG (검토 모드) — 실제 입력 치수 기반, 세로 비율은 과장(표기).

    좌: 단면(x–y, 층 구조·접점·IDL) / 우: 평면(x–z, 핑거 끝단). 임의값 없음 —
    모든 라벨 수치는 빌더에 실제로 들어간 값.
    """
    L = w_um + gap_um            # 단위셀 폭 (양끝 반폭 핑거)
    sx = 260.0 / L               # x 스케일 [px/um]
    hw = w_um / 2 * sx
    x0, y0 = 40, 30              # 단면 패널 원점
    # 세로(두께)는 비율 과장: CTL 26px, IDL 6px, 흡수층 120px
    hC, hI, hA = 26, (6 if has_idl else 0), 120
    yA, yI, yC = y0 + 20, y0 + 20 + hA, y0 + 20 + hA + hI
    def r(x, y, w, h, fill, op="1"):
        return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
                f'fill="{fill}" fill-opacity="{op}" stroke="#333" stroke-width="0.6"/>')
    def t(x, y, s, size=10, anchor="start", fill="#111"):
        return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" '
                f'text-anchor="{anchor}" fill="{fill}" font-family="sans-serif">{s}</text>')
    p = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 680 260" '
         f'font-family="sans-serif">',
         t(x0, y0 - 10, f"단면 (x–y)  W={w_um:g}um, gap={gap_um:g}um — 세로 비율 과장", 11),
         r(x0, yA, L * sx, hA, "#f8d5a3"),                      # 흡수층
         t(x0 + L * sx / 2, yA + hA / 2, f"MAPbI3 {t_abs_nm:g}nm (G(z) 주입)", 11, "middle")]
    if has_idl:  # IDL 밴드 (n/p 각 핑거 위)
        p += [r(x0, yI, hw, hI, "#e05c5c"), r(x0 + L * sx - hw, yI, hw, hI, "#e05c5c"),
              t(x0 + L * sx / 2, yI + hI - 1, "IDL 2nm (계면 재결합 등가층)", 9, "middle", "#a00")]
    p += [r(x0, yC, hw, hC, "#9ecbf5"), t(x0 + hw / 2, yC + hC / 2 + 4,
                                          f"SnO2 {t_sno2:g}nm", 9, "middle"),
          r(x0 + L * sx - hw, yC, hw, hC, "#b7e3b1"),
          t(x0 + L * sx - hw / 2, yC + hC / 2 + 4, f"NiOx {t_niox:g}nm", 9, "middle"),
          # 접점 (CTL 바닥면)
          r(x0, yC + hC, hw, 5, "#555"), r(x0 + L * sx - hw, yC + hC, hw, 5, "#555"),
          t(x0 + hw / 2, yC + hC + 16, "n 접점", 9, "middle"),
          t(x0 + L * sx - hw / 2, yC + hC + 16, "p 접점 (V0 스윕)", 9, "middle"),
          t(x0 + L * sx / 2, yC + hC / 2 + 4, f"gap {gap_um:g}um", 9, "middle", "#666")]
    # 우측: 평면 (x–z) — 핑거 끝단
    X0, Y0 = 400, 50
    sz = 150.0 / lz_um
    ztip = tip_um * sz
    p += [t(X0, Y0 - 10, f"평면 (x–z)  핑거 {tip_um:g}um / 셀 {lz_um:g}um", 11),
          r(X0, Y0, L * sx * 0.9, lz_um * sz, "#f8d5a3", "0.5"),
          r(X0, Y0, hw * 0.9, ztip, "#9ecbf5", "0.9"),
          r(X0 + (L * sx - hw) * 0.9, Y0, hw * 0.9, ztip, "#b7e3b1", "0.9")]
    if tip_um < lz_um:
        p += [f'<line x1="{X0}" y1="{Y0 + ztip:.1f}" x2="{X0 + L * sx * 0.9:.1f}" '
              f'y2="{Y0 + ztip:.1f}" stroke="#c00" stroke-dasharray="4 3"/>',
              t(X0 + L * sx * 0.9 + 4, Y0 + ztip + 3, "핑거 끝단(팁)", 9, "start", "#c00")]
    p.append("</svg>")
    return "\n".join(p)


def _write_model_review(jid, params, files, combos, mats, g_profile, jsc_ref,
                        t_abs, lz_um, tip_um, taun, jv_mode, log):
    """검토 모드 산출물: model_review.md(모폴로지·경계조건·물성·메시·스터디) + 개략도 SVG.

    모든 수치는 빌더에 실제로 넣은 값에서 가져온다(임의값 금지). 한계(정직 고지):
    실제 생성된 메시의 요소 수·품질 통계는 COMSOL API 검증이 필요해 아직 자동화하지
    않았다 — unsolved .mph를 GUI로 열어 Mesh 노드에서 확인 가능(서버 검증 후 추가 예정).
    """
    from . import ibc3d_builder as _b
    jd = jobs.job_dir(jid)
    s_ifc = float(params.get("s_ifc_cms") or 0)
    has_idl = s_ifc > 0
    requested_hmax = float(params.get("mesh_hmax_nm") or 0)
    hmax = (f"{requested_hmax:g}nm (요청값)" if requested_hmax else
            "120nm (IDL 안전망 자동 적용)" if has_idl else "자동 메시")
    n_pairs = int(float(params.get("n_pairs") or 0))
    vmx = float(params.get("v_max") or 1.3)
    vst = float(params.get("v_step") or 0.05)
    import json as _json
    md = ["# 모델 검토서 (솔브 전 확인용)", "",
          f"- 케이스: `{params.get('case_id', '')}` / 빌드 성공 {len(files)}/{len(combos)}조합",
          f"- 구조: IBC 3D {'주기 단위셀(양끝 반폭 핑거)' if n_pairs == 0 else f'깍지 배열 {n_pairs}쌍'}"
          f" — 흡수층 MAPbI3 {t_abs:g}nm / 핑거 {tip_um:g}um (셀 z {lz_um:g}um"
          + (", 끝단 효과 포함)" if tip_um < lz_um else " = 압출 극한)"), "",
          "## 1. 모폴로지 (지오메트리)", "",
          "| 조합 | W [um] | gap [um] | unsolved 파일 | 접점 면적 [cm²] |",
          "|---|---|---|---|---|"]
    for (w, g) in combos:
        if (w, g) in files:
            fn, a = files[(w, g)]
            md.append(f"| W{w:g}/g{g:g} | {w:g} | {g:g} | {fn} | {a:.3e} |")
    md += ["",
           f"- 층 구성(아래→위): 접점(금속) / n측 SnO₂ {_b.T_SNO2:g}nm · p측 NiOx "
           f"{_b.T_NIOX:g}nm 핑거"
           + (f" / IDL {_b.T_IDL:g}nm(계면 재결합 등가층)" if has_idl else "")
           + f" / MAPbI₃ 흡수층 {t_abs:g}nm", "",
           "## 2. 경계조건·물리", "",
           f"- 접점: 금속 접점 2개 — n측(SnO₂ 바닥면 핑거 구간)·p측(NiOx 바닥면 핑거 구간), "
           f"{'풀 J-V: V0를 0→' + f'{vmx:g}V 스윕' if jv_mode == 'full_jv' else 'Jsc 전용(V=0)'}",
           f"- SRH 재결합: τ = {taun}를 현재 전 반도체 도메인에 적용. 이 τ의 출처는 흡수층이며 "
           "SnO₂/NiOx 수명은 자료 누락 상태라 현재 구현은 단순화입니다.",
           (f"- 계면 재결합: IDL {_b.T_IDL:g}nm 등가층, S = {s_ifc:g} cm/s → "
            f"τ_IDL = d/S = {(_b.T_IDL * 1e-9) / (s_ifc * 1e-2):.3g}s (벌크 SRH와 가산)"
            if has_idl else "- 계면 재결합: 없음 (s_ifc_cms=0)"),
           ("- 광생성: 평판 파동광학 G(z) 주입"
            + (f", Jsc,opt = {g_profile['jsc_wave']:.2f} mA/cm²" if g_profile else "")
            if str(params.get('gen_mode', 'wave_optics')) == "wave_optics"
            else f"- 광생성: Beer-Lambert, 주입 생성률 적분 = {jsc_ref:.3f} mA/cm²"), "",
           "## 3. 물성 (materials.json 값 그대로)", "", "```json",
           _json.dumps({k: v for k, v in mats.items()}, ensure_ascii=False, indent=1),
           "```", "",
           "## 4. 메시", "",
           f"- 방식: Swept (3D 평판 검증에서 확정) / hmax = {hmax}",
           "- IDL 사용 시: IDL 밴드 z-전구간 분할(끝단×IDL Swept 파괴 해결, 2026-07-09)",
           "- ⚠️ 실제 생성된 요소 수·품질 통계는 아직 자동 추출하지 않음(COMSOL API 검증"
           " 필요) — unsolved .mph를 GUI로 열어 Mesh 노드에서 확인하세요.",
           "- 솔브 시 자동 메시 사다리: 요청값 → 150 → 100 → 135 → 90nm(+스윕 전체 미세"
           " 스텝) 순으로 발산 시 재시도합니다.", "",
           "## 5. 스터디", "",
           "- Study 1: 평형(초기해)",
           (f"- Study 2: J-V 명시 스윕 0→{vmx:g}V — 0→0.9V는 {vst:g}V 간격, "
            f"0.9→{vmx:g}V는 {vst / 2:g}V 간격(턴온 구간 미세화)"
            if jv_mode == "full_jv" else "- Study 2: 없음 (Jsc 전용)"), "",
           "## 6. 다음 단계", "",
           "- 모델 확정: ④ 작업 상세의 [✅ 검토 완료 — 해 구하기] 버튼 → 이 빌드 그대로 솔브",
           "- 수정 필요: [🛠 수정 프롬프트]에 요청 입력 → 복붙 모드로 수정 케이스 생성 후"
           " 다시 검토", ""]
    (jd / "model_review.md").write_text("\n".join(md), encoding="utf-8")
    try:
        w0, g0 = next(iter(k for k in combos if k in files))
        (jd / "model_schematic.svg").write_text(
            _review_svg(w0, g0, t_abs, lz_um, tip_um, has_idl, _b.T_SNO2, _b.T_NIOX),
            encoding="utf-8")
    except Exception as e:
        log(f"⚠️ 개략도 생성 실패: {type(e).__name__}: {e}")
    log("모델 검토서 저장: model_review.md + model_schematic.svg")


def _run_ibc3d(jid, params, log, get_client):
    """IBC 3D 단위 셀 (v4): W×gap 그리드, 핑거 길이·팁 파라미터, Jsc 전용.
    (아래 본체 — 검토서 함수는 바로 위 _write_model_review 참조)

    검증 기준: tip_len=finger_len(완전 압출)이면 **같은 메시 설정의** 2D IBC와 Jsc가
    ~2% 내 일치해야 함 (기본 메시끼리 W3/g3 wave: 3D 14.87 vs 2D 14.55, 2026-07-08).
    절대값은 메시 수렴 필요 — 2D CASE_SPEC 6.9절 (기본 메시는 Jsc −24% 과소).
    """
    from . import ibc3d_builder, library
    client = get_client(log)

    def _list(key, default):
        raw = str(params.get(key) or default)
        return [float(v) for v in raw.replace(" ", "").split(",") if v]

    ws = _list("w_list_um", "3")
    gs = _list("gap_list_um", "3")
    t_abs = float(params.get("t_abs_nm") or 800)
    lz_um = float(params.get("finger_len_um") or 4)
    tip_um = float(params.get("tip_len_um") or lz_um)  # 기본 = 압출 극한(검증 모드)
    taun_ns = float(params.get("taun_ns") or 38.7)
    mode = params.get("mode", "local")
    gen_mode = str(params.get("gen_mode", "wave_optics"))
    jv_mode = str(params.get("jv_mode", "jsc_only"))
    if gen_mode not in ("wave_optics", "beer_lambert"):
        raise ValueError(f"지원하지 않는 광생성 모델: {gen_mode}")
    if jv_mode not in ("full_jv", "jsc_only"):
        raise ValueError(f"지원하지 않는 J-V 범위: {jv_mode}")
    v_max_input = float(params.get("v_max") or 1.3)
    v_step_input = float(params.get("v_step") or 0.05)
    if jv_mode == "full_jv" and len(_v_vals(v_max_input, v_step_input)) < 3:
        raise ValueError("full_jv 전압 목록은 서로 다른 전압점을 3개 이상 만들어야 합니다")
    s_ifc_cms = float(params.get("s_ifc_cms") or 0)
    mesh_hmax_nm = (float(params.get("mesh_hmax_nm"))
                    if params.get("mesh_hmax_nm") else None)
    if (not ws or not gs or not all(np.isfinite(v) for v in (*ws, *gs))
            or any(v <= 0 for v in (*ws, *gs))):
        raise ValueError("W와 gap 목록은 0보다 큰 숫자를 하나 이상 포함해야 합니다")
    numeric_inputs = (t_abs, lz_um, tip_um, taun_ns, s_ifc_cms)
    if (not all(np.isfinite(v) for v in numeric_inputs)
            or (mesh_hmax_nm is not None and not np.isfinite(mesh_hmax_nm))
            or t_abs <= 0 or lz_um <= 0 or tip_um <= 0 or tip_um > lz_um
            or taun_ns <= 0 or s_ifc_cms < 0
            or (mesh_hmax_nm is not None and mesh_hmax_nm <= 0)):
        raise ValueError(
            "두께·핑거 길이·팁·SRH 수명·메시는 0보다 커야 하고, "
            "팁은 핑거 길이 이하여야 하며 계면 S는 0 이상이어야 합니다")
    taun = f"{taun_ns:g}[ns]"
    unsafe_80 = tip_um < lz_um and s_ifc_cms > 0 and mesh_hmax_nm == 80.0
    if unsafe_80 and mode not in ("export", "review"):
        raise ValueError(
            "끝단+IDL 3D에서 hmax=80nm는 평형 발산이 실측 확인됐습니다. "
            "검증된 기본 120nm 또는 메시 사다리 값을 사용하세요.")
    if unsafe_80:
        log("⚠️ hmax=80nm 끝단+IDL 모델은 평형 발산이 실측 확인됨 — "
            "검토/재현용 unsolved만 생성하며 솔브 입력으로는 권장하지 않음")
    if (str(params.get("hpc_only", "")).lower() in ("yes", "true", "1")
            and mode not in ("export", "review")):  # review=빌드+검토서만 (솔브 없음)
        raise RuntimeError(
            "이 케이스는 서버 컴퓨터 전용(HPC)입니다 — 이 PC에서 local 솔브를 막았습니다. "
            "mode=export로 unsolved+run_server.bat을 만들어 서버에서 돌리세요. "
            "절차: docs/SERVER_GUIDE.md")
    # 깍지 배열 봉인 (2026-07-13 정책 확정): n_pairs≥1은 평형 수렴 미검증(2026-07-09
    # W2·W3 발산)이라 local 솔브만 차단 — review/export(모델 생성·검토·반출)는 허용해
    # 연구 경로를 줄이지 않는다. 수렴 개선 검증 후 해제.
    if int(float(params.get("n_pairs") or 0)) >= 1 and mode not in ("export", "review"):
        raise RuntimeError(
            "깍지 배열(n_pairs≥1)은 평형 수렴이 미검증 상태라 local 솔브를 봉인했습니다. "
            "review/export로 모델 생성·검토는 가능합니다. 배열 솔브가 필요하면 "
            "Claude와 수렴 개선을 먼저 진행하세요.")
    log(f"IBC 3D 그리드: W {ws} × gap {gs} um → {len(ws)*len(gs)}셀 / 핑거 {lz_um:g}um "
        f"(팁 {tip_um:g}um{' = 압출 극한(2D 대조 검증 모드)' if tip_um >= lz_um else ''}) "
        f"/ {f'풀 J-V(0→{v_max_input:g}V, 기본 간격 {v_step_input:g}V)' if jv_mode == 'full_jv' else 'Jsc 전용'} "
        f"/ 광생성={gen_mode}")
    mats = {
        "absorber": library.material_props("mapbi3")["props"],
        "sno2": library.material_props("sno2")["props"],
        "niox": library.material_props("niox")["props"],
        "sno2_nd": "1e19[1/cm^3]",
        "niox_na": "1e18[1/cm^3]",
    }
    resume_from = str(params.get("resume_from") or "").strip()
    g_profile = None
    jsc_ref_fresh = None
    if gen_mode == "wave_optics" and not resume_from:
        from . import wo_optics
        g_profile = wo_optics.compute_G_profile(client, t_abs, log,
                                                nlam=int(float(params.get("nlam") or 15)))
        jsc_ref_fresh = g_profile.get("jsc_injected", g_profile["jsc_wave"])
    elif not resume_from:
        from . import wo_optics
        jsc_ref_fresh = wo_optics.beer_lambert_jsc(t_abs)
        log(f"Beer-Lambert 주입 생성률 적분 상한: {jsc_ref_fresh:.3f} mA/cm²")
    jd = jobs.job_dir(jid)
    combos = [(w, g) for g in gs for w in ws]
    files = {}
    jsc_ref_resume = None
    if resume_from:
        # (2026-07-13) 모델 검토 워크플로: review 작업의 unsolved 빌드를 복사해 와서
        # 1단계(광학·모델 생성)를 통째로 생략하고 솔브만 수행
        import json as _json
        import shutil
        src = jobs.job_dir(resume_from)
        mf = src / "build_manifest.json"
        if not mf.exists():
            raise RuntimeError(f"재개할 빌드 매니페스트가 없습니다 (원본 작업 {resume_from})")
        man = _json.loads(mf.read_text(encoding="utf-8"))
        for c in man.get("combos", []):
            key = (float(c["w"]), float(c["g"]))
            if key in combos and (src / c["fname"]).exists():
                shutil.copy2(src / c["fname"], jd / c["fname"])
                files[key] = (c["fname"], float(c["area_cm2"]))
        jsc_ref_resume = man.get("jsc_ref_mA_cm2")
        if jsc_ref_resume is None:  # 2026-07-13 이전 wave-optics 매니페스트 호환
            jsc_ref_resume = man.get("jsc_wave")
        log(f"[재개] 작업 {resume_from}의 검토 완료 빌드 재사용 — "
            f"{len(files)}/{len(combos)}조합 복사, 1단계(광학·생성) 생략")
        if not files:
            raise RuntimeError("재개할 unsolved 모델이 없습니다 — 원본 작업 그리드와 일치 확인")
    else:
        log(f"\n[1단계] unsolved 모델 {len(combos)}개 생성")
    build_list = [] if resume_from else combos
    for i, (w, g) in enumerate(build_list, 1):
        jobs.check_cancel(jid)
        # 조합별 보호 + 1회 재시도 (2026-07-09 서버: 45번째 생성에서 COMSOL 세션 NPE로
        # 배치 전체가 죽음 — 장시간 생성 반복 후 세션 일시 불안정 대비)
        for attempt in (1, 2):
            model = None
            try:
                model, area_cm2 = ibc3d_builder.build(
                    client, f"ibc3d_{w:g}_{g:g}", mats, w * 1000.0, g * 1000.0, t_abs, taun,
                    lz_um * 1000.0, tip_um * 1000.0, log, g_profile=g_profile,
                    v0_only=(jv_mode != "full_jv"),
                    # 스윕 상한 기본 1.3V + 턴온(≥0.9V) 구간 미세 스텝 (2026-07-10:
                    # 0-2V는 강순방향 발산 / 발산 지점이 전부 1.0~1.15V 부근 → 그 구간
                    # 스텝을 절반으로 좁혀 뉴턴이 이전 해에서 출발하기 쉽게)
                    vcfg={"plist": _v_plist(float(params.get("v_max") or 1.3),
                                            float(params.get("v_step") or 0.05))},
                    s_ifc_cms=s_ifc_cms,
                    mesh_hmax_nm=mesh_hmax_nm,
                    n_pairs=int(float(params.get("n_pairs") or 0)))
                fname = f"ibc3d_W{w:g}_g{g:g}_unsolved.mph"
                model.save(str(jd / fname))
                files[(w, g)] = (fname, area_cm2)
                log(f"  [{i}/{len(combos)}] unsolved 저장: {fname}")
                break
            except Exception as e:
                log(f"  ✖ 빌드 실패 W{w:g}/g{g:g} (시도 {attempt}/2 — "
                    f"{type(e).__name__}: {str(e)[:140]})")
                if attempt == 1:
                    log("    → 모델 정리 후 재시도")
            finally:
                try:
                    if model is not None:
                        client.remove(model)
                except Exception:
                    pass
        if (w, g) not in files:
            log(f"  [{i}/{len(combos)}] 건너뜀 (빌드 2회 실패): W{w:g}/g{g:g}")
    if not files:
        raise RuntimeError("모든 조합의 모델 생성이 실패했습니다 — 로그 회신 요청")
    _write_server_script(jid, [files[c][0] for c in combos if c in files], log)
    if not resume_from:
        # 빌드 매니페스트 — 검토(review) 후 [해 구하기]로 재개할 때 1단계 생략용
        import json as _json
        (jd / "build_manifest.json").write_text(_json.dumps({
            "combos": [{"w": w, "g": g, "fname": files[(w, g)][0],
                        "area_cm2": files[(w, g)][1]}
                       for (w, g) in combos if (w, g) in files],
            "jsc_wave": (g_profile or {}).get("jsc_wave"),
            "jsc_ref_mA_cm2": jsc_ref_fresh,
            "params": {k: str(v) for k, v in (params or {}).items()},
        }, ensure_ascii=False, indent=1), encoding="utf-8")
    if mode == "export":
        log("반출용 생성 완료")
        return
    if mode == "review":
        # (2026-07-13) 검토 모드: 솔브 없이 모델 검토서+개략도 생성 후 대기
        try:
            _write_model_review(jid, params, files, combos, mats, g_profile, jsc_ref_fresh,
                                t_abs, lz_um, tip_um, taun, jv_mode, log)
        except Exception as e:
            log(f"⚠️ 검토서 생성 실패: {type(e).__name__}: {e}")
            raise RuntimeError("모델 검토서 생성 실패 — 검토 완료로 처리하지 않음") from e
        log("\n[검토 대기] unsolved 모델 + 모델 검토서 생성 완료.\n"
            "④ 작업 상세에서 model_review.md(모폴로지·경계조건·물성·메시)와 개략도를 확인하세요.\n"
            "- 모델이 마음에 들면: [✅ 검토 완료 — 해 구하기] 버튼 → 이 빌드 그대로 솔브 시작\n"
            "- 수정이 필요하면: [🛠 수정 프롬프트]에 요청을 쓰고 복붙 모드로 수정된 케이스 생성")
        return

    log(f"\n[2단계] 순차 솔브 ({len(combos)}건, 셀당 수 분~수십 분 — 촘촘한 메시·풀 J-V일수록 길어짐)")
    rows = []
    fail_notes = []  # 셀별 실패 진단문 (fail_diagnosis.txt — ④ 작업 상세에 표시)
    jv_curves = []  # full_jv일 때 조합별 J-V 곡선 (jv.png + 재플롯 CSV)
    pin = _pin_mw_cm2(log)
    cancelled = False
    jsc_ref = jsc_ref_fresh if not resume_from else jsc_ref_resume
    if jsc_ref is None or not np.isfinite(float(jsc_ref)) or float(jsc_ref) <= 0:
        raise RuntimeError(
            "주입 광생성 전류 기준(jsc_ref)이 누락되었습니다. 임의값으로 대체하지 않습니다 — "
            "검토 모델을 다시 생성하세요.")
    jsc_ref = float(jsc_ref)
    for i, (w, g) in enumerate(combos, 1):
        if jobs.cancel_requested(jid):
            log(f"\n[중단 요청] 남은 {len(combos)-i+1}건 건너뜀")
            cancelled = True
            break
        if (w, g) not in files:  # 1단계 빌드 실패 조합 — NaN 행으로 기록하고 스킵
            rows.append({"W_um": w, "gap_um": g, "Jsc_mA_cm2": float("nan"),
                         "eta_col": float("nan"), "note": "빌드 실패",
                         **({"Voc_V": float("nan"), "FF": float("nan"),
                             "PCE_pct": float("nan")} if jv_mode == "full_jv" else {})})
            continue
        fname, area_cm2 = files[(w, g)]
        log(f"\n===== [{i}/{len(combos)}] W={w:g}um, gap={g:g}um =====")
        model = None
        pfile = jd / fname.replace("_unsolved", "_partial")
        candidate_file = jd / fname.replace("_unsolved", "_partial_candidate")
        solved_file = jd / fname.replace("_unsolved", "_solved")
        _hist, _last_v = [], None
        _best_progress = (float("-inf"), 0)  # (실제 최대 V, 점 수) — 격자가 달라도 비교 가능
        solve_completed = False
        try:
            import time as _t
            # 솔브 + 메시 사다리 재시도 (2026-07-10 배치 검증: 150nm 4건 + 100nm 5건
            # 구제 = 실패의 주 원인이 메시-뉴턴 상성임을 확인. 잔여 실패 대응으로
            # 135/90 단계 추가, 마지막 단계는 스윕 전체 미세 스텝(0.025V 균일).
            # 부분 데이터 수락 기준 (2026-07-13): Voc(≈1.05V)를 넘긴 부분 스윕만 최종
            # 결과로 인정 — 5점[0.17V]짜리 부분 회수가 사다리를 끊고 Voc 없는 NaN 행이
            # 되던 W3/g3 사례 수정. Voc 미달 부분 해는 *_partial로 보존만 하고 사다리 계속.
            last_err = None
            _vmx = float(params.get("v_max") or 1.3)
            _vst = float(params.get("v_step") or 0.05)
            _v_ok = min(1.1, _vmx)               # 부분 수락 하한 (Voc 위)
            ladder = ((1, None, False), (2, 150.0, False), (3, 100.0, False),
                      (4, 135.0, False), (5, 90.0, True))
            for attempt, hm, fine in ladder:
                # 실패 상태와 이전 solution dataset을 다음 시도에 끌고 가지 않는다.
                if model is not None:
                    try:
                        client.remove(model)
                    finally:
                        model = None
                model = client.load(str(jd / fname))
                cur_study_index = -1
                try:
                    if hm is not None:
                        model.java.mesh("mesh1").feature("size").set("custom", "on")
                        model.java.mesh("mesh1").feature("size").set("hmax", f"{hm:g}[nm]")
                    if fine and jv_mode == "full_jv":
                        # 최후 단계: 스윕 전체 미세화 (0→v_max 균일 step/2 — 시간 ~2배)
                        model.java.study("std2").feature("stat").set(
                            "plistarr", [_v_plist(_vmx, _vst / 2, knee=_vmx)])
                        log("    (최후 단계: 스윕 전체 미세 스텝)")
                    for cur_study_index, s in enumerate((model / "studies").children()):
                        t0 = _t.time()
                        log(f"  솔브: {s.name()}" + (f" (재시도 {hm:g}nm)" if hm else ""))
                        model.solve(s.name())
                        log(f"    완료 {_t.time()-t0:.1f}s")
                    last_err = None
                    _hist.append((hm, "성공", None, None, None))
                    break
                except Exception as e_s:
                    last_err = e_s
                    err1 = " ".join(str(e_s).split())[:110]
                    pts, lv, progress_ds = 0, None, None
                    recoverable_jv = False
                    if jv_mode == "full_jv":
                        # 확정된 회수 순서: 발산 모델 저장 → 제거 → 재로드 → 평가.
                        # 직접 evaluate가 거부돼 부분해를 0점으로 오판하는 경로를 없앤다.
                        try:
                            model.save(str(candidate_file))
                            client.remove(model)
                            model = None
                            model = client.load(str(candidate_file))
                            pts, lv, progress_ds = _sweep_progress(model, min_points=3)
                            log("    부분해 candidate 저장→재로드→V0 평가: "
                                + (f"{pts}점, max={lv:.3f}V, dataset={progress_ds or '기본'}"
                                   if lv is not None else "유효 스윕 없음"))
                            if lv is not None:
                                # V0 배열만 있고 terminal current가 없거나 깨진 데이터셋은
                                # 회수 가능한 J-V가 아니다. 사다리를 멈추기 전에 실제 V-I
                                # 쌍 추출까지 확인하고, 추출된 전압 범위로 다시 판정한다.
                                partial_v, _partial_i, _partial_expr = _extract_iv(
                                    model, log, preferred_dataset=progress_ds)
                                pts, lv = int(partial_v.size), float(np.max(partial_v))
                                recoverable_jv = True
                                log(f"    부분 J-V 추출 확인: {pts}점, max={lv:.3f}V")
                            if lv is not None and (lv, pts) > _best_progress:
                                try:
                                    model.save(str(pfile))
                                except Exception as e_save:
                                    log(f"    ⚠️ best partial 보존 실패({type(e_save).__name__}) — 사다리 계속")
                                else:
                                    _best_progress = (lv, pts)
                                    log(f"    best partial 갱신: {pts}점, max={lv:.3f}V")
                        except Exception as e_part:
                            log(f"    ⚠️ 부분해 저장·재로드 평가 실패({type(e_part).__name__}) — 사다리 계속")
                    shown_v = lv if lv is not None else 0.0
                    stage = ("mesh" if "building mesh" in str(e_s).lower()
                             else "sweep" if (pts >= 3 or cur_study_index >= 1) else "eq")
                    _hist.append((hm, stage, pts, shown_v, err1))
                    if (recoverable_jv and pts >= 5 and lv is not None and lv >= _v_ok
                            and pfile.exists() and _best_progress[0] >= _v_ok):
                        _last_v = lv
                        log(f"  (부분 스윕 {pts}점[max={lv:.2f}V] — 수락 기준 통과 → 회수 전환)")
                        break
                    if attempt < len(ladder):
                        log(f"  ✖ 솔브 실패({err1[:90]}) → 메시 사다리 다음 단계")
            if last_err is not None:
                raise last_err
            model.save(str(solved_file))  # 솔브 성공 즉시 보존
            solve_completed = True
            if jv_mode == "full_jv":
                # 풀 J-V: 스윕 데이터셋 자동 탐색 → 지표(Jsc/Voc/FF/PCE) + J-V 곡선 축적
                V, I_A, _expr = _extract_iv(model, log)  # 3-튜플 (2026-07-09 unpack 버그 수정)
                m, Jgen = _metrics(V, I_A, area_cm2, pin, log)
                jsc = m["Jsc_mA_cm2"]
                eta = jsc / jsc_ref if jsc == jsc else float("nan")
                rows.append({"W_um": w, "gap_um": g, "Jsc_mA_cm2": jsc,
                             "eta_col": round(eta, 4), "Voc_V": m["Voc_V"],
                             "FF": m["FF"], "PCE_pct": m["PCE_pct"], "note": ""})
                jv_curves.append((f"W{w:g}/g{g:g}", V, Jgen))
                log(f"  W{w:g}/g{g:g}: Jsc {jsc} / Voc {m['Voc_V']} / FF {m['FF']} / "
                    f"PCE {m['PCE_pct']}% (수집효율 {eta:.1%})")
            else:
                I = 0.0
                for dsn in [d.name() for d in (model / "datasets").children()]:
                    try:
                        val = float(np.ravel(model.evaluate("semi.I0_1", dataset=dsn))[-1])
                        log(f"    데이터셋 {dsn}: I0_1 = {val:.4e} A")
                        if abs(val) > abs(I):
                            I = val
                    except Exception:
                        continue
                if I == 0.0:  # 전 데이터셋 0 → 접점 배선 의심, 엔티티 덤프
                    for tag in ("mc1", "mc2", "adm_s", "adm_n"):
                        try:
                            ents = model.java.physics("semi").feature(tag).selection().entities(
                                2 if tag.startswith("mc") else 3)
                            log(f"    {tag} 엔티티: {list(ents)[:12]}")
                        except Exception as e:
                            log(f"    {tag} 엔티티 조회 실패: {type(e).__name__}")
                    raise RuntimeError("SC 전류가 모든 데이터셋에서 0 — 로그의 엔티티 덤프 분석 필요")
                jsc = abs(I) * 1000.0 / area_cm2
                eta = jsc / jsc_ref
                rows.append({"W_um": w, "gap_um": g, "Jsc_mA_cm2": round(jsc, 3),
                             "eta_col": round(eta, 4), "note": ""})
                log(f"  Jsc = {jsc:.3f} mA/cm² (수집효율 {eta:.1%}, 광학 상한 {jsc_ref:.2f})")
            if len(_hist) > 1:  # 재시도와 결과 추출까지 성공한 뒤에만 최종 성공으로 진단
                diag = _diagnose_fail(f"W{w:g}/g{g:g} (최종 성공)", _hist)
                log(diag)
                fail_notes.append(diag)
            # (solved 저장은 솔브 직후 위에서 수행 — 평가 오류가 나도 해는 보존됨)
        except Exception as e:
            log(f"  ✖ 셀 실패 ({type(e).__name__}: {str(e)[:180]})")
            if _hist and _hist[-1][1] == "성공":
                _hist.append((_hist[-1][0], "eval", None, None,
                              " ".join(str(e).split())[:110]))
            # 부분 해/솔브본 회수: 어떤 경우에도 열린 실패 모델을 직접 평가하지 않고
            # canonical 저장본을 새로 로드한다(2026-07-10 서버 실측 순서).
            recorded = False
            diag_outcome = None
            if model is not None:
                try:
                    client.remove(model)
                except Exception as e_remove:
                    log(f"    열린 모델 제거 실패({type(e_remove).__name__}) — 저장본 복구는 계속")
                finally:
                    model = None
            recovery_files = []
            if solve_completed and solved_file.exists():
                recovery_files.append((solved_file, True))
            if pfile.exists() and pfile != solved_file:
                recovery_files.append((pfile, False))
            if jv_mode == "full_jv":
                for recovery_file, recovery_is_complete in recovery_files:
                    try:
                        log(f"    저장본 재로드 후 평가: {recovery_file.name}")
                        model = client.load(str(recovery_file))
                        V, I_A, _e2 = _extract_iv(model, log)
                        if V.size < 5:
                            raise RuntimeError(f"J-V 점 수 부족: {V.size}")
                        m, Jgen = _metrics(V, I_A, area_cm2, pin, log)
                        jsc = m["Jsc_mA_cm2"]
                        eta = jsc / jsc_ref if jsc == jsc else float("nan")
                        _last_v = float(np.max(V))
                        voltage_ok = recovery_is_complete or _last_v >= _v_ok
                        if voltage_ok:
                            diag_outcome = "accepted"
                            note = ("저장본 재로드(완전해)" if recovery_is_complete else
                                    f"부분회수 {V.size}점[max={_last_v:.2f}V]")
                            voc, ff, pce = m["Voc_V"], m["FF"], m["PCE_pct"]
                            curve_label = f"W{w:g}/g{g:g}" + ("" if recovery_is_complete else "*")
                        else:
                            # Jsc(V=0)와 원시 곡선은 참고용으로 보존하되, 수락 기준 미달
                            # 부분해의 Voc/FF/PCE가 완전한 J-V 결과로 섞이지 않게 한다.
                            diag_outcome = "preserved"
                            note = f"참고용 부분해 {V.size}점[max={_last_v:.2f}V, 수락 미달]"
                            voc = ff = pce = float("nan")
                            curve_label = f"W{w:g}/g{g:g}†"
                        rows.append({"W_um": w, "gap_um": g, "Jsc_mA_cm2": jsc,
                                     "eta_col": round(eta, 4), "Voc_V": voc,
                                     "FF": ff, "PCE_pct": pce, "note": note})
                        jv_curves.append((curve_label, V, Jgen))
                        log(f"  ↺ 저장본 평가 성공: {V.size}점 [max={_last_v:.2f}V] — "
                            f"{('수락' if voltage_ok else '참고용 보존(지표 미집계)')}; "
                            f"Jsc {jsc} / Voc {voc} / PCE {pce}%")
                        recorded = True
                        break
                    except Exception as e2:
                        log(f"    {recovery_file.name} 회수 실패({type(e2).__name__}) — 다음 저장본 시도")
                        try:
                            if model is not None:
                                client.remove(model)
                        except Exception:
                            pass
                        finally:
                            model = None
            if not recorded:
                log("  — NaN 기록, 다음 진행")
                fail_row = {"W_um": w, "gap_um": g, "Jsc_mA_cm2": float("nan"),
                            "eta_col": float("nan"), "note": "실패(사다리 소진)"}
                if jv_mode == "full_jv":  # 히트맵 키 일치 (실패행 KeyError 방지)
                    fail_row.update({"Voc_V": float("nan"), "FF": float("nan"),
                                     "PCE_pct": float("nan")})
                rows.append(fail_row)
            # '왜 실패했나' 진단문 — 로그 + fail_diagnosis.txt (④ 작업 상세에 표시)
            diag = _diagnose_fail(f"W{w:g}/g{g:g}", _hist, diag_outcome, _last_v)
            log(diag)
            fail_notes.append(diag)
        finally:
            try:
                if model is not None:
                    client.remove(model)
            except Exception:
                pass
            try:
                candidate_file.unlink(missing_ok=True)
            except Exception:
                pass
    if rows:
        _save_csv(jid, rows)
        try:
            hk = (("Jsc_mA_cm2", "Jsc [mA/cm2]"), ("eta_col", "수집효율"))
            if jv_mode == "full_jv":
                hk = (("Jsc_mA_cm2", "Jsc [mA/cm2]"), ("PCE_pct", "PCE [%]"),
                      ("Voc_V", "Voc [V]"), ("FF", "FF"))
            _plot_heatmap(jid, ws, gs, rows,
                          {"param": "W_um", "field": "w_list_um", "label": "전극 폭 W [um]"},
                          {"param": "gap_um", "field": "gap_list_um", "label": "간격 gap [um]"},
                          keys=hk,
                          suptitle=f"IBC 3D unit cell (finger {lz_um:g}um, tip {tip_um:g}um)")
        except Exception as e:
            log(f"⚠️ 히트맵 실패: {type(e).__name__}: {e}")
    if jv_curves:
        try:
            _plot_jv(jid, jv_curves)
            log("J-V 곡선 저장: jv.png (+ 재플롯용 jv_curves.csv)")
        except Exception as e:
            log(f"⚠️ J-V 플롯 실패: {type(e).__name__}: {e}")
    if fail_notes:  # 실패/부분회수 진단서 — ④ 작업 상세 인라인 표시용 (2026-07-13)
        try:
            (jd / "fail_diagnosis.txt").write_text(
                "실패·부분회수 셀 진단 — 같은 물리인데 왜 이 치수만 실패했나\n"
                + "=" * 62 + "\n\n" + "\n\n".join(fail_notes) + "\n", encoding="utf-8")
            log(f"\n실패 진단서 저장: fail_diagnosis.txt ({len(fail_notes)}건)")
        except Exception as e:
            log(f"⚠️ 진단서 저장 실패: {type(e).__name__}")
    if jv_mode == "full_jv":
        ok = [r for r in rows if r.get("PCE_pct") == r.get("PCE_pct")]
    else:
        ok = [r for r in rows if r["Jsc_mA_cm2"] == r["Jsc_mA_cm2"]]
    if ok:
        best = max(ok, key=lambda r: r["PCE_pct"] if jv_mode == "full_jv"
                   else r["Jsc_mA_cm2"])
        metric = (f"PCE {best['PCE_pct']}% / Jsc {best['Jsc_mA_cm2']} mA/cm²"
                  if jv_mode == "full_jv" else f"Jsc {best['Jsc_mA_cm2']} mA/cm²")
        log(f"\n최적: W={best['W_um']:g} gap={best['gap_um']:g} → {metric}")
        if tip_um >= lz_um:
            log("[검증] 압출 극한 모드 — '같은 메시 설정'의 2D IBC Jsc와 ~2% 내 일치해야 통과 "
                "(기본 메시끼리 W3/g3 wave: 2D 14.55. 절대값은 메시 수렴 필요 — 2D 사양 6.9절)")
    elif jv_mode == "full_jv" and any(r["Jsc_mA_cm2"] == r["Jsc_mA_cm2"] for r in rows):
        log("\n완전한 J-V 지표는 없고 Jsc 참고값만 보존됐습니다.")
    if cancelled:
        raise jobs.Cancelled()


# sync-marker: 2026-07-06 rev3 (v0.3 데이터 케이스)
