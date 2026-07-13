"""평판 파동광학 → G(depth) 프로파일 계산 (v3 O-4, SPEC_OPTICS_IDE.md).

IDE/IBC v0의 광학: 상부 조사면이 평탄(전극은 하부)하므로 광학은 1D 평판 문제 —
G는 깊이만의 함수. (Yang 2020 QIBC와 동일 접근. 하부 금속 반사는 v1.)

확정 API만 사용 (2026-07-08 스파이크 WO-1~3):
- ewfd + WaveEquationElectric(n_mat/ki_mat userdef), Port(PortType=Periodic),
  PeriodicCondition(PeriodicType=Floquet), Frequency 스터디(punit "Hz" 필수!), ewfd.Qh.
정규화: 각 λ에서 ∫G_λ dA = A(λ)·Φ_AM1.5(λ)·W (A=ewfd.Atotal).
"""
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
H_C = 6.62607015e-34 * 299792458.0
Q_E = 1.602176634e-19
_TRAPEZOID = getattr(np, "trapezoid", None)
if _TRAPEZOID is None:  # NumPy 1.x 서버 venv 호환
    _TRAPEZOID = np.trapz


def beer_lambert_jsc(t_abs_nm, lam0=300.0, lam1=850.0):
    """주입한 Beer-Lambert 생성률의 광전류 상한 [mA/cm²].

    별도 상수를 두지 않고 앱이 실제 사용하는 AM1.5 및 MAPbI3 k 데이터의 원래
    파장 격자에서 적분한다. 3D/검토 재개 경로의 수집효율 분모로 사용한다.
    """
    from . import data_prep
    if t_abs_nm <= 0:
        raise ValueError("흡수층 두께는 0보다 커야 합니다")
    nk = np.loadtxt(DATA / data_prep.dataset("mapbi3_nk")["file"], encoding="utf-8")
    am = np.loadtxt(DATA / data_prep.dataset("am15")["file"], encoding="utf-8")
    inside = (am[:, 0] > lam0) & (am[:, 0] < lam1)
    wl_nm = np.unique(np.concatenate(([lam0], am[inside, 0], [lam1])))
    flux = np.interp(wl_nm, am[:, 0], am[:, 1])
    k = np.interp(wl_nm / 1000.0, nk[:, 0], nk[:, 2])
    alpha = 4.0 * np.pi * k / (wl_nm * 1e-9)
    photon_flux = flux * (wl_nm * 1e-9) / H_C
    absorbed = (1.0 - np.exp(-alpha * t_abs_nm * 1e-9)) * photon_flux
    return float(Q_E * _TRAPEZOID(absorbed, wl_nm) * 0.1)


def _normalize_generation_profile(depth_nm, generation, target_jsc):
    """선형 보간 G(depth)의 적분을 target_jsc와 일치시킨다."""
    depth_nm = np.asarray(depth_nm, dtype=float)
    generation = np.asarray(generation, dtype=float)
    if (depth_nm.ndim != 1 or generation.ndim != 1
            or depth_nm.size != generation.size or depth_nm.size < 2):
        raise ValueError("G(depth) 정규화 입력의 크기가 올바르지 않습니다")
    if not np.all(np.isfinite(depth_nm)) or not np.all(np.isfinite(generation)):
        raise ValueError("G(depth) 정규화 입력에 비유한값이 있습니다")
    injected = Q_E * _TRAPEZOID(generation, depth_nm * 1e-9) * 0.1
    if not np.isfinite(injected) or injected <= 0 or target_jsc <= 0:
        raise RuntimeError("G(depth) 또는 목표 광전류 적분값이 유효하지 않습니다")
    scaled = generation * (target_jsc / injected)
    check = Q_E * _TRAPEZOID(scaled, depth_nm * 1e-9) * 0.1
    return scaled, float(check)


