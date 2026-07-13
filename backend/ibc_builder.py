"""IBC(후면 깍지형 전극) 페로브스카이트 2D 빌더 — v2 첫 '진짜 2D' 구조.

사양: cases/perovskite_ibc_2d/CASE_SPEC.md
- half-cell 대칭 단면: x∈[0, L], L = W + g. 사각형 5개 타일링(전부 mappable).
- 확정 API 재사용: Box 선택(entitydim=str, 층보다 큰 상자), Map 메시, 평형→스윕 스터디.
- 신규 API [스파이크 확인 대상]: Union 선택(흡수층 3개 도메인 묶기).
  실패 시 폴백: 흡수층 물성은 기본 smm1에 직접 설정(전역) + CTL smm이 국소 재정의,
  광생성은 y-게이트 식으로 흡수층 높이에서만.
"""
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

T_SNO2 = 20.0   # nm (사용자 사양)
T_NIOX = 10.0   # nm (사용자 사양)
D_OUT = 10000.0  # nm — 면외 두께 = 전극 길이 10um (사용자 사양)
T_IDL = 2.0     # nm — 계면 결함층(IDL) 두께: 수치 근사 파라미터 (S=d/τ 등가, CASE_SPEC 6.8)


def _try_set(node, pairs, log, label):
    for prop, val in pairs:
        try:
            node.set(prop, val)
            return True
        except Exception as e:
            log(f"    set {label}.{prop}: {type(e).__name__} (다음 후보)")
    return False


def _box(j, tag, entity_dim, x0, x1, y0, y1, log):
    """확정 패턴: 좌표 Box 선택 (entitydim 문자열, inside 조건)."""
    sel = j.selection().create(tag, "Box")
    for v in (str(entity_dim),):
        sel.set("entitydim", v)
    sel.set("xmin", f"{x0:g}[nm]")
    sel.set("xmax", f"{x1:g}[nm]")
    sel.set("ymin", f"{y0:g}[nm]")
    sel.set("ymax", f"{y1:g}[nm]")
    _try_set(sel, [("condition", "inside")], log, tag)
    return sel


def _sel_entities(jmodel, tag, edim, log):
    """모델 선택 노드의 결정 엔티티 목록 (진단·폴백용)."""
    try:
        return [int(v) for v in jmodel.selection(tag).entities(edim)]
    except Exception:
        try:
            return [int(v) for v in jmodel.selection(tag).entities()]
        except Exception as e:
            log(f"    선택 {tag} 엔티티 조회 실패: {type(e).__name__}")
            return None


def _add_idl_rec(semi, log, t_idl_nm, s_cms, dim=2):
    """계면 재결합 v1 — IDL(계면 결함층) 등가 방식 (#29, CASE_SPEC 6.8).

    근거(COMSOL 문서, Semiconductor Module User's Guide p.116):
    - TrapAssistedSurfaceRecombination은 Insulation/Thin Insulator Gate/Insulator
      Interface/Metal Contact(Schottky) 경계 '전용' → 반도체-반도체 내부 계면에는 기여 0
      (실측: 선택 지정에도 지표 불변).
    - 내부 계면 전용 feature(TrapAssistedHeterointerfaceRecombination)는
      Continuity/Heterojunction을 thermionic emission으로 바꿔야 하며 explicit trap만
      지원 → 물리 기저 변경 + 미검증 API 다수라 v2로 미룸.
    → v1: CTL 상면 위 t_idl 두께 흡수층 도메인에 SRH τ=d/S 부여 (S=d/τ 등가, 문헌 관용).
    검증된 API만 사용: TrapAssistedRecombination(도메인) + Box 선택 named."""
    tau_s = (t_idl_nm * 1e-7) / s_cms   # [s] = [cm] / [cm/s]
    for tag, seln in (("tar_idl_n", "d_idl_n"), ("tar_idl_p", "d_idl_p")):
        t2 = semi.create(tag, "TrapAssistedRecombination", dim)
        t2.selection().named(seln)
        t2.set("taun_mat", "userdef")
        t2.set("taun", f"{tau_s:.6g}[s]")
        t2.set("taup_mat", "userdef")
        t2.set("taup", f"{tau_s:.6g}[s]")
        try:
            ents = [int(v) for v in t2.selection().entities(dim)]
        except Exception:
            ents = []
        log(f"    IDL {tag}: 도메인 {ents} (τ={tau_s:.3g}s)")
        if not ents:
            log(f"    ⚠️ {tag} 선택이 비었음 — 계면 재결합 무효과 위험")
    log(f"  계면 재결합 OK (IDL 등가층): d={t_idl_nm:g}nm, S={s_cms:g} cm/s "
        f"→ τ_IDL={tau_s:.3g}s (벌크 SRH와 가산)")


