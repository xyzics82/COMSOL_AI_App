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

def _extract_iv(model, log):
    """전압 스윕 결과에서 (V, I) 배열 추출.

    스터디가 여러 개면 데이터셋도 여러 개 → V0가 스윕 배열(size>=3)로 나오는
    데이터셋을 자동 탐색한다. 전류 표현식도 후보 탐색.
    """
    def _eval(expr, dset):
        arr = model.evaluate(expr) if dset is None else model.evaluate(expr, dataset=dset)
        return np.atleast_1d(np.array(arr, dtype=float)).ravel()

    dsets = [None]
    try:
        dsets += [d.name() for d in (model / "datasets").children()]
    except Exception:
        pass
    V, chosen = None, None
    for ds in dsets:
        try:
            v = _eval("V0", ds)
            log(f"  데이터셋 {ds or '(기본)'}: V0 {v.size}점")
            if v.size >= 3:
                V, chosen = v, ds
                break
        except Exception as e:
            log(f"  데이터셋 {ds or '(기본)'}: V0 평가 실패 ({type(e).__name__})")
    if V is None:
        raise RuntimeError("V0 스윕 배열을 주는 데이터셋을 찾지 못함 — 로그 회신 요청")
    log(f"V0 평가: {V.size}점 [{V.min():.3g}..{V.max():.3g}] V (dataset={chosen or '기본'})")

    candidates = ["semi.I0_1", "semi.I0_2", "semi.mc1.I0", "semi.mc2.I0"]
    for expr in candidates:
        try:
            I = _eval(expr, chosen)
            if I.size == V.size and np.ptp(np.abs(I)) > 0:
                log(f"전류 표현식 채택: {expr}")
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
    taun = f"{float(params.get('taun_ns') or 38.7):g}[ns]"
    mode = params.get("mode", "local")
    gen_mode = str(params.get("gen_mode", "wave_optics"))
    log(f"IBC 그리드: W {ws}um × gap {gs}um → {len(ws)*len(gs)}회 솔브 / t_abs={t_abs:g}nm "
        f"/ mode={mode} / 광생성={gen_mode}")
    g_profile = None
    if gen_mode == "wave_optics":
        from . import wo_optics
        client_early = get_client(log)
        g_profile = wo_optics.compute_G_profile(client_early, t_abs, log,
                                                nlam=int(float(params.get("nlam") or 15)))
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
            g_profile=g_profile, s_ifc_cms=float(params.get("s_ifc_cms") or 0),
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
            jsc_ref = g_profile["jsc_wave"] if g_profile else 26.261  # 광학 상한 대비 수집효율
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


