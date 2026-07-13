"""Solcore 엔진 — 파이썬 in-process (외부 프로그램 불필요, pip install solcore).

v0 케이스 2종:
1) solcore_tmm_stack : 공기/MAPbI3(t)/공기 TMM — R/A/T 스펙트럼 + AM1.5 가중 G(depth)
   + Jsc,opt. COMSOL 파동광학(v3, wo_optics)과 '같은 문제'라 직접 대조 검증 가능
   (기준: t=800nm에서 A(550)=0.8216. Jsc,opt는 같은 파장 격자끼리 대조).
2) solcore_sq_limit  : Shockley-Queisser 상한 (AM1.5 데이터 + 300K 흑체 복사 —
   자체 수식, 임의 파라미터 없음). Eg 스윕 곡선 + 대상 Eg 마커.

검증 이력(2026-07-08, 샌드박스 solcore 5.10.1): 아래 solcore_tmm이 COMSOL O-2/O-3와
대조됨 — 결과는 케이스 문서(cases/solcore_tmm_stack/CASE_SPEC.md)에 기록.
"""
import numpy as np

from .. import jobs
from . import common

H = 6.62607015e-34
C = 299792458.0
Q = 1.602176634e-19
KB = 1.380649e-23


def _tmm_modules():
    """solcore의 TMM 코어 import (버전에 따라 경로가 달라 후보 순차)."""
    try:
        from solcore.absorption_calculator import tmm_core_vec as tc  # 5.9+
        return tc
    except ImportError:
        from solcore.absorption_calculator import tmm_core as tc  # 구버전
        return tc


def _slab_rat_profile(t_abs_nm, lams_nm, nk, nz=200):
    """공기/흡수층/공기 TMM: λ별 R/A/T + 깊이별 흡수밀도 a(z,λ) [1/nm].
    ∫a dz = A(λ) 가 되도록 정규화된 국소 흡수율.
    확정(2026-07-08 샌드박스 검증): tmm_core_vec은 '파장 벡터화' — n_list는 (층, λ)
    배열, lam_vac은 배열이어야 함 (스칼라 호출은 numpy.bool 오류). fn.run(z)는
    (λ, z) 형상으로 반환 → 전치해서 (z, λ)로 사용."""
    tc = _tmm_modules()
    lams = np.asarray(lams_nm, dtype=float)
    n_i = np.interp(lams / 1000.0, nk[:, 0], nk[:, 1])
    k_i = np.interp(lams / 1000.0, nk[:, 0], nk[:, 2])
    ones = np.ones_like(lams)
    res = tc.coh_tmm("s", np.array([ones, n_i + 1j * k_i, ones]),
                     [np.inf, t_abs_nm, np.inf], 0.0, lams)
    R = np.asarray(res["R"], dtype=float)
    T = np.asarray(res["T"], dtype=float)
    A = 1.0 - R - T
    zs = np.linspace(0.0, t_abs_nm, nz)  # 흡수층 내부 깊이 (윗면=입사면에서)
    fn = tc.absorp_analytic_fn().fill_in(res, 1)  # layer 1 해석적 흡수 함수
    a_zl = np.real(fn.run(zs)).T  # (λ,z) → (z,λ), [1/nm]
    return zs, R, A, T, a_zl


def _g_from_profile(zs, a_zl, lams_nm, am15):
    """AM1.5 가중 G(z) [1/(m^3 s)] + Jsc,opt [mA/cm^2] (wo_optics와 동일 정의)."""
    if len(lams_nm) < 2:
        raise ValueError("광학 파장 샘플 수는 2 이상이어야 합니다")
    F = np.interp(lams_nm, am15[:, 0], am15[:, 1])           # W/m^2/nm
    phi = F * (lams_nm * 1e-9) / (H * C)                     # photons/(m^2 s nm)
    G = np.trapezoid(a_zl * phi[np.newaxis, :], lams_nm, axis=1) * 1e9
    A_int = np.trapezoid(a_zl, zs, axis=0)                    # = A(λ)
    jsc = Q * np.trapezoid(A_int * phi, lams_nm) * 0.1        # A/m² → mA/cm²
    return G, jsc