def _add_surface_rec(semi, log, sel_name, s_cms, edim=1, jmodel=None, parts=()):
    """[보류 — 외부 경계 전용] 표면 재결합 경계 feature.
    ⚠️ COMSOL 문서 확인(2026-07-08): TrapAssistedSurfaceRecombination은 Insulation/
    Thin Insulator Gate/Insulator Interface/Metal Contact(Schottky) 경계에서만 기여.
    반도체-반도체 내부 계면(CTL/흡수층)에는 무효 → 내부 계면은 _add_idl_rec 사용.
    이 함수는 향후 '외부 표면(상면 패시베이션 등)' 재결합용으로 보존.
    named 선택이 빈 것으로 해석되면 parts(Box 선택들)의 엔티티를 직접 합쳐 지정(폴백).
    (교훈: 경계 Box들의 Union 선택은 physics named()에서 0개로 해석 — 부품 직접 합산 필요)"""
    for ftype in ("TrapAssistedSurfaceRecombination", "SurfaceTrapAssistedRecombination",
                  "SurfaceRecombination", "TrapAssistedRecombination"):
        try:
            f = semi.create("srec1", ftype, edim)
            f.selection().named(sel_name)
            ok_n = _try_set(f, [("vsn", f"{s_cms:g}[cm/s]"), ("Sn0", f"{s_cms:g}[cm/s]"),
                                ("Sn", f"{s_cms:g}[cm/s]")], log, "srec.n")
            ok_p = _try_set(f, [("vsp", f"{s_cms:g}[cm/s]"), ("Sp0", f"{s_cms:g}[cm/s]"),
                                ("Sp", f"{s_cms:g}[cm/s]")], log, "srec.p")
            log(f"  계면 재결합 OK: feature={ftype}, S={s_cms:g} cm/s "
                f"(속도 설정 n:{'OK' if ok_n else '실패'} p:{'OK' if ok_p else '실패'})")
            # 선택 검증: named가 비면 부품 Box 선택 엔티티 직접 합산으로 폴백
            try:
                ents = [int(v) for v in f.selection().entities(edim)]
            except Exception:
                ents = []
            if not ents and jmodel is not None:
                if parts:
                    log(f"    ⚠️ named({sel_name}) 해석 결과 0개 — 부품 선택 직접 합산 폴백")
                merged = []
                for tag in parts:
                    e_ = _sel_entities(jmodel, tag, edim, log)
                    log(f"    부품 {tag}: {e_ if e_ is not None else '조회 실패'}")
                    merged += e_ or []
                if merged:
                    f.selection().set([int(v) for v in sorted(set(merged))])
                    ents = merged
            log(f"    srec 적용 엔티티({edim}dim): {sorted(set(ents))[:15]} (총 {len(set(ents))}개)")
            if not ents:
                log("    ⚠️ 계면 선택이 최종적으로 비어 있음 — 재결합 무효과 상태")
            return bool(ents)
        except Exception as e:
            log(f"    srec 후보 {ftype}: {type(e).__name__} (다음 후보)")
            try:
                semi.feature().remove("srec1")
            except Exception:
                pass
    log("  ⚠️ 계면 재결합 feature 생성 실패 — 전 후보 거부, 미적용으로 진행")
    return False


