"""환경 점검(구 Phase 0 스파이크) — 웹 작업으로 실행.

7개 체크 + Si 예제 모델의 '완전한 구조 덤프'(노드 태그/타입/속성).
이 덤프가 페로브스카이트 빌드 스크립트의 정확한 API 호출을 확정하는 근거가 된다.
로그 전체를 UI에서 복사해 Claude에게 회신할 것.
"""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work"

COMSOL_APPS = Path(r"C:\Program Files\COMSOL\COMSOL64\Multiphysics_copy1\applications")
SRC_MPH = COMSOL_APPS / "Semiconductor_Module" / "Photonic_Devices_and_Sensors" / "si_solar_cell_1d.mph"


def _dump_node(node, log, depth=1, max_depth=3):
    pad = "  " * depth
    try:
        children = list(node.children())
    except Exception:
        children = []
    for ch in children:
        try:
            typ = ch.type()
        except Exception:
            typ = "?"
        log(f"{pad}{ch.name()} [tag={ch.tag()}, type={typ}]")
        # 속성 덤프 (빌드 스크립트 작성의 핵심 정보)
        try:
            props = ch.properties()
            for k, v in props.items():
                s = str(v)
                if len(s) > 160:
                    s = s[:160] + "...(생략)"
                log(f"{pad}   .{k} = {s}")
        except Exception as e:
            log(f"{pad}   (속성 읽기 실패: {e})")
        if depth < max_depth:
            _dump_node(ch, log, depth + 1, max_depth)


def run(jid, params, log, get_client):
    results = []

    def check(name, fn):
        log(f"\n===== [CHECK] {name} =====")
        try:
            out = fn()
            results.append((name, "PASS"))
            log(f"[PASS] {name}")
            return out
        except Exception:
            import traceback
            results.append((name, "FAIL"))
            log("[FAIL] " + name + "\n" + traceback.format_exc())
            return None

    import sys
    log(f"Python: {sys.version}")

    mph = check("0. import mph", lambda: __import__("mph"))
    if mph is None:
        _summary(log, results)
        return
    log(f"mph version: {getattr(mph, '__version__', '?')}")

    client = check("1. COMSOL 세션+버전", lambda: get_client(log))
    if client is None:
        _summary(log, results)
        return

    def load_model():
        if not SRC_MPH.exists():
            raise FileNotFoundError(f"예제 없음: {SRC_MPH} — diagnostics.py의 COMSOL_APPS 수정 필요")
        dst = WORK / "si_solar_cell_1d.mph"
        shutil.copy(SRC_MPH, dst)
        m = client.load(str(dst))
        log(f"loaded: {m.name()}")
        return m

    model = check("2. Si 예제 복사+로드", load_model)
    if model is None:
        _summary(log, results)
        return

    def dump():
        log("--- parameters ---")
        for k, v in model.parameters().items():
            log(f"  {k} = {v}")
        for group in ["functions", "geometries", "materials", "physics",
                      "meshes", "studies", "solutions", "datasets",
                      "evaluations", "tables", "plots", "exports"]:
            try:
                node = model / group
                log(f"--- {group} ---")
                _dump_node(node, log, depth=1,
                           max_depth=3 if group in ("physics", "studies", "functions", "materials") else 2)
            except Exception as e:
                log(f"--- {group}: 접근 실패 ({e}) ---")
        try:
            j = model.java
            for t in j.variable().tags():
                v = j.variable(str(t))
                for n in v.varnames():
                    log(f"  variable {t}: {n} = {v.get(str(n))}")
        except Exception as e:
            log(f"variables via java 실패: {e}")

    check("3. 모델 구조 덤프", dump)

    def param_save():
        model.parameter("V0", "0[V]")
        p = WORK / "si_unsolved_copy.mph"
        model.save(str(p))
        log(f"미솔브 저장: {p} exists={p.exists()}")

    check("4. 파라미터 변경+미솔브 저장", param_save)

    def solve():
        import time
        for s in (model / "studies").children():
            t0 = time.time()
            log(f"솔브 시작: {s.name()}")
            model.solve(s.name())
            log(f"  완료 {time.time()-t0:.1f}s")

    check("5. 솔브(전체 스터디)", solve)

    def evaluate():
        n = model.evaluate("semi.N")
        log(f"도메인 평가 OK: n array len={getattr(n, 'shape', len(n))}")
        for expr in ["semi.I0_1", "semi.I0_2", "semi.mc1.I0", "semi.mc2.I0", "V0"]:
            try:
                val = model.evaluate(expr)
                s = str(val)
                log(f"  OK  {expr} -> {s[:200]}")
            except Exception as e:
                log(f"  --  {expr}: {type(e).__name__}")

    check("6. 결과 평가(evaluate)", evaluate)

    def reopen():
        p = WORK / "si_solved.mph"
        model.save(str(p))
        m2 = client.load(str(p))
        n = m2.evaluate("semi.N")
        log(f"재열기+재솔브 없이 평가 OK: len={getattr(n, 'shape', len(n))}")

    check("7. 솔브본 저장→재열기→평가", reopen)

    _summary(log, results)