def _run_tmm_stack(jid, params, log):
    jd = jobs.job_dir(jid)
    t_list = [float(x) for x in str(params.get("t_abs_list_nm", "800")).replace(" ", "").split(",") if x]
    nlam = int(float(params.get("nlam", 111)))
    lam0, lam1 = 300.0, 850.0
    lams = np.linspace(lam0, lam1, nlam)
    nk = common.load_nk("mapbi3_nk")
    am15 = common.load_am15()
    log(f"Solcore TMM 슬랩: t {t_list} nm, λ {nlam}점 ({lam0:g}-{lam1:g}nm), 수직입사 TE "
        f"(COMSOL 파동광학 v3와 동일 구성 — 대조 가능)")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig1, ax1 = plt.subplots(figsize=(6.4, 4.2), dpi=110)
    fig2, ax2 = plt.subplots(figsize=(6.4, 4.2), dpi=110)
    rows = ["t_nm,lambda_nm,R,A,T"]
    grow = ["t_nm,depth_nm,G_m3s"]
    jsc_list = []
    for t in t_list:
        jobs.check_cancel(jid)
        zs, R, A, T, a_zl = _slab_rat_profile(t, lams, nk)
        G, jsc = _g_from_profile(zs, a_zl, lams, am15)
        jsc_list.append(jsc)
        a550 = float(np.interp(550.0, lams, A))
        log(f"  t={t:g}nm: A(550)={a550:.4f}, Jsc,opt={jsc:.2f} mA/cm²")
        if abs(t - 800.0) < 1e-9:
            log("    [대조] COMSOL 파동광학 기준 A(550)=0.8216 — "
                f"이번 값 {a550:.4f}; Jsc,opt={jsc:.2f}는 같은 λ 격자 결과와 비교")
        ax1.plot(lams, A, lw=1.3, label=f"A t={t:g}nm")
        ax2.plot(zs, G, lw=1.3, label=f"t={t:g}nm")
        for lam, r, a, tt in zip(lams, R, A, T):
            rows.append(f"{t:g},{lam:.1f},{r:.5f},{a:.5f},{tt:.5f}")
        for z, g in zip(zs, G):
            grow.append(f"{t:g},{z:.2f},{g:.5e}")
    if len(t_list) == 1:
        zs, R, A, T, _ = _slab_rat_profile(t_list[0], lams, nk)
        ax1.plot(lams, R, "--", lw=1.0, label="R")
        ax1.plot(lams, T, ":", lw=1.0, label="T")
    ax1.set_xlabel("wavelength [nm]")
    ax1.set_ylabel("R / A / T")
    ax1.set_title("Solcore TMM: air / MAPbI3 / air")
    ax1.grid(alpha=0.3)
    ax1.legend(fontsize=8)
    fig1.tight_layout()
    fig1.savefig(jd / "rat_spectrum.png")
    ax2.set_xlabel("depth from top [nm]")
    ax2.set_ylabel("G [1/(m$^3$s)]")
    ax2.set_title("AM1.5-weighted generation profile (TMM)")
    ax2.grid(alpha=0.3)
    ax2.legend(fontsize=8)
    fig2.tight_layout()
    fig2.savefig(jd / "g_profile_tmm.png")
    plt.close("all")
    (jd / "rat_spectrum.csv").write_text("\n".join(rows), encoding="utf-8")
    (jd / "g_profile_tmm.csv").write_text("\n".join(grow), encoding="utf-8")
    if len(t_list) > 1:
        fig3, ax3 = plt.subplots(figsize=(5.6, 3.8), dpi=110)
        ax3.plot(t_list, jsc_list, "o-")
        ax3.set_xlabel("t_abs [nm]")
        ax3.set_ylabel("Jsc,opt [mA/cm$^2$]")
        ax3.grid(alpha=0.3)
        fig3.tight_layout()
        fig3.savefig(jd / "jsc_vs_thickness.png")
        plt.close(fig3)
    log(f"완료: Jsc,opt = {', '.join(f'{j:.2f}' for j in jsc_list)} mA/cm² "
        "(전기 손실 없는 광학 상한 — COMSOL 소자 Jsc의 상한선)")