def build(client, name, mats, w_nm, gap_nm, t_abs_nm, taun, vcfg, log, g_profile=None,
          s_ifc_cms=0.0, mesh_hmax_nm=None):
    """IBC half-cell 모델 생성. mats: {'absorber':props, 'sno2':props, 'niox':props,
    'niox_na': str, 'sno2_nd': str}. g_profile: wo_optics.compute_G_profile 결과(dict)면
    파동광학 G(depth)를 보간 주입, None이면 Beer-Lambert.
    mesh_hmax_nm: 지정 시 hmax 강제(수렴 검사용); None이면 gap≥4um에서만 120nm 세분.
    반환: (model, area_cm2)."""
    L = w_nm + gap_nm                    # half-n + gap + half-p
    hw = w_nm / 2.0
    H = T_SNO2 + t_abs_nm                # 전체 높이 (흡수층 윗면)
    eps = 0.5
    model = client.create(name)
    j = model.java
    try:
        j.component().create("comp1", True)
    except Exception:
        j.modelNode().create("comp1")
    comp = j.component("comp1")
    geom = comp.geom().create("geom1", 2)
    geom.lengthUnit("nm")
    j.param().set("V0", "0[V]")

    # 사각형 타일 (겹침 없음 → Form Union 후에도 mappable)
    # s_ifc_cms>0이면 CTL 상면 바로 위 IDL(계면 결함층, t_idl) 타일 추가 — τ=d/S 등가 (#29)
    t_idl = T_IDL if (s_ifc_cms and s_ifc_cms > 0) else 0.0
    rects = [
        ("r_sno2", 0.0, hw, 0.0, T_SNO2),                    # SnO2
        ("r_gap", hw, L - hw, 0.0, T_SNO2),                  # 간격부 흡수층(바닥)
        ("r_niox", L - hw, L, 0.0, T_NIOX),                  # NiOx
    ]
    if t_idl > 0:
        rects += [
            ("r_idl_p", L - hw, L, T_NIOX, T_NIOX + t_idl),      # IDL(p계면)
            ("r_absnx", L - hw, L, T_NIOX + t_idl, T_SNO2),      # NiOx 위 흡수층 채움
            ("r_idl_n", 0.0, hw, T_SNO2, T_SNO2 + t_idl),        # IDL(n계면)
            ("r_bmid", hw, L - hw, T_SNO2, T_SNO2 + t_idl),      # 같은 높이 밴드(일반 흡수층)
            ("r_bp", L - hw, L, T_SNO2, T_SNO2 + t_idl),         # 같은 높이 밴드(일반 흡수층)
            ("r_top", 0.0, L, T_SNO2 + t_idl, H),                # 본체 흡수층
        ]
    else:
        rects += [
            ("r_absnx", L - hw, L, T_NIOX, T_SNO2),          # NiOx 위 흡수층 채움
            ("r_top", 0.0, L, T_SNO2, H),                    # 본체 흡수층
        ]
    for tag, x0, x1, y0, y1 in rects:
        r = geom.create(tag, "Rectangle")
        r.set("size", [f"{x1 - x0:g}", f"{y1 - y0:g}"])
        r.set("pos", [f"{x0:g}", f"{y0:g}"])
    geom.run()
    log(f"  지오메트리 OK (IBC 2D half-cell): W={w_nm/1000:g}um, gap={gap_nm/1000:g}um, "
        f"L={L/1000:g}um, t_abs={t_abs_nm:g}nm, SnO2 {T_SNO2:g}/NiOx {T_NIOX:g}nm")

    # 도메인 선택: CTL 2개 + 흡수층 3개 → Union
    _box(j, "d_sno2", 2, -eps, hw + eps, -eps, T_SNO2 + eps, log)
    _box(j, "d_niox", 2, L - hw - eps, L + eps, -eps, T_NIOX + eps, log)
    _box(j, "d_gap", 2, hw - eps, L - hw + eps, -eps, T_SNO2 + eps, log)
    _box(j, "d_absnx", 2, L - hw - eps, L + eps, T_NIOX - eps, T_SNO2 + eps, log)
    _box(j, "d_top", 2, -eps, L + eps, T_SNO2 - eps, H + eps, log)
    absorber_sel = None
    try:
        uni = j.selection().create("d_abs", "Union")
        uni.set("input", ["d_gap", "d_absnx", "d_top"])
        absorber_sel = "d_abs"
        log("  흡수층 선택: Union(d_gap+d_absnx+d_top) OK [스파이크 확인]")
    except Exception as e:
        log(f"  Union 선택 실패 ({type(e).__name__}) — 기본 smm 폴백 사용")

    # 접점 경계 선택 (바닥의 해당 구간 에지만 완전히 들어오게)
    _box(j, "b_n", 1, -eps, hw + eps, -eps, eps, log)          # SnO2 바닥 (접지)
    _box(j, "b_p", 1, L - hw - eps, L + eps, -eps, eps, log)   # NiOx 바닥 (V0)
    # IDL 도메인 선택 (계면 재결합 v1 — 경계 feature는 내부 경계 비활성이라 IDL 방식, 6.8절)
    if t_idl > 0:
        _box(j, "d_idl_n", 2, -eps, hw + eps, T_SNO2 - eps, T_SNO2 + t_idl + eps, log)
        _box(j, "d_idl_p", 2, L - hw - eps, L + eps, T_NIOX - eps, T_NIOX + t_idl + eps, log)

    # 광생성 (윗면 y=H에서 하향): 파동광학 프로파일 주입 or Beer-Lambert 적분식
    if g_profile is not None:
        f3 = j.func().create("int3", "Interpolation")
        f3.set("source", "table")
        f3.set("table", [[f"{d:.2f}", f"{g:.6e}"] for d, g in
                         zip(g_profile["depth_nm"], g_profile["G"])])
        f3.set("funcname", "Gwo")
        _try_set(f3, [("argunit", ["nm"]), ("argunit", "nm")], log, "int3")
        _try_set(f3, [("fununit", ["1/(m^3*s)"]), ("fununit", "1/(m^3*s)")], log, "int3")
        depth = f"({H:g}[nm]-y)"
        expr = (f"Gwo({depth})*({depth}>=0[nm])*"
                f"({depth}<={t_abs_nm:g}[nm])")
        log(f"  광생성 OK: 파동광학 G(depth) 보간 주입 ({len(g_profile['G'])}점, "
            f"Jsc,opt={g_profile['jsc_wave']:.2f} mA/cm²)")
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
        depth = f"({H:g}[nm]-y)"
        expr = (f"(4*pi/(h_const*c_const)*integrate(kref(lm)*F(lm)*"
                f"exp(-4*pi*kref(lm)*{depth}/lm),lm,300[nm],850[nm]))*"
                f"({depth}>=0[nm])*({depth}<={t_abs_nm:g}[nm])")
        log(f"  광생성 OK: Beer-Lambert (깊이 = {H:g}nm − y), 음영 없음(IBC)")
    try:
        var = comp.variable().create("var1")
    except Exception:
        var = j.variable().create("var1")
        var.model("comp1")
    var.set("G_ph", expr)

    # 물리: 기본 smm1 = 흡수층 물성(전역), CTL은 국소 재정의
    semi = comp.physics().create("semi", "Semiconductor", "geom1")
    try:
        semi.prop("d").set("d", f"{D_OUT:g}[nm]")
        log(f"  면외 두께 OK: d = {D_OUT/1000:g}um (전극 길이)")
    except Exception as e:
        log(f"  면외 두께 설정 실패 ({type(e).__name__}) — 기본값(1m): J 환산 주의")
    area_cm2 = (L * 1e-7) * (D_OUT * 1e-7)

    def _set_props(smm, p):
        for prop, val in [("Eg0", p["Eg"]), ("chi0", p["chi"]),
                          ("Nc", p["Nc"]), ("Nv", p["Nv"]),
                          ("mun", p["mun"]), ("mup", p["mup"])]:
            smm.set(prop + "_mat", "userdef")
            smm.set(prop, val)
        smm.set("epsilonr_mat", "userdef")
        _try_set(smm, [("epsilonr", [p["epsr"]]), ("epsilonr", p["epsr"])], log, "smm.epsr")

    _set_props(j.physics("semi").feature("smm1"), mats["absorber"])  # 기본(전역) = 흡수층
    smm_s = semi.create("smm_s", "SemiconductorMaterialModel", 2)
    smm_s.selection().named("d_sno2")
    _set_props(smm_s, mats["sno2"])
    smm_n = semi.create("smm_n", "SemiconductorMaterialModel", 2)
    smm_n.selection().named("d_niox")
    _set_props(smm_n, mats["niox"])

    adm_s = semi.create("adm_s", "AnalyticDopingModel", 2)
    adm_s.selection().named("d_sno2")
    adm_s.set("impurityType", "donor")
    adm_s.set("NDc", mats["sno2_nd"])
    adm_n = semi.create("adm_n", "AnalyticDopingModel", 2)
    adm_n.selection().named("d_niox")
    adm_n.set("impurityType", "acceptor")
    adm_n.set("NAc", mats["niox_na"])

    tar = semi.create("tar1", "TrapAssistedRecombination", 2)  # 전 도메인 (한계 7.5절)
    tar.selection().all()  # ⚠️ create()된 기능은 선택이 빈 채 시작 — 명시 필수 (2026-07-08)
    tar.set("taun_mat", "userdef")
    tar.set("taun", taun)
    tar.set("taup_mat", "userdef")
    tar.set("taup", taun)

    udg = semi.create("udg1", "UDGeneration", 2)
    if absorber_sel:
        udg.selection().named(absorber_sel)
        udg.set("Gn", "G_ph")
        udg.set("Gp", "G_ph")
    else:  # 폴백: 전역 + y-게이트 (CTL 높이 이하에서 0) — 간격부 바닥은 게이트로 못 살림(근사)
        udg.set("Gn", f"G_ph*(y>{T_SNO2:g}[nm])")
        udg.set("Gp", f"G_ph*(y>{T_SNO2:g}[nm])")
        log("  ⚠️ UDG 폴백: y-게이트 근사 (간격부 바닥 20nm 광생성 무시)")

    if t_idl > 0:
        _add_idl_rec(semi, log, t_idl, s_ifc_cms, dim=2)
    mc1 = semi.create("mc1", "MetalContact", 1)
    mc1.selection().named("b_n")          # 접지 (n쪽)
    mc2 = semi.create("mc2", "MetalContact", 1)
    mc2.selection().named("b_p")          # V0 (p쪽) → 0→+ 스윕 = 순방향
    mc2.set("V0", "V0")
    log("  접점 OK: V0=NiOx 바닥(p), 접지=SnO2 바닥(n) — 이상 옴익 (한계 7.1절)")

    msh = j.mesh().create("mesh1", "geom1")
    try:
        msh.create("map1", "Map")
        log("  메시: Mapped(사각) — 확정 패턴")
    except Exception as e:
        log(f"  Mapped 실패 ({type(e).__name__}) — 기본 메시 (수렴 위험)")
    hmax_nm = mesh_hmax_nm if mesh_hmax_nm else (120.0 if gap_nm >= 4000 else None)
    if hmax_nm:  # 수렴 보강 (#29): 명시 지정 또는 gap≥4um 자동
        try:
            msh.feature("size").set("custom", "on")
            msh.feature("size").set("hmax", f"{hmax_nm:g}[nm]")
            log(f"  메시 세분: hmax={hmax_nm:g}nm "
                f"({'명시 지정' if mesh_hmax_nm else '넓은 gap 자동'})")
        except Exception as e:
            log(f"  메시 세분 실패({type(e).__name__}) — 기본 크기")

    std1 = j.study().create("std1")
    std1.create("semie", "SemiconductorEquilibrium")
    std2 = j.study().create("std2")
    stat = std2.create("stat", "Stationary")
    stat.set("useparam", "on")
    stat.set("pname", ["V0"])
    stat.set("plistarr", [f"range({vcfg['start']},{vcfg['step']},{vcfg['stop']})"])
    stat.set("punit", ["V"])
    _try_set(stat, [("sweeptype", "sparse")], log, "stat")
    _try_set(stat, [("pcontinuation", "V0")], log, "stat")
    for pn, pv in [("initmethod", "sol"), ("initstudy", "std1"), ("initstudystep", "semie"),
                   ("notsolmethod", "sol"), ("notstudy", "std1"), ("notstudystep", "semie")]:
        _try_set(stat, [(pn, pv)], log, "stat.init")
    return model, area_cm2