def quick(jid, params, log, get_client):
    """빠른 환경 점검 (2회차 이후 평소용) — 예시 솔브 없이 핵심 상태만 수 초 내 확인."""
    import os
    import sys
    log("=== 빠른 환경 점검 ===")
    log(f"[시스템] Python {sys.version.split()[0]} / CPU {os.cpu_count()}코어")
    try:
        import ctypes

        class _MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        ms = _MS()
        ms.dwLength = ctypes.sizeof(_MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
        log(f"[시스템] 메모리 전체 {ms.ullTotalPhys/2**30:.1f} GB / 가용 {ms.ullAvailPhys/2**30:.1f} GB "
            f"(사용률 {ms.dwMemoryLoad}%)")
    except Exception as e:
        log(f"[시스템] 메모리 조회 불가 ({type(e).__name__})")

    from . import runner
    already = runner._client is not None
    try:
        client = get_client(log)
        if already:
            log(f"[COMSOL] 연결 OK (version {client.version}) — 기존 세션 재사용")
            log("  참고: 세션이 살아 있으면 동글을 중간에 뽑아도 통과할 수 있음. 확실한 동글 확인은 서버 재시작 직후의 빠른 점검")
        else:
            log(f"[COMSOL] 연결 OK (version {client.version}) — 라이선스 체크아웃 성공 = 동글 정상")
    except Exception as e:
        log(f"[COMSOL] 연결 실패: {e}")

    from . import data_prep
    st = data_prep.status()
    lack = [s["name"] for s in st if not s["ready"]]
    log(f"[데이터] {len(st)-len(lack)}/{len(st)} 준비됨" + (f" — 누락: {', '.join(lack)}" if lack else ""))

    from . import comsol_cases
    cs = comsol_cases.get_cases()
    log(f"[케이스] {len(cs)}개 등록: " + ", ".join(c["id"] for c in cs))

    from . import llm_propose
    s = llm_propose.status()
    if s["ready"]:
        log(f"[자연어 AI] API 모드 사용 가능 (모델 {s['model']}) — 복붙 모드도 병행 가능")
    else:
        log(f"[자연어 AI] 복붙 모드로 사용 (API 비활성: {s['reason']})")
    log("=== 빠른 점검 완료 ===")


def spike_nd(jid, params, log, get_client):
    """v2 스파이크: 2D/3D 최소 평판 다이오드 빌드→솔브→구조 덤프.

    목적: Box 선택 바인딩, semi 면외두께(d), 2D/3D 수렴, I0 평가 등 미확정 API를
    실제 COMSOL 로그로 확정한다. 로그 전체를 Claude에게 회신할 것.
    """
    import time

    from . import jobs, library, stack_builder_nd
    dim = int(params.get("dim", 2))
    client = get_client(log)
    log(f"=== {dim}D 스파이크: 2층 평판 다이오드 (mapbi3 동종접합, v1 케이스 도핑 재사용) ===")
    mat = library.material_props("mapbi3")

    def L(name, t, absorber=False, doping=None, srh=None):
        d = {"name": name, "material": "mapbi3", "material_version": mat["version"],
             "thickness_nm": float(t), "props": mat["props"], "refs": mat["refs"],
             "absorber": absorber}
        if doping:
            d["doping"] = doping
        if srh:
            d["srh"] = srh
        return d

    layers = [
        L("p_side", 300, doping={"type": "acceptor", "conc": "1e18[1/cm^3]"}),
        L("n_side", 500, absorber=True,
          doping={"type": "donor", "conc": "2.6e17[1/cm^3]"},
          srh={"taun": "38.7[ns]", "taup": "38.7[ns]"}),
    ]
    gen = {"lambda_nm": [300, 850], "spectrum_dataset": "am15", "nk_dataset": "mapbi3_nk"}
    vcfg = {"start": 0.0, "stop": 0.4, "step": 0.1}
    model, area_cm2 = stack_builder_nd.build(client, f"spike_{dim}d", layers, gen, vcfg,
                                             log, dim=dim)
    log(f"단면적 가정: {area_cm2:g} cm² (전류밀도 환산용 — 평판이므로 1D와 J가 같아야 함)")
    jd = jobs.job_dir(jid)
    model.save(str(jd / f"spike_{dim}d_unsolved.mph"))
    for s in (model / "studies").children():
        t0 = time.time()
        log(f"솔브 시작: {s.name()}")
        model.solve(s.name())
        log(f"  완료 {time.time() - t0:.1f}s")
    for expr in ["V0", "semi.I0_1", "semi.I0_2", "semi.mc1.I0", "semi.mc2.I0"]:
        try:
            val = model.evaluate(expr)
            log(f"  OK  {expr} -> {str(val)[:200]}")
        except Exception as e:
            log(f"  --  {expr}: {type(e).__name__}")
    model.save(str(jd / f"spike_{dim}d_solved.mph"))
    log("\n--- 구조 덤프 (선택 바인딩·semi 속성 확인용) ---")
    for group in ["geometries", "physics", "studies"]:
        try:
            node = model / group
            log(f"--- {group} ---")
            _dump_node(node, log, depth=1, max_depth=3 if group == "physics" else 2)
        except Exception as e:
            log(f"--- {group}: 접근 실패 ({e}) ---")
    log(f"\n>> 이 로그 전체를 '로그 복사'로 복사해 Claude에게 회신하세요 — {dim}D API 확정에 사용됩니다 <<")


def spike_ibc(jid, params, log, get_client):
    """IBC 2D 스파이크: W3/gap3 1조합 — Union 선택·수렴·2D 전하분포 평가를 확정."""
    import time

    from . import ibc_builder, jobs, library
    client = get_client(log)
    etl = str(params.get("etl", "sno2"))  # 판별 실험용: sno2(장벽 0.49eV) vs c60(무장벽)
    log(f"=== IBC 2D 스파이크 (W=3um, gap=3um, t_abs=800nm, ETL={etl}) ===")
    mats = {
        "absorber": library.material_props("mapbi3")["props"],
        "sno2": library.material_props(etl)["props"],   # 'sno2' 슬롯에 실험 ETL 주입
        "niox": library.material_props("niox")["props"],
        "sno2_nd": "1e19[1/cm^3]" if etl == "sno2" else "2.6e17[1/cm^3]",  # meskini2024 / rscadv2024
        "niox_na": "1e18[1/cm^3]",   # sahu2018 Table 1
    }
    vcfg = {"start": 0.0, "stop": 1.6, "step": 0.1}
    model, area = ibc_builder.build(client, "spike_ibc", mats, 3000.0, 3000.0, 800.0,
                                    "38.7[ns]", vcfg, log)
    log(f"단면적: {area:g} cm² (L × 전극길이 10um)")
    jd = jobs.job_dir(jid)
    model.save(str(jd / "spike_ibc_unsolved.mph"))
    for s in (model / "studies").children():
        t0 = time.time()
        log(f"솔브 시작: {s.name()}")
        model.solve(s.name())
        log(f"  완료 {time.time() - t0:.1f}s")
    import numpy as _np
    ds = "Study 2//Solution 2"
    for expr in ["V0", "semi.I0_1", "semi.I0_2"]:
        try:
            arr = _np.ravel(model.evaluate(expr, dataset=ds))
            log(f"  {expr} @스윕: n={arr.size}, 처음 {arr[:3]}, 끝 {arr[-3:]}")
        except Exception as e:
            log(f"  {expr} @스윕 실패: {type(e).__name__} {str(e)[:100]}")
    # 배선 최종 확인: 각 물리 기능이 실제로 잡은 엔티티 번호
    for tag, edim in [("mc1", 1), ("mc2", 1), ("adm_s", 2), ("adm_n", 2), ("udg1", 2),
                      ("smm_s", 2), ("smm_n", 2)]:
        try:
            ents = model.java.physics("semi").feature(tag).selection().entities(edim)
            log(f"  {tag} 엔티티(dim{edim}): {list(ents)[:20]}")
        except Exception as e:
            log(f"  {tag} 엔티티 조회 실패: {type(e).__name__} {str(e)[:80]}")
    # 결정 실험: 정전위장이 바이어스에 따라 변하는가?
    # 변함 → 바이어스는 걸림(전류 평탄 = 물리적 차단) / 불변 → BC·스터디 버그
    try:
        Vf = model.evaluate("V", dataset=ds)
        vals = Vf if isinstance(Vf, (list, tuple)) else [Vf]
        maxes = [float(_np.max(_np.ravel(v))) for v in vals]
        mins = [float(_np.min(_np.ravel(v))) for v in vals]
        log(f"  전위장 V @스윕: 해 {len(maxes)}개, max(V) 처음 {maxes[:4]} 끝 {maxes[-3:]}")
        log(f"    min(V) 처음 {mins[:4]} 끝 {mins[-3:]}")
    except Exception as e:
        log(f"  전위장 평가 실패: {type(e).__name__} {str(e)[:120]}")
    log("\n--- physics 덤프 (mc1/mc2 배선 확인) ---")
    try:
        _dump_node(model / "physics", log, depth=1, max_depth=3)
    except Exception as e:
        log(f"physics 덤프 실패: {e}")
    log("\n--- selections 덤프 ---")
    try:
        _dump_node(model / "selections", log, depth=1, max_depth=2)
    except Exception as e:
        log(f"selections 덤프 실패: {e}")
    # 2D 전하분포 평가 시험 (케이스의 charge2d 플롯용 API 확정)
    try:
        import numpy as np
        X = np.ravel(model.evaluate("x"))
        Y = np.ravel(model.evaluate("y"))
        N = np.ravel(model.evaluate("semi.N"))
        m = min(X.size, Y.size, N.size)
        log(f"  2D 필드 평가 OK: 점 {m}개, x범위 {X.min():.3g}~{X.max():.3g}, "
            f"y범위 {Y.min():.3g}~{Y.max():.3g}, N범위 {N.min():.3g}~{N.max():.3g}")
        scale = 1e6 if abs(X).max() < 1e-2 else 1.0  # m로 오면 um 환산
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(7.5, 3.2))
        tc = ax.tripcolor(X[:m] * scale, Y[:m] * scale,
                          np.log10(np.clip(N[:m], 1.0, None)), shading="gouraud")
        fig.colorbar(tc, ax=ax, label="log10(electron conc.)")
        ax.set_xlabel("x [um]")
        ax.set_ylabel("y [um]")
        ax.set_title("IBC spike: electron concentration")
        fig.tight_layout()
        fig.savefig(jd / "spike_ibc_charge2d.png", dpi=140)
        log("  전하분포 시험 플롯 저장 OK: spike_ibc_charge2d.png")
    except Exception as e:
        log(f"  2D 필드 평가/플롯 실패 ({type(e).__name__}: {str(e)[:200]}) — 다음 반복에서 수정")
    model.save(str(jd / "spike_ibc_solved.mph"))
    log(">> IBC 스파이크 완료 — Claude가 로그를 자동 분석합니다 <<")


