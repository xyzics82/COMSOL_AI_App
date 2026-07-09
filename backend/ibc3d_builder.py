"""IBC 3D 단위 셀 빌더 — 핑거 끝단(팁) 효과 포함 (v4).

좌표계: x=가로(n전극 중앙→p전극 중앙, L=W+gap), y=수직(H=SnO2두께+t_abs),
        z=핑거 길이 방향 (0=핑거 중앙 대칭면, Lz=단위 셀 끝 대칭면).
z ∈ [0, z_tip]: 핑거 구간(CTL·접점 존재) / z ∈ [z_tip, Lz]: 팁 너머 갭(흡수층만).

v0 정직한 경계:
- Jsc 전용(V0=0 단일점) 권장 — 3D 풀 J-V는 계산량 과대, 신뢰 지표는 어차피 Jsc.
- 광생성: v3 파동광학 G(depth) 재사용(윗면 평탄) 또는 Beer-Lambert. UDG는 전 도메인
  적용 — CTL은 깊이 800nm+ 구간이라 G≈0, 오차 미미(사양서 기록).
- 금속 저항·버스바 없음(이상 옴익 접점) — 핑거 방향 전류 밀집(crowding)은 v1.

검증 기준: z_tip=Lz(완전 압출)에서 '같은 메시 설정'의 2D IBC와 Jsc 일치.
(2026-07-08: 기본 메시끼리 2.4% / hmax 120nm·S=1000·IDL 포함 3D 18.90 vs 2D 19.01 = 0.6%)
절대값은 메시 수렴 필요 — 기본(자동) 메시는 Jsc −24% 과소 (2D CASE_SPEC 6.9절).
IDL(계면 재결합, 2nm 층) 포함 시 기본 메시는 솔브 실패 → hmax 자동 120nm 안전망.
확정 API 재사용: Box 선택(entitydim str), 기본 smm1=흡수층 트릭, Sweep 메시(3D 평판 확정).
"""
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

T_SNO2 = 20.0
T_NIOX = 10.0
from .ibc_builder import T_IDL  # 계면 결함층 두께 (2D와 동일 값 공유)


def _try_set(node, pairs, log, label):
    for prop, val in pairs:
        try:
            node.set(prop, val)
            return True
        except Exception as e:
            log(f"    set {label}.{prop}: {type(e).__name__} (다음 후보)")
    return False


def _box3(j, tag, edim, lo, hi):
    s = j.selection().create(tag, "Box")
    s.set("entitydim", str(edim))
    for k, v in zip(("xmin", "ymin", "zmin"), lo):
        s.set(k, f"{v:g}[nm]")
    for k, v in zip(("xmax", "ymax", "zmax"), hi):
        s.set(k, f"{v:g}[nm]")
    s.set("condition", "inside")
    return s