def _run_sq_limit(jid, params, log):
    """Shockley-Queisser 상한: Jsc=q∫Φ(E>Eg), J0=q∫흑체(300K, E>Eg), 1-diode 최대전력.
    자체 수식(복사 재결합 한계)이라 외부 프로그램·임의값 없음."""
    jd = jobs.job_dir(jid)
    eg_target = float(params.get("eg_ev", 1.55))
    T = float(params.get("temp_k", 300.0))
    am15 = common.load_am15()
    lam = am15[:, 0] * 1e-9
    F = am15[:, 1] * 1e9                    # W/m^2/m
    E = H * C / lam                          # J
    phi = F / E                              # photons/(m^2 s m)
    pin = np.trapezoid(F, lam)               # W/m^2
    egs = np.linspace(0.8, 2.4, 161)
    out = []
    for eg in egs:
        egj = eg * Q
        m = E >= egj
        jsc = Q * np.trapezoid(phi[m], lam[m])                       # A/m^2 (역순 적분 부호 주의)
        jsc = abs(jsc)
        # 300K 흑체 (Planck, 광자 플럭스, E>Eg) — 수치 적분
        Ee = np.linspace(egj, 40 * KB * T + egj, 4000)
        bb = 2 * np.pi / (H**3 * C**2) * Ee**2 / (np.exp(Ee / (KB * T)) - 1.0)
        j0 = Q * np.trapezoid(bb, Ee)                                # A/m^2
        v = np.linspace(0, eg, 800)
        jv = jsc - j0 * (np.exp(Q * v / (KB * T)) - 1.0)
        p = jv * v
        eta = 100.0 * np.max(p) / pin
        out.append((eg, jsc * 0.1, eta))
    arr = np.array(out)
    eta_t = float(np.interp(eg_target, arr[:, 0], arr[:, 2]))
    jsc_t = float(np.interp(eg_target, arr[:, 0], arr[:, 1]))
    log(f"SQ 한계 (AM1.5 데이터 기반, T={T:g}K): Eg={eg_target:g}eV → "
        f"Jsc,max={jsc_t:.2f} mA/cm², η,max={eta_t:.2f}%")
    log(f"  전 범위 최적: Eg={arr[np.argmax(arr[:,2]),0]:.2f}eV, η={arr[:,2].max():.2f}%")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(6.2, 4.2), dpi=110)
    ax.plot(arr[:, 0], arr[:, 2], lw=1.5)
    ax.axvline(eg_target, color="crimson", ls="--", lw=1.0,
               label=f"Eg={eg_target:g}eV → {eta_t:.1f}%")
    ax.set_xlabel("bandgap [eV]")
    ax.set_ylabel("SQ efficiency limit [%]")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(jd / "sq_limit.png")
    plt.close(fig)
    np.savetxt(jd / "sq_limit.csv", arr, delimiter=",", header="Eg_eV,Jsc_mA_cm2,eta_pct",
               comments="", fmt="%.5g")


def check(jid, params, log):
    """환경 점검: 패키지 임포트 + 초소형 TMM 1점 + 데이터 파일. 동글·외부 프로그램 불필요."""
    try:
        import solcore
        log(f"solcore {solcore.__version__} 임포트 OK")
    except ImportError:
        raise RuntimeError("solcore 미설치 — install_engines.bat 실행 (인터넷 필요, 1~2분)")
    tc = _tmm_modules()
    lam = np.array([550.0])
    one = np.ones(1)
    res = tc.coh_tmm("s", np.array([one, one * (2.0 + 0.1j), one]),
                     [np.inf, 500.0, np.inf], 0.0, lam)
    s = float(np.asarray(res["R"])[0] + np.asarray(res["T"])[0])
    assert 0 < s < 1, "TMM 자기검사 실패"
    log(f"TMM 코어 자기검사 OK (R+T={s:.4f} < 1, 나머지=흡수)")
    for did in ("am15", "mapbi3_nk"):
        try:
            arr = common.load_nk(did) if did == "mapbi3_nk" else common.load_am15()
            log(f"데이터셋 {did}: {len(arr)}행 OK")
        except Exception as e:
            log(f"⚠️ 데이터셋 {did} 미준비({type(e).__name__}) — ③ 데이터 준비 탭 확인")
    log("Solcore 점검 완료 — 케이스 실행 가능 (동글 불필요)")


def run(jid, params, log, case):
    try:
        import solcore  # noqa: F401
    except ImportError:
        raise RuntimeError("solcore가 설치돼 있지 않습니다 — 프로젝트 폴더의 install_engines.bat을 "
                           "실행하거나 .venv\\Scripts\\pip install solcore 후 다시 실행하세요")
    kind = case.get("solcore_kind") or params.get("solcore_kind") or "tmm_stack"
    if kind == "tmm_stack":
        return _run_tmm_stack(jid, params, log)
    if kind == "sq_limit":
        return _run_sq_limit(jid, params, log)
    raise RuntimeError(f"알 수 없는 solcore 케이스 종류: {kind}")