def spike_wo(jid, params, log, get_client):
    """Wave Optics API 확정 스파이크 (v3 광학·전기 결합 1단계 — SPEC_OPTICS_IDE.md O-1).

    설치된 Wave Optics 예제(plasmonic_wire_grating 우선)를 로드해 전체 구조를 덤프한다.
    v0의 si_solar_cell_1d 덤프와 같은 전략 — 주기 포트/Floquet/굴절률/주파수 스터디의
    정확한 API 속성명을 실제 작동 모델에서 확정한다.
    """
    import shutil
    import time

    from . import jobs
    apps = COMSOL_APPS  # 설치 applications 폴더
    hits = (list(apps.rglob("plasmonic_wire_grating*.mph"))
            or list(apps.rglob("*wire_grating*.mph"))
            or list(apps.rglob("*grating*.mph")))
    if not hits:
        wo = apps / "Wave_Optics_Module"
        if wo.exists():
            hits = sorted(wo.rglob("*.mph"))[:1]
    if not hits:
        log("Wave Optics 예제(.mph)를 찾지 못했습니다 — applications 하위 폴더 목록:")
        try:
            for p in sorted(apps.iterdir()):
                log(f"  - {p.name}")
        except Exception as e:
            log(f"  (목록 실패: {e})")
        log("Wave Optics Module 예제 경로를 알려주시면 반영합니다")
        return
    src = hits[0]
    log(f"예제 발견: {src}")
    client = get_client(log)
    dst = jobs.job_dir(jid) / src.name
    shutil.copy(src, dst)
    model = client.load(str(dst))
    log(f"로드 OK: {model.name()}")
    log("--- parameters ---")
    try:
        for k, v in model.parameters().items():
            log(f"  {k} = {v}")
    except Exception as e:
        log(f"  (parameters 실패: {e})")
    for group in ["functions", "geometries", "materials", "physics",
                  "meshes", "studies", "datasets", "evaluations"]:
        try:
            log(f"--- {group} ---")
            _dump_node(model / group, log, depth=1,
                       max_depth=3 if group in ("physics", "materials", "studies") else 2)
        except Exception as e:
            log(f"--- {group}: 접근 실패 ({e}) ---")
    try:
        t0 = time.time()
        model.solve()
        log(f"솔브 OK ({time.time() - t0:.1f}s)")
        for expr in ["ewfd.S11", "ewfd.S21", "ewfd.Atotal", "ewfd.Rtotal", "ewfd.Ttotal"]:
            try:
                val = model.evaluate(expr)
                log(f"  OK  {expr} -> {str(val)[:160]}")
            except Exception as e:
                log(f"  --  {expr}: {type(e).__name__}")
    except Exception as e:
        log(f"솔브 실패({type(e).__name__}: {str(e)[:150]}) — 덤프만으로 진행")
    log(">> 이 덤프가 Wave Optics 빌더(광학-전기 결합)의 API 사전이 됩니다 — Claude가 분석 <<")