def compute_G_profile(client, t_abs_nm, log, nlam=15, nbins=80,
                      lam0=300.0, lam1=850.0):
    """공기/흡수층(t_abs)/공기 슬랩 λ 스윕 → dict(depth_nm, G, jsc_wave, jsc_bl, A_list)."""
    from . import data_prep
    from .stack_builder import _try_set
    nk = np.loadtxt(DATA / data_prep.dataset("mapbi3_nk")["file"], encoding="utf-8")
    am = np.loadtxt(DATA / data_prep.dataset("am15")["file"], encoding="utf-8")
    if nlam < 2:
        raise ValueError("광학 파장 샘플 수 nlam은 2 이상이어야 합니다")
    if nbins < 2:
        raise ValueError("광생성 깊이 bin 수 nbins는 2 이상이어야 합니다")
    lams = np.linspace(lam0, lam1, nlam)
    dlam = (lam1 - lam0) / (nlam - 1)
    # 균일 격자 사다리꼴 가중치. 기존에는 양 끝점에도 dlam 전부를 곱해
    # nlam 의존 과대 적분이 생겼다.
    lam_weights = np.full(nlam, dlam)
    lam_weights[[0, -1]] *= 0.5
    W, pad = 250.0, 500.0
    y1, y2 = pad, pad + t_abs_nm
    y3 = y2 + pad
    log(f"  [광학] 평판 파동광학: t_abs={t_abs_nm:g}nm, λ {nlam}점({lam0:g}-{lam1:g}nm)")

    model = client.create("wo_planar")
    j = model.java
    try:
        j.component().create("comp1", True)
    except Exception:
        j.modelNode().create("comp1")
    comp = j.component("comp1")
    geom = comp.geom().create("geom1", 2)
    geom.lengthUnit("nm")
    for tag, a, b in [("rb", 0.0, y1), ("rp", y1, y2), ("rt", y2, y3)]:
        rct = geom.create(tag, "Rectangle")
        rct.set("size", [f"{W:g}", f"{b - a:g}"])
        rct.set("pos", ["0", f"{a:g}"])
    geom.run()

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
    box("b_bot", 1, -eps, W + eps, -eps, eps)
    box("b_l", 1, -eps, eps, -eps, y3 + eps)
    box("b_r", 1, W - eps, W + eps, -eps, y3 + eps)
    uni = j.selection().create("b_lr", "Union")
    uni.set("entitydim", "1")
    uni.set("input", ["b_l", "b_r"])

    ewfd = comp.physics().create("ewfd", "ElectromagneticWavesFrequencyDomain", "geom1")
    wee1 = j.physics("ewfd").feature("wee1")
    wee1.set("DisplacementFieldModel", "RefractiveIndex")
    for pn, pv in [("n_mat", "userdef"), ("n", ["1"]), ("ki_mat", "userdef"), ("ki", ["0"])]:
        wee1.set(pn, pv)
    wee2 = ewfd.create("wee2", "WaveEquationElectric", 2)
    wee2.selection().named("d_pvk")
    wee2.set("DisplacementFieldModel", "RefractiveIndex")
    wee2.set("n_mat", "userdef")
    wee2.set("ki_mat", "userdef")
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
    msh = j.mesh().create("mesh1", "geom1")
    try:
        msh.feature("size").set("custom", "on")
        msh.feature("size").set("hmax", "20[nm]")
    except Exception:
        pass
    std1 = j.study().create("std1")
    fr = std1.create("freq", "Frequency")
    _try_set(fr, [("punit", "Hz"), ("punit", ["Hz"])], log, "freq.punit")  # 필수!

    X = Y = None
    Gsum = None
    jsc_wave = 0.0
    jsc_bl = 0.0
    A_list = []
    t_abs_m = t_abs_nm * 1e-9
    for li, lam_nm in enumerate(lams, 1):
        n_l = float(np.interp(lam_nm / 1000, nk[:, 0], nk[:, 1]))
        k_l = float(np.interp(lam_nm / 1000, nk[:, 0], nk[:, 2]))
        F_l = float(np.interp(lam_nm, am[:, 0], am[:, 1]))
        wee2.set("n", [f"{n_l:.6f}"])
        wee2.set("ki", [f"{k_l:.6f}"])
        fr.set("plist", [299792458.0 / (lam_nm * 1e-9)])
        t0 = time.time()
        model.solve("Study 1")
        A = float(np.ravel(model.evaluate("ewfd.Atotal"))[0])
        if X is None:
            X = np.ravel(model.evaluate("x"))
            Y = np.ravel(model.evaluate("y"))
        Qh = np.ravel(model.evaluate("ewfd.Qh"))
        m = min(X.size, Y.size, Qh.size)
        import matplotlib.tri as mtri
        tri = mtri.Triangulation(X[:m] * 1e-9, Y[:m] * 1e-9)
        xy = np.column_stack([tri.x, tri.y])
        t = tri.triangles
        v1 = xy[t[:, 1]] - xy[t[:, 0]]
        v2 = xy[t[:, 2]] - xy[t[:, 0]]
        areas = 0.5 * np.abs(v1[:, 0] * v2[:, 1] - v1[:, 1] * v2[:, 0])
        integ = float(np.sum(areas * Qh[:m][t].mean(axis=1)))
        Phi = F_l * lam_weights[li - 1] * (lam_nm * 1e-9) / H_C
        c_l = (A * Phi * (W * 1e-9) / integ) if integ > 0 else 0.0
        if Gsum is None:
            Gsum = np.zeros(m)
        Gsum += Qh[:m] * c_l
        jsc_wave += Q_E * A * Phi * 0.1
        alpha = 4 * np.pi * k_l / (lam_nm * 1e-9)
        jsc_bl += Q_E * (1 - np.exp(-alpha * t_abs_m)) * Phi * 0.1
        A_list.append((float(lam_nm), A))
        log(f"    [{li}/{nlam}] λ={lam_nm:.0f}nm A={A:.4f} ({time.time() - t0:.1f}s)")
    client.remove(model)

    # 깊이(조사면 기준) 프로파일로 축약 — 흡수층 내부만
    depth = y2 - Y[:Gsum.size]          # nm, 0=조사면(흡수층 상단)
    inside = (depth >= 0) & (depth <= t_abs_nm)
    edges = np.linspace(0, t_abs_nm, nbins + 1)
    centers = edges[:-1] + np.diff(edges) / 2
    prof = np.empty(nbins)
    for i in range(nbins):
        sel = inside & (depth >= edges[i]) & (depth < edges[i + 1])
        prof[i] = Gsum[sel].mean() if np.any(sel) else np.nan
    ok = ~np.isnan(prof)
    if np.count_nonzero(ok) < 2:
        raise RuntimeError("파동광학 G(depth) bin을 구성할 유효 표본이 부족합니다")
    prof = np.interp(centers, centers[ok], prof[ok])  # 빈 bin 보간

    # 노드 단순평균→깊이 bin 축약은 원래 2D 적분을 보존하지 않는다. COMSOL에
    # 넘기는 선형 보간표 자체의 적분이 광학 흡수 전류(jsc_wave)와 같도록 보정한다.
    # 양 끝점을 명시해 Interpolation의 기본 외삽 방식에 결과가 좌우되지 않게 한다.
    depth_out = np.concatenate(([0.0], centers, [float(t_abs_nm)]))
    prof_out = np.concatenate(([prof[0]], prof, [prof[-1]]))
    prof_out, jsc_injected = _normalize_generation_profile(
        depth_out, prof_out, jsc_wave)
    log(f"  [광학] 완료: Jsc,opt(파동)={jsc_wave:.2f} vs Beer-Lambert={jsc_bl:.2f} mA/cm² "
        f"(BL은 반사 무시로 과대, 주입 G 적분={jsc_injected:.2f})")
    return {"depth_nm": depth_out, "G": prof_out, "jsc_wave": jsc_wave,
            "jsc_injected": jsc_injected,
            "jsc_bl": jsc_bl, "A_list": A_list, "nlam": nlam}