def build(client, name, mats, w_nm, gap_nm, t_abs_nm, taun, lz_nm, ztip_nm, log,
          g_profile=None, v0_only=True, vcfg=None, s_ifc_cms=0.0, mesh_hmax_nm=None,
          n_pairs=0):
    """반환: (model, area_cm2).
    n_pairs=0(기본): 주기 대칭 단위셀(half-n + gap + half-p) — 기존 검증 경로.
    n_pairs>=1   : 유한 깍지 배열 — n·p 핑거 각 n_pairs개를 실제로 배치 (2026-07-09
                   사용자 요청). 폭 = 2·n_pairs·(W+gap), 가장자리 여백 gap/2.
                   계산량 ≈ 단위셀 × 2·n_pairs."""
    if n_pairs and int(n_pairs) >= 1:
        return _build_array(client, name, mats, w_nm, gap_nm, t_abs_nm, taun,
                            lz_nm, ztip_nm, log, g_profile, v0_only, vcfg,
                            s_ifc_cms, mesh_hmax_nm, int(n_pairs))
    L = w_nm + gap_nm
    hw = w_nm / 2.0
    H = T_SNO2 + t_abs_nm
    ztip_nm = min(ztip_nm, lz_nm)
    eps = 0.5
    model = client.create(name)
    j = model.java
    try:
        j.component().create("comp1", True)
    except Exception:
        j.modelNode().create("comp1")
    comp = j.component("comp1")
    geom = comp.geom().create("geom1", 3)
    geom.lengthUnit("nm")
    j.param().set("V0", "0[V]")

    def blk(tag, lo, hi):
        b = geom.create(tag, "Block")
        b.set("size", [f"{hi[0] - lo[0]:g}", f"{hi[1] - lo[1]:g}", f"{hi[2] - lo[2]:g}"])
        b.set("pos", [f"{lo[0]:g}", f"{lo[1]:g}", f"{lo[2]:g}"])
    blk("b_abs", (0, 0, 0), (L, H, lz_nm))                     # 기본 흡수층 블록
    blk("b_sno2", (0, 0, 0), (hw, T_SNO2, ztip_nm))            # n쪽 CTL 핑거
    blk("b_niox", (L - hw, 0, 0), (L, T_NIOX, ztip_nm))        # p쪽 CTL 핑거
    # IDL(계면 결함층): CTL 상면 위 얇은 흡수층 블록 — τ=d/S 등가 (#29, 2D와 동일 방식)
    t_idl = T_IDL if (s_ifc_cms and s_ifc_cms > 0) else 0.0
    if t_idl > 0:
        blk("b_idl_n", (0, T_SNO2, 0), (hw, T_SNO2 + t_idl, ztip_nm))
        blk("b_idl_p", (L - hw, T_NIOX, 0), (L, T_NIOX + t_idl, ztip_nm))
        if ztip_nm < lz_nm:
            # 끝단 모드 필수 (2026-07-09 서버 100조합 전멸 교훈): 2nm 밴드가 z=z_tip에서
            # 계단으로 끝나면 Swept 메시가 실패 ("Swept 1" + multiphysics compilation).
            # 해결: 밴드를 z 전 구간으로 연장하되 z_tip에서 두 조각으로 분할 — 단면
            # 토폴로지가 z-균일해져 스윕 성립. 팁 너머 조각은 tar_idl 선택(z≤z_tip)에서
            # 제외되어 일반 흡수층 물성 그대로 → 물리 불변 (도메인 분할만 추가).
            blk("b_idl_n2", (0, T_SNO2, ztip_nm), (hw, T_SNO2 + t_idl, lz_nm))
            blk("b_idl_p2", (L - hw, T_NIOX, ztip_nm), (L, T_NIOX + t_idl, lz_nm))
    geom.run()
    log(f"  지오메트리 OK (IBC 3D 단위셀): W={w_nm/1000:g}um gap={gap_nm/1000:g}um "
        f"Lz={lz_nm/1000:g}um z_tip={ztip_nm/1000:g}um t_abs={t_abs_nm:g}nm")

    _box3(j, "d_sno2", 3, (-eps, -eps, -eps), (hw + eps, T_SNO2 + eps, ztip_nm + eps))
    _box3(j, "d_niox", 3, (L - hw - eps, -eps, -eps), (L + eps, T_NIOX + eps, ztip_nm + eps))
    _box3(j, "b_n", 2, (-eps, -eps, -eps), (hw + eps, eps, ztip_nm + eps))       # SnO2 바닥면
    _box3(j, "b_p", 2, (L - hw - eps, -eps, -eps), (L + eps, eps, ztip_nm + eps))  # NiOx 바닥면
    # IDL 도메인 선택 (계면 재결합 v1 — 경계 feature는 내부 경계 비활성, ibc_builder 6.8 참조)
    # 팁 끝면(z=z_tip 수직면)은 면적 미미해 생략 — 사양 기록
    if t_idl > 0:
        _box3(j, "d_idl_n", 3, (-eps, T_SNO2 - eps, -eps),
              (hw + eps, T_SNO2 + t_idl + eps, ztip_nm + eps))
        _box3(j, "d_idl_p", 3, (L - hw - eps, T_NIOX - eps, -eps),
              (L + eps, T_NIOX + t_idl + eps, ztip_nm + eps))

    # 광생성 (전 도메인 — CTL 구간은 G≈0 깊이라 오차 미미, 헤더 참조)
    if g_profile is not None:
        f3 = j.func().create("int3", "Interpolation")
        f3.set("source", "table")
        f3.set("table", [[f"{d:.2f}", f"{g:.6e}"] for d, g in
                         zip(g_profile["depth_nm"], g_profile["G"])])
        f3.set("funcname", "Gwo")
        _try_set(f3, [("argunit", ["nm"]), ("argunit", "nm")], log, "int3")
        _try_set(f3, [("fununit", ["1/(m^3*s)"]), ("fununit", "1/(m^3*s)")], log, "int3")
        expr = f"Gwo({H:g}[nm]-y)"
        log(f"  광생성 OK: 파동광학 G(depth) 주입 (Jsc,opt={g_profile['jsc_wave']:.2f} mA/cm²)")
    else:
        from . import data_prep
        am15 = np.loadtxt(DATA / data_prep.dataset("am15")["file"], encoding="utf-8")
        f1 = j.func().create("int1", "Interpolation")
        f1.set("source", "table")
        f1.set("table", [[str(r[0]), str(r[1])] for r in am15])
        f1.set("funcname", "F")
        _try_set(f1, [("argunit", ["nm"]), ("argunit", "nm")], log, "int1")
        _try_set(f1, [("fununit", ["W/m^2/nm"]), ("fununit", "W/m^2/nm")], log, "int1")
        nk = np.loadtxt(DATA / data_prep.dataset("mapbi3_nk")["file"], encoding="utf-8")
        f2 = j.func().create("int2", "Interpolation")
        f2.set("source", "table")
        f2.set("table", [[str(r[0]), str(r[2])] for r in nk])
        f2.set("funcname", "kref")
        _try_set(f2, [("argunit", ["um"]), ("argunit", "um")], log, "int2")
        _try_set(f2, [("fununit", ["1"]), ("fununit", "1")], log, "int2")
        expr = (f"4*pi/(h_const*c_const)*integrate(kref(lm)*F(lm)*"
                f"exp(-4*pi*kref(lm)*({H:g}[nm]-y)/lm),lm,300[nm],850[nm])")
        log("  광생성 OK: Beer-Lambert")
    try:
        var = comp.variable().create("var1")
    except Exception:
        var = j.variable().create("var1")
        var.model("comp1")
    var.set("G_ph", expr)

    semi = comp.physics().create("semi", "Semiconductor", "geom1")
    area_cm2 = (L * 1e-7) * (lz_nm * 1e-7)

    def _set_props(smm, p):
        for prop, val in [("Eg0", p["Eg"]), ("chi0", p["chi"]),
                          ("Nc", p["Nc"]), ("Nv", p["Nv"]),
                          ("mun", p["mun"]), ("mup", p["mup"])]:
            smm.set(prop + "_mat", "userdef")
            smm.set(prop, val)
        smm.set("epsilonr_mat", "userdef")
        _try_set(smm, [("epsilonr", [p["epsr"]]), ("epsilonr", p["epsr"])], log, "smm.epsr")

    _set_props(j.physics("semi").feature("smm1"), mats["absorber"])  # 전역 = 흡수층
    smm_s = semi.create("smm_s", "SemiconductorMaterialModel", 3)
    smm_s.selection().named("d_sno2")
    _set_props(smm_s, mats["sno2"])
    smm_n = semi.create("smm_n", "SemiconductorMaterialModel", 3)
    smm_n.selection().named("d_niox")
    _set_props(smm_n, mats["niox"])
    adm_s = semi.create("adm_s", "AnalyticDopingModel", 3)
    adm_s.selection().named("d_sno2")
    adm_s.set("impurityType", "donor")
    adm_s.set("NDc", mats["sno2_nd"])
    adm_n = semi.create("adm_n", "AnalyticDopingModel", 3)
    adm_n.selection().named("d_niox")
    adm_n.set("impurityType", "acceptor")
    adm_n.set("NAc", mats["niox_na"])
    tar = semi.create("tar1", "TrapAssistedRecombination", 3)
    tar.selection().all()  # ⚠️ create()된 기능은 선택이 빈 채 시작 (2026-07-08 Jsc=0 교훈)
    tar.set("taun_mat", "userdef")
    tar.set("taun", taun)
    tar.set("taup_mat", "userdef")
    tar.set("taup", taun)
    udg = semi.create("udg1", "UDGeneration", 3)
    udg.selection().all()
    udg.set("Gn", "G_ph")
    udg.set("Gp", "G_ph")
    if t_idl > 0:
        from .ibc_builder import _add_idl_rec
        _add_idl_rec(semi, log, t_idl, s_ifc_cms, dim=3)
    mc1 = semi.create("mc1", "MetalContact", 2)
    mc1.selection().named("b_n")
    mc2 = semi.create("mc2", "MetalContact", 2)
    mc2.selection().named("b_p")
    mc2.set("V0", "V0")
    log("  물리 OK: 전역 smm=흡수층, CTL 국소 재정의, 접점=CTL 바닥면(핑거 구간만)")

    msh = j.mesh().create("mesh1", "geom1")
    try:
        msh.create("swp1", "Sweep")
        log("  메시: Sweep (3D 평판 검증에서 확정)")
    except Exception as e:
        log(f"  Sweep 실패({type(e).__name__}) — 기본 메시 (수렴 위험)")
    if t_idl > 0 and not mesh_hmax_nm:
        # 안전망: IDL(2nm 층) 포함 시 기본(자동) 메시는 평형 솔브가 실패함 (2026-07-08
        # 실측: 기본 실패 / hmax 120nm 성공·압출 극한 2D와 0.6% 일치) → 자동 세분
        mesh_hmax_nm = 120.0
        log("  메시 자동 세분: hmax=120nm (IDL 2nm 층 안전망 — 기본 메시는 솔브 실패)")
    if mesh_hmax_nm:  # 촘촘한 메시 (정량용 — 솔브 시간 급증, HPC 권장)
        try:
            msh.feature("size").set("custom", "on")
            msh.feature("size").set("hmax", f"{mesh_hmax_nm:g}[nm]")
            log(f"  메시 세분: hmax={mesh_hmax_nm:g}nm (HPC용 — 솔브 시간 급증 주의)")
        except Exception as e:
            log(f"  메시 세분 실패({type(e).__name__})")

    std1 = j.study().create("std1")
    std1.create("semie", "SemiconductorEquilibrium")
    std2 = j.study().create("std2")
    stat = std2.create("stat", "Stationary")
    stat.set("useparam", "on")
    stat.set("pname", ["V0"])
    if v0_only:
        stat.set("plistarr", ["0"])
        log("  스터디: Jsc 전용 (V0=0 단일점) — 3D v0 권장 모드")
    else:
        v = vcfg or {"start": 0.0, "stop": 2.0, "step": 0.05}
        stat.set("plistarr", [f"range({v['start']},{v['step']},{v['stop']})"])
    stat.set("punit", ["V"])
    _try_set(stat, [("sweeptype", "sparse")], log, "stat")
    _try_set(stat, [("pcontinuation", "V0")], log, "stat")
    for pn, pv in [("initmethod", "sol"), ("initstudy", "std1"), ("initstudystep", "semie"),
                   ("notsolmethod", "sol"), ("notstudy", "std1"), ("notstudystep", "semie")]:
        _try_set(stat, [(pn, pv)], log, "stat.init")
    return model, area_cm2