def spike_wo2(jid, params, log, get_client):
    """O-2: 공기/MAPbI3(800nm)/공기 슬랩을 ewfd로 직접 빌드 → R/T/A를 TMM과 대조.

    2026-07-08 O-1 덤프로 확정된 API 사용: WaveEquationElectric(n_mat/ki_mat userdef),
    PeriodicPort, FloquetPeriodicCondition(Floquet_source=FromPeriodicPort),
    Frequency 스터디, ewfd.Rtotal/Ttotal/Atotal.
    검증 기준: |R−R_TMM|, |A−A_TMM| < 0.03.
    """
    import cmath

    import numpy as np

    from . import data_prep, jobs
    from .stack_builder import _try_set
    lam_nm = float(params.get("lam_nm", 550))
    t_abs = 800.0
    client = get_client(log)
    nk = np.loadtxt(ROOT / "data" / data_prep.dataset("mapbi3_nk")["file"], encoding="utf-8")
    n2r = float(np.interp(lam_nm / 1000, nk[:, 0], nk[:, 1]))
    k2 = float(np.interp(lam_nm / 1000, nk[:, 0], nk[:, 2]))
    log(f"=== WO-2 슬랩 검증: λ={lam_nm}nm, MAPbI3 n={n2r:.4f}, k={k2:.5f} (Phillips) ===")
    d = t_abs * 1e-9
    lam = lam_nm * 1e-9
    n1, n2c, n3 = 1.0, complex(n2r, k2), 1.0
    r12 = (n1 - n2c) / (n1 + n2c)
    r23 = (n2c - n3) / (n2c + n3)
    t12 = 2 * n1 / (n1 + n2c)
    t23 = 2 * n2c / (n2c + n3)
    beta = 2 * cmath.pi * n2c * d / lam
    e2 = cmath.exp(2j * beta)
    r = (r12 + r23 * e2) / (1 + r12 * r23 * e2)
    t = (t12 * t23 * cmath.exp(1j * beta)) / (1 + r12 * r23 * e2)
    R_t, T_t = abs(r) ** 2, abs(t) ** 2
    A_t = 1 - R_t - T_t
    log(f"TMM 기준: R={R_t:.4f} T={T_t:.4f} A={A_t:.4f}")

    model = client.create("spike_wo2")
    j = model.java
    try:
        j.component().create("comp1", True)
    except Exception:
        j.modelNode().create("comp1")
    comp = j.component("comp1")
    geom = comp.geom().create("geom1", 2)
    geom.lengthUnit("nm")
    W, y0, y1, y2, y3 = 250.0, 0.0, 500.0, 1300.0, 1800.0
    for tag, a, b in [("rb", y0, y1), ("rp", y1, y2), ("rt", y2, y3)]:
        rct = geom.create(tag, "Rectangle")
        rct.set("size", [f"{W:g}", f"{b - a:g}"])
        rct.set("pos", ["0", f"{a:g}"])
    geom.run()
    log("  지오메트리 OK: 공기 500 / MAPbI3 800 / 공기 500 (nm), 주기 폭 250nm")

    def box(tag, edim, x0, x1, ylo, yhi):
        s = j.selection().create(tag, "Box")
        s.set("entitydim", str(edim))
        s.set("xmin", f"{x0:g}[nm]")
        s.set("xmax", f"{x1:g}[nm]")
        s.set("ymin", f"{ylo:g}[nm]")
        s.set("ymax", f"{yhi:g}[nm]")
        s.set("condition", "inside")
    eps = 0.5
    box("d_pvk", 2, -eps, W + eps, y1 - eps, y2 + eps)
    box("b_top", 1, -eps, W + eps, y3 - eps, y3 + eps)
    box("b_bot", 1, -eps, W + eps, y0 - eps, y0 + eps)
    box("b_l", 1, -eps, eps, y0 - eps, y3 + eps)
    box("b_r", 1, W - eps, W + eps, y0 - eps, y3 + eps)
    uni = j.selection().create("b_lr", "Union")
    uni.set("entitydim", "1")  # 경계 레벨 Union (2026-07-08 확정: 기본은 도메인 레벨)
    uni.set("input", ["b_l", "b_r"])

    ewfd = comp.physics().create("ewfd", "ElectromagneticWavesFrequencyDomain", "geom1")
    wee1 = j.physics("ewfd").feature("wee1")
    wee1.set("DisplacementFieldModel", "RefractiveIndex")
    for pn, pv in [("n_mat", "userdef"), ("n", ["1"]), ("ki_mat", "userdef"), ("ki", ["0"])]:
        wee1.set(pn, pv)
    wee2 = ewfd.create("wee2", "WaveEquationElectric", 2)
    wee2.selection().named("d_pvk")
    wee2.set("DisplacementFieldModel", "RefractiveIndex")
    for pn, pv in [("n_mat", "userdef"), ("n", [f"{n2r:.6f}"]),
                   ("ki_mat", "userdef"), ("ki", [f"{k2:.6f}"])]:
        wee2.set(pn, pv)
    log("  파동방정식 OK: wee1=공기(전역), wee2=MAPbI3 (n/ki userdef — O-1 확정 패턴)")

    # 생성 ID는 "Port"/"PeriodicCondition" — 속성으로 Periodic/Floquet 지정 (2026-07-08 확정)
    pp1 = ewfd.create("pport1", "Port", 1)
    pp1.selection().named("b_top")
    pp1.set("PortType", "Periodic")
    _try_set(pp1, [("PortExcitation", "on")], log, "pport1")
    pp2 = ewfd.create("pport2", "Port", 1)
    pp2.selection().named("b_bot")
    pp2.set("PortType", "Periodic")
    fpc = ewfd.create("fpc1", "PeriodicCondition", 1)
    fpc.selection().named("b_lr")
    _try_set(fpc, [("PeriodicType", "Floquet")], log, "fpc1")
    _try_set(fpc, [("Floquet_source", "FromPeriodicPort")], log, "fpc1")
    log("  포트/Floquet OK: 상부 입사(pport1 excitation), 하부 수음(pport2), 양측 Floquet")

    msh = j.mesh().create("mesh1", "geom1")
    hmax = lam_nm / (6.0 * max(n2r, 1.0))
    try:
        msh.feature("size").set("custom", "on")
        msh.feature("size").set("hmax", f"{hmax:.1f}[nm]")
        log(f"  메시 크기: hmax={hmax:.1f}nm (λ/6n)")
    except Exception as e:
        log(f"  메시 size 설정 실패({type(e).__name__}) — 기본 크기 사용 [확인 대상]")
    try:
        msh.create("ftri1", "FreeTri")
    except Exception:
        pass

    std1 = j.study().create("std1")
    fr = std1.create("freq", "Frequency")
    f_hz = 299792458.0 / lam
    ok = _try_set(fr, [("plist", [f_hz])], log, "freq.plist")
    _try_set(fr, [("punit", "Hz"), ("punit", ["Hz"])], log, "freq.punit")
    log(f"  주파수 스터디: f={f_hz:.4e} Hz (λ={lam_nm}nm) plist설정={'OK' if ok else '실패'}")
    jd = jobs.job_dir(jid)
    model.save(str(jd / "spike_wo2_unsolved.mph"))
    import time
    t0 = time.time()
    model.solve("Study 1")
    log(f"솔브 OK ({time.time() - t0:.1f}s)")
    res = {}
    for expr in ["ewfd.Rtotal", "ewfd.Ttotal", "ewfd.Atotal"]:
        try:
            v = float(np.ravel(model.evaluate(expr))[0])
            res[expr.split(".")[1]] = v
            log(f"  {expr} = {v:.4f}")
        except Exception as e:
            log(f"  {expr} 평가 실패: {type(e).__name__} {str(e)[:100]}")
    model.save(str(jd / "spike_wo2_solved.mph"))
    if res:
        dR = abs(res.get("Rtotal", 9) - R_t)
        dA = abs(res.get("Atotal", 9) - A_t)
        verdict = "통과" if (dR < 0.03 and dA < 0.03) else "실패"
        log(f"\n[검증] TMM 대조: ΔR={dR:.4f}, ΔA={dA:.4f} → {verdict} (기준 <0.03)")
    log(">> WO-2 완료 — Claude가 결과를 분석합니다 <<")


def _summary(log, results):
    log("\n===== 요약 =====")
    for name, r in results:
        log(f"  {r:4s}  {name}")
    log(">> 이 로그 전체를 '로그 복사' 버튼으로 복사해 Claude에게 붙여넣어 주세요 <<")