def _run_ibc3d(jid, params, log, get_client):
    """IBC 3D 단위 셀 (v4): W×gap 그리드, 핑거 길이·팁 파라미터, Jsc 전용.

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
    taun = f"{float(params.get('taun_ns') or 38.7):g}[ns]"
    mode = params.get("mode", "local")
    gen_mode = str(params.get("gen_mode", "wave_optics"))
    jv_mode = str(params.get("jv_mode", "jsc_only"))
    if str(params.get("hpc_only", "")).lower() in ("yes", "true", "1") and mode != "export":
        raise RuntimeError(
            "이 케이스는 서버 컴퓨터 전용(HPC)입니다 — 이 PC에서 local 솔브를 막았습니다. "
            "mode=export로 unsolved+run_server.bat을 만들어 서버에서 돌리세요. "
            "절차: docs/SERVER_GUIDE.md")
    log(f"IBC 3D 그리드: W {ws} × gap {gs} um → {len(ws)*len(gs)}셀 / 핑거 {lz_um:g}um "
        f"(팁 {tip_um:g}um{' = 압출 극한(2D 대조 검증 모드)' if tip_um >= lz_um else ''}) "
        f"/ {'풀 J-V(0-2V/0.05)' if jv_mode == 'full_jv' else 'Jsc 전용'} / 광생성={gen_mode}")
    mats = {
        "absorber": library.material_props("mapbi3")["props"],
        "sno2": library.material_props("sno2")["props"],
        "niox": library.material_props("niox")["props"],
        "sno2_nd": "1e19[1/cm^3]",
        "niox_na": "1e18[1/cm^3]",
    }
    g_profile = None
    if gen_mode == "wave_optics":
        from . import wo_optics
        g_profile = wo_optics.compute_G_profile(client, t_abs, log,
                                                nlam=int(float(params.get("nlam") or 15)))
    jd = jobs.job_dir(jid)
    combos = [(w, g) for g in gs for w in ws]
    files = {}
    log(f"\n[1단계] unsolved 모델 {len(combos)}개 생성")
    for i, (w, g) in enumerate(combos, 1):
        jobs.check_cancel(jid)
        model, area_cm2 = ibc3d_builder.build(
            client, f"ibc3d_{w:g}_{g:g}", mats, w * 1000.0, g * 1000.0, t_abs, taun,
            lz_um * 1000.0, tip_um * 1000.0, log, g_profile=g_profile,
            v0_only=(jv_mode != "full_jv"),
            vcfg={"start": 0.0, "stop": 2.0, "step": 0.05},
            s_ifc_cms=float(params.get("s_ifc_cms") or 0),
            mesh_hmax_nm=(float(params.get("mesh_hmax_nm")) if params.get("mesh_hmax_nm") else None))
        fname = f"ibc3d_W{w:g}_g{g:g}_unsolved.mph"
        model.save(str(jd / fname))
        client.remove(model)
        files[(w, g)] = (fname, area_cm2)
        log(f"  [{i}/{len(combos)}] unsolved 저장: {fname}")
    _write_server_script(jid, [files[c][0] for c in combos], log)
    if mode == "export":
        log("반출용 생성 완료")
        return

    log(f"\n[2단계] 순차 솔브 ({len(combos)}건, 셀당 수 분 가능)")
    rows = []
    cancelled = False
    jsc_ref = g_profile["jsc_wave"] if g_profile else 26.261
    for i, (w, g) in enumerate(combos, 1):
        if jobs.cancel_requested(jid):
            log(f"\n[중단 요청] 남은 {len(combos)-i+1}건 건너뜀")
            cancelled = True
            break
        fname, area_cm2 = files[(w, g)]
        log(f"\n===== [{i}/{len(combos)}] W={w:g}um, gap={g:g}um =====")
        model = None
        try:
            import time as _t
            model = client.load(str(jd / fname))
            for s in (model / "studies").children():
                t0 = _t.time()
                log(f"  솔브: {s.name()}")
                model.solve(s.name())
                log(f"    완료 {_t.time()-t0:.1f}s")
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
                         "eta_col": round(eta, 4)})
            log(f"  Jsc = {jsc:.3f} mA/cm² (수집효율 {eta:.1%}, 광학 상한 {jsc_ref:.2f})")
            model.save(str(jd / fname.replace("_unsolved", "_solved")))
        except Exception as e:
            log(f"  ✖ 셀 실패 ({type(e).__name__}: {str(e)[:180]}) — 다음 진행")
            rows.append({"W_um": w, "gap_um": g, "Jsc_mA_cm2": float("nan"),
                         "eta_col": float("nan")})
        finally:
            try:
                if model is not None:
                    client.remove(model)
            except Exception:
                pass
    if rows:
        _save_csv(jid, rows)
        try:
            _plot_heatmap(jid, ws, gs, rows,
                          {"param": "W_um", "field": "w_list_um", "label": "전극 폭 W [um]"},
                          {"param": "gap_um", "field": "gap_list_um", "label": "간격 gap [um]"},
                          keys=(("Jsc_mA_cm2", "Jsc [mA/cm2]"), ("eta_col", "수집효율")),
                          suptitle=f"IBC 3D unit cell (finger {lz_um:g}um, tip {tip_um:g}um)")
        except Exception as e:
            log(f"⚠️ 히트맵 실패: {type(e).__name__}: {e}")
    ok = [r for r in rows if r["Jsc_mA_cm2"] == r["Jsc_mA_cm2"]]
    if ok:
        best = max(ok, key=lambda r: r["Jsc_mA_cm2"])
        log(f"\n최적: W={best['W_um']:g} gap={best['gap_um']:g} → Jsc {best['Jsc_mA_cm2']} mA/cm²")
        if tip_um >= lz_um:
            log("[검증] 압출 극한 모드 — '같은 메시 설정'의 2D IBC Jsc와 ~2% 내 일치해야 통과 "
                "(기본 메시끼리 W3/g3 wave: 2D 14.55. 절대값은 메시 수렴 필요 — 2D 사양 6.9절)")
    if cancelled:
        raise jobs.Cancelled()


# sync-marker: 2026-07-06 rev3 (v0.3 데이터 케이스)