def _build_array(client, name, mats, w_nm, gap_nm, t_abs_nm, taun, lz_nm, ztip_nm, log,
                 g_profile, v0_only, vcfg, s_ifc_cms, mesh_hmax_nm, n_pairs):
    """유한 깍지 배열: n·p 핑거 각 n_pairs개 실배치 (2026-07-09).

    배치: 왼쪽부터 [gap/2][n][gap][p][gap][n]...[p][gap/2] — 핑거 2·n_pairs개 교대,
    가장자리 여백 gap/2 (총폭 = 2·n_pairs·(W+gap)). 단위셀과 달리 배열 가장자리
    효과가 포함됨 — 주기 근사 검증/실소자 비교용.
    선택 전략: 도메인은 Box Union(검증됨), 경계(접점)는 부품 Box 엔티티 직접 합산
    (경계 Union은 named 해석 0 — 2026-07-08 교훈)."""
    pitch = w_nm + gap_nm
    NF = 2 * n_pairs                       # 총 핑거 수 (짝수 idx=n, 홀수 idx=p)
    Wtot = NF * pitch                      # 총 폭 (여백 gap/2×2 포함)
    H = T_SNO2 + t_abs_nm
    ztip_nm = min(ztip_nm, lz_nm)
    eps = 0.5
    t_idl = T_IDL if (s_ifc_cms and s_ifc_cms > 0) else 0.0
    model = client.create(name)
    j = model.java
    try:
        j.component().create("comp1", True)
    except Exception:
        j.modelNode().create("comp1")
    comp = j.component("comp1")
    geom = comp.geom().create("geom1", 3)
    geom.lengthUnit("nm")
    j.param().set("V0", "0[V]")

    def blk(tag, lo, hi):
        b = geom.create(tag, "Block")
        b.set("size", [f"{hi[0] - lo[0]:g}", f"{hi[1] - lo[1]:g}", f"{hi[2] - lo[2]:g}"])
        b.set("pos", [f"{lo[0]:g}", f"{lo[1]:g}", f"{lo[2]:g}"])

    if ztip_nm < lz_nm:
        # 끝단 모드: 흡수층을 z_tip에서 두 블록으로 분할 — 전 단면에 z_tip 파티션을
        # 만들어 CTL/IDL 섬의 z-계단을 스윕 구간 경계와 정렬 (2026-07-09: IDL 밴드
        # 연장만으로는 배열 기하에서 Swept가 여전히 실패 → 전 단면 분할로 해결)
        blk("b_abs", (0, 0, 0), (Wtot, H, ztip_nm))
        blk("b_abs2", (0, 0, ztip_nm), (Wtot, H, lz_nm))
    else:
        blk("b_abs", (0, 0, 0), (Wtot, H, lz_nm))
    fingers = []  # (idx, is_n, x0, x1, t_ctl)
    for i in range(NF):
        x0 = gap_nm / 2.0 + i * pitch
        x1 = x0 + w_nm
        is_n = (i % 2 == 0)
        t_ctl = T_SNO2 if is_n else T_NIOX
        fingers.append((i, is_n, x0, x1, t_ctl))
        blk(f"bf{i}", (x0, 0, 0), (x1, t_ctl, ztip_nm))
        if t_idl > 0:
            blk(f"bf{i}i", (x0, t_ctl, 0), (x1, t_ctl + t_idl, ztip_nm))
            if ztip_nm < lz_nm:  # z-계단 금지 (2026-07-09 Swept 교훈) — 전 구간 연장
                blk(f"bf{i}i2", (x0, t_ctl, ztip_nm), (x1, t_ctl + t_idl, lz_nm))
    geom.run()
    log(f"  지오메트리 OK (IBC 3D 깍지 배열): n·p 핑거 각 {n_pairs}개 (총 {NF}), "
        f"W={w_nm/1000:g}um gap={gap_nm/1000:g}um → 총폭 {Wtot/1000:g}um / "
        f"Lz={lz_nm/1000:g}um z_tip={ztip_nm/1000:g}um")

    # 도메인 선택: 핑거별 Box → Union (도메인 Union은 named 해석 정상 — 검증됨)
    def fbox(tag, x0, x1, ylo, yhi):
        _box3(j, tag, 3, (x0 - eps, ylo - eps, -eps), (x1 + eps, yhi + eps, ztip_nm + eps))

    n_tags, p_tags, idln_tags, idlp_tags = [], [], [], []
    for i, is_n, x0, x1, t_ctl in fingers:
        fbox(f"dctl{i}", x0, x1, 0, t_ctl)
        (n_tags if is_n else p_tags).append(f"dctl{i}")
        if t_idl > 0:
            fbox(f"didl{i}", x0, x1, t_ctl, t_ctl + t_idl)
            (idln_tags if is_n else idlp_tags).append(f"didl{i}")

    def union(tag, inputs):
        u = j.selection().create(tag, "Union")
        u.set("entitydim", "3")
        u.set("input", inputs)

    union("d_sno2", n_tags)
    union("d_niox", p_tags)
    if t_idl > 0:
        union("d_idl_n", idln_tags)
        union("d_idl_p", idlp_tags)
    # 접점(경계): 핑거별 바닥 Box — 엔티티 직접 합산용
    for i, is_n, x0, x1, _t in fingers:
        _box3(j, f"bb{i}", 2, (x0 - eps, -eps, -eps), (x1 + eps, eps, ztip_nm + eps))

    def ents_of(tags, dim=2):
        out = []
        for t in tags:
            try:
                out += [int(v) for v in j.selection(t).entities(dim)]
            except Exception as e:
                log(f"    선택 {t} 엔티티 조회 실패: {type(e).__name__}")
        return sorted(set(out))

    # 광생성 함수 (단위셀과 동일 — 상면 평탄이라 G는 깊이만의 함수)
    if g_profile is not None:
        f3 = j.func().create("int3", "Interpolation")
        f3.set("source", "table")
        f3.set("table", [[f"{d:.2f}", f"{g:.6e}"] for d, g in
                         zip(g_profile["depth_nm"], g_profile["G"])])
        f3.set("funcname", "Gwo")
        _try_set(f3, [("argunit", ["nm"]), ("argunit", "nm")], log, "int3")
        _try_set(f3, [("fununit", ["1/(m^3*s)"]), ("fununit", "1/(m^3*s)")], log, "int3")
        expr = f"Gwo({H:g}[nm]-y)"
        log(f"  광생성 OK: 파동광학 G(depth) 주입 (Jsc,opt={g_profile['jsc_wave']:.2f} mA/cm²)")
    else:
        from . import data_prep
        am15 = np.loadtxt(DATA / data_prep.dataset("am15")["file"], encoding="utf-8")
        f1 = j.func().create("int1", "Interpolation")
        f1.set("source", "table")
        f1.set("table", [[str(r[0]), str(r[1])] for r in am15])
        f1.set("funcname", "F")
        _try_set(f1, [("argunit", ["nm"]), ("argunit", "nm")], log, "int1")
        _try_set(f1, [("fununit", ["W/m^2/nm"]), ("fununit", "W/m^2/nm")], log, "int1")
        nk = np.loadtxt(DATA / data_prep.dataset("mapbi3_nk")["file"], encoding="utf-8")
        f2 = j.func().create("int2", "Interpolation")
        f2.set("source", "table")
        f2.set("table", [[str(r[0]), str(r[2])] for r in nk])
        f2.set("funcname", "kref")
        _try_set(f2, [("argunit", ["um"]), ("argunit", "um")], log, "int2")
        _try_set(f2, [("fununit", ["1"]), ("fununit", "1")], log, "int2")
        expr = (f"4*pi/(h_const*c_const)*integrate(kref(lm)*F(lm)*"
                f"exp(-4*pi*kref(lm)*({H:g}[nm]-y)/lm),lm,300[nm],850[nm])")
        log("  광생성 OK: Beer-Lambert")
    try:
        var = comp.variable().create("var1")
    except Exception:
        var = j.variable().create("var1")
        var.model("comp1")
    var.set("G_ph", expr)

    semi = comp.physics().create("semi", "Semiconductor", "geom1")
    area_cm2 = (Wtot * 1e-7) * (lz_nm * 1e-7)

    def _set_props(smm, p):
        for prop, val in [("Eg0", p["Eg"]), ("chi0", p["chi"]),
                          ("Nc", p["Nc"]), ("Nv", p["Nv"]),
                          ("mun", p["mun"]), ("mup", p["mup"])]:
            smm.set(prop + "_mat", "userdef")
            smm.set(prop, val)
        smm.set("epsilonr_mat", "userdef")
        _try_set(smm, [("epsilonr", [p["epsr"]]), ("epsilonr", p["epsr"])], log, "smm.epsr")

    _set_props(j.physics("semi").feature("smm1"), mats["absorber"])  # 전역 = 흡수층
    smm_s = semi.create("smm_s", "SemiconductorMaterialModel", 3)
    smm_s.selection().named("d_sno2")
    _set_props(smm_s, mats["sno2"])
    smm_n = semi.create("smm_n", "SemiconductorMaterialModel", 3)
    smm_n.selection().named("d_niox")
    _set_props(smm_n, mats["niox"])
    adm_s = semi.create("adm_s", "AnalyticDopingModel", 3)
    adm_s.selection().named("d_sno2")
    adm_s.set("impurityType", "donor")
    adm_s.set("NDc", mats["sno2_nd"])
    adm_n = semi.create("adm_n", "AnalyticDopingModel", 3)
    adm_n.selection().named("d_niox")
    adm_n.set("impurityType", "acceptor")
    adm_n.set("NAc", mats["niox_na"])
    tar = semi.create("tar1", "TrapAssistedRecombination", 3)
    tar.selection().all()
    tar.set("taun_mat", "userdef")
    tar.set("taun", taun)
    tar.set("taup_mat", "userdef")
    tar.set("taup", taun)
    udg = semi.create("udg1", "UDGeneration", 3)
    udg.selection().all()
    udg.set("Gn", "G_ph")
    udg.set("Gp", "G_ph")
    if t_idl > 0:
        from .ibc_builder import _add_idl_rec
        _add_idl_rec(semi, log, t_idl, s_ifc_cms, dim=3)
    mc1 = semi.create("mc1", "MetalContact", 2)
    ents_n = ents_of([f"bb{i}" for i, is_n, *_ in fingers if is_n])
    mc1.selection().set(ents_n)
    mc2 = semi.create("mc2", "MetalContact", 2)
    ents_p = ents_of([f"bb{i}" for i, is_n, *_ in fingers if not is_n])
    mc2.selection().set(ents_p)
    mc2.set("V0", "V0")
    log(f"  접점 OK: 접지=n 핑거 바닥 {len(ents_n)}면 / V0=p 핑거 바닥 {len(ents_p)}면 "
        f"(경계는 부품 합산 — Union named 미사용)")
    if not ents_n or not ents_p:
        raise RuntimeError("접점 경계 선택이 비었습니다 — 바닥 Box 좌표 확인 필요")

    msh = j.mesh().create("mesh1", "geom1")
    try:
        msh.create("swp1", "Sweep")
        log("  메시: Sweep")
    except Exception as e:
        log(f"  Sweep 실패({type(e).__name__}) — 기본 메시 (수렴 위험)")
    if t_idl > 0 and not mesh_hmax_nm:
        mesh_hmax_nm = 120.0
        log("  메시 자동 세분: hmax=120nm (IDL 안전망)")
    if mesh_hmax_nm:
        try:
            msh.feature("size").set("custom", "on")
            msh.feature("size").set("hmax", f"{mesh_hmax_nm:g}[nm]")
            log(f"  메시 세분: hmax={mesh_hmax_nm:g}nm")
        except Exception as e:
            log(f"  메시 세분 실패({type(e).__name__})")

    std1 = j.study().create("std1")
    std1.create("semie", "SemiconductorEquilibrium")
    std2 = j.study().create("std2")
    stat = std2.create("stat", "Stationary")
    stat.set("useparam", "on")
    stat.set("pname", ["V0"])
    if v0_only:
        stat.set("plistarr", ["0"])
        log("  스터디: Jsc 전용 (V0=0 단일점)")
    else:
        v = vcfg or {"start": 0.0, "stop": 2.0, "step": 0.05}
        stat.set("plistarr", [f"range({v['start']},{v['step']},{v['stop']})"])
    stat.set("punit", ["V"])
    _try_set(stat, [("sweeptype", "sparse")], log, "stat")
    _try_set(stat, [("pcontinuation", "V0")], log, "stat")
    for pn, pv in [("initmethod", "sol"), ("initstudy", "std1"), ("initstudystep", "semie"),
                   ("notsolmethod", "sol"), ("notstudy", "std1"), ("notstudystep", "semie")]:
        _try_set(stat, [(pn, pv)], log, "stat.init")
    return model, area_cm2
