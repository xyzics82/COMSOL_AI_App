"""평판(planar) 스택의 2D/3D 빌더 — v2 차원 확장 1단계.

정직한 경계 (계획서 원칙):
- 여기서의 2D/3D는 1D와 물리적으로 동일한 '평판 스택'을 가로로 압출한 것이다.
  따라서 결과(Jsc/Voc/FF/PCE)가 1D와 일치해야 하며, 그것이 이 빌더의 검증 기준이다.
- IBC처럼 진짜 2D 기하(가로 방향 구조)는 이 빌더 검증 후 별도 빌더(ibc2d)로 개발한다.
- 도메인/경계 번호 하드코딩을 피하려고 좌표 기반 Box 선택을 쓴다.
  ✅ 2026-07-07 스파이크·1D 대조로 확정: entitydim은 문자열로 설정, 도메인 상자는 층보다
  크게(inside 조건), 2D 면외두께는 semi.prop('d'), 메시는 2D=Map/3D=Sweep 필수(얇은 층 수렴),
  1D 대조 편차 ~1%대 (Jsc -1.4~-1.7%, Voc -0.1%, PCE -1.0~-1.3%).
- 1D는 기존 검증된 stack_builder.build()를 그대로 쓴다 (이 파일은 dim 2/3 전용).

레이어 항목: stack_builder와 동일 {name, material, thickness_nm, props, absorber, doping?, srh?}
적층 축: 2D=y, 3D=z. 가로 폭 W(및 3D 깊이 D)=1um — 평판이라 값은 결과에 영향 없어야 함(검증 항목).
"""
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

W_NM = 1000.0  # 가로 폭 1um (2D/3D), 3D 깊이도 동일


def _try_set(node, pairs, log, label):
    for prop, val in pairs:
        try:
            node.set(prop, val)
            return True
        except Exception as e:
            log(f"    set {label}.{prop}: {type(e).__name__} (다음 후보)")
    return False


def _box_select(j, tag, dim_geom, entity_dim, lo, hi, log):
    """좌표 범위 기반 선택 생성. lo/hi = (x0,y0,z0),(x1,y1,z1) — 사용 축만 의미 있음.

    2026-07-07 2D 스파이크 결과: entitydim에 파이썬 int를 주면 TypeError(JPype 오버로드 불일치).
    문자열/JInt 후보를 순차 시도한다. 도메인 레벨은 기본값이라 실패해도 무해하지만
    경계(접점) 선택은 반드시 성공해야 한다.
    """
    sel = j.selection().create(tag, "Box")
    candidates = [str(entity_dim), entity_dim, float(entity_dim)]
    try:
        import jpype
        candidates.insert(1, jpype.JInt(entity_dim))
    except Exception:
        pass
    ok = False
    for v in candidates:
        try:
            sel.set("entitydim", v)
            ok = True
            log(f"    선택 {tag}: entitydim={entity_dim} OK ({type(v).__name__} 형)")
            break
        except Exception:
            continue
    if not ok:
        if entity_dim == dim_geom:
            log(f"    선택 {tag}: entitydim 설정 실패 — 도메인 레벨은 기본값이라 계속 진행")
        else:
            raise RuntimeError(f"선택 {tag}: entitydim={entity_dim}(경계) 설정 불가 — "
                               "접점을 지정할 수 없어 중단. 이 로그를 Claude에게 회신하세요")
    names = [("xmin", "xmax"), ("ymin", "ymax"), ("zmin", "zmax")][:dim_geom]
    for ax, (nlo, nhi) in enumerate(names):
        sel.set(nlo, f"{lo[ax]:g}[nm]")
        sel.set(nhi, f"{hi[ax]:g}[nm]")
    _try_set(sel, [("condition", "inside")], log, tag)
    return sel


def build(client, name, layers, gen_cfg, vcfg, log, dim=2):
    """2D/3D 평판 스택 생성. 반환: (model, area_cm2)."""
    assert dim in (2, 3), "dim은 2 또는 3 (1D는 stack_builder 사용)"
    model = client.create(name)
    j = model.java
    try:
        j.component().create("comp1", True)
    except Exception:
        j.modelNode().create("comp1")
    comp = j.component("comp1")
    geom = comp.geom().create("geom1", dim)
    geom.lengthUnit("nm")
    j.param().set("V0", "0[V]")
    # 가로 폭: 평판이라 결과에 무관 — 3D는 자유도 절약을 위해 좁게
    w_nm = W_NM if dim == 2 else 200.0

    # 지오메트리: 적층 축(2D=y, 3D=z)으로 레이어 쌓기
    coords = [0.0]
    for lay in layers:
        coords.append(coords[-1] + lay["thickness_nm"])
    total = coords[-1]
    for i, lay in enumerate(layers):
        t = lay["thickness_nm"]
        if dim == 2:
            r = geom.create(f"r{i+1}", "Rectangle")
            r.set("size", [f"{w_nm:g}", f"{t:g}"])
            r.set("pos", ["0", f"{coords[i]:g}"])
        else:
            b = geom.create(f"blk{i+1}", "Block")
            b.set("size", [f"{w_nm:g}", f"{w_nm:g}", f"{t:g}"])
            b.set("pos", ["0", "0", f"{coords[i]:g}"])
    geom.run()
    axis = "y" if dim == 2 else "z"
    stack_str = " / ".join(f"{l['name']}({l['thickness_nm']:g}nm)" for l in layers)
    log(f"  지오메트리 OK ({dim}D, 폭 {w_nm:g}nm, 적층축 {axis}): {stack_str}")

    # 좌표 기반 선택: 레이어별 도메인 + 양끝 접점 경계
    eps = 0.5  # nm 여유
    big = w_nm + 10.0
    for i, lay in enumerate(layers):
        # 'inside' 조건 = 도메인이 상자 안에 '완전히' 들어와야 선택됨.
        # (2026-07-07 2D 스파이크 교훈: 층보다 작게 잡으면 아무것도 안 잡혀
        #  기본 smm1으로 떨어짐 → "Undefined material property 'Nv'")
        # 층 범위보다 eps만큼 크게, 단 이웃 층은 밖으로 나가게.
        lo3 = [-eps, -eps, -eps]
        hi3 = [big, big, big]
        lo3[dim - 1] = coords[i] - eps
        hi3[dim - 1] = coords[i + 1] + eps
        _box_select(j, f"dom{i+1}", dim, dim, lo3[:dim] + [0] * (3 - dim),
                    hi3[:dim] + [0] * (3 - dim), log)
    for tag, pos in (("bndlo", 0.0), ("bndhi", total)):
        lo3 = [-eps, -eps, -eps]
        hi3 = [big, big, big]
        lo3[dim - 1] = pos - eps
        hi3[dim - 1] = pos + eps
        _box_select(j, tag, dim, dim - 1, lo3[:dim] + [0] * (3 - dim),
                    hi3[:dim] + [0] * (3 - dim), log)

    # 보간 함수 (1D 빌더와 동일)
    from . import data_prep
    am15 = np.loadtxt(DATA / data_prep.dataset(gen_cfg["spectrum_dataset"])["file"], encoding="utf-8")
    f1 = j.func().create("int1", "Interpolation")
    f1.set("source", "table")
    f1.set("table", [[str(r[0]), str(r[1])] for r in am15])
    f1.set("funcname", "F")
    _try_set(f1, [("argunit", ["nm"]), ("argunit", "nm")], log, "int1")
    _try_set(f1, [("fununit", ["W/m^2/nm"]), ("fununit", "W/m^2/nm")], log, "int1")
    nk = np.loadtxt(DATA / data_prep.dataset(gen_cfg["nk_dataset"])["file"], encoding="utf-8")
    f2 = j.func().create("int2", "Interpolation")
    f2.set("source", "table")
    f2.set("table", [[str(r[0]), str(r[2])] for r in nk])
    f2.set("funcname", "kref")
    _try_set(f2, [("argunit", ["um"]), ("argunit", "um")], log, "int2")
    _try_set(f2, [("fununit", ["1"]), ("fununit", "1")], log, "int2")

    # 광생성: 적층축 좌표 기준 Beer-Lambert (1D의 x → 2D y / 3D z)
    abs_idx = next(i for i, l in enumerate(layers) if l["absorber"])
    x0 = coords[abs_idx]
    lam0, lam1 = gen_cfg["lambda_nm"]
    expr = (f"4*pi/(h_const*c_const)*integrate(kref(lm)*F(lm)*"
            f"exp(-4*pi*kref(lm)*({axis}-{x0:g}[nm])/lm),lm,{lam0}[nm],{lam1}[nm])")
    try:
        var = comp.variable().create("var1")
    except Exception:
        var = j.variable().create("var1")
        var.model("comp1")
    var.set("G_ph", expr)
    log(f"  광생성 OK: absorber(레이어 {abs_idx+1}), λ {lam0}-{lam1}nm, {axis}0={x0:g}nm")

    # 물리 (선택은 named Box)
    semi = comp.physics().create("semi", "Semiconductor", "geom1")
    if dim == 2:  # 면외 두께 1um → 단면적 = W × d
        ok = _try_set(semi, [("d", f"{W_NM:g}[nm]")], log, "semi.d")
        if not ok:
            try:
                semi.prop("d").set("d", f"{W_NM:g}[nm]")
                ok = True
                log("  2D 면외 두께 OK: semi.prop('d') 경로 (2026-07-07 스파이크에서 확정)")
            except Exception as e:
                log(f"    2D 면외 두께 설정 실패 [확인 대상] ({type(e).__name__}) — 기본값 사용")
        area_cm2 = (w_nm * 1e-7) * (W_NM * 1e-7) if ok else (w_nm * 1e-7) * 100.0  # 기본 d=1m 가정
    else:
        area_cm2 = (w_nm * 1e-7) ** 2

    for i, lay in enumerate(layers, start=1):
        smm = semi.create(f"smm{i+1}", "SemiconductorMaterialModel", dim)
        smm.selection().named(f"dom{i}")
        p = lay["props"]
        for prop, val in [("Eg0", p["Eg"]), ("chi0", p["chi"]),
                          ("Nc", p["Nc"]), ("Nv", p["Nv"]),
                          ("mun", p["mun"]), ("mup", p["mup"])]:
            smm.set(prop + "_mat", "userdef")
            smm.set(prop, val)
        smm.set("epsilonr_mat", "userdef")
        _try_set(smm, [("epsilonr", [p["epsr"]]), ("epsilonr", p["epsr"])], log, "smm.epsilonr")
        if "doping" in lay:
            d = lay["doping"]
            adm = semi.create(f"adm{i}", "AnalyticDopingModel", dim)
            adm.selection().named(f"dom{i}")
            if d["type"] == "donor":
                adm.set("impurityType", "donor")
                adm.set("NDc", d["conc"])
            else:
                adm.set("impurityType", "acceptor")
                adm.set("NAc", d["conc"])
        if "srh" in lay:
            tar = semi.create(f"tar{i}", "TrapAssistedRecombination", dim)
            tar.selection().named(f"dom{i}")
            tar.set("taun_mat", "userdef")
            tar.set("taun", lay["srh"]["taun"])
            tar.set("taup_mat", "userdef")
            tar.set("taup", lay["srh"]["taup"])
    udg = semi.create("udg1", "UDGeneration", dim)
    udg.selection().named(f"dom{abs_idx+1}")
    udg.set("Gn", "G_ph")
    udg.set("Gp", "G_ph")

    # 접점 극성: 1D와 동일 로직 — V0(+)는 p형 쪽
    def _dop(lay):
        return (lay.get("doping") or {}).get("type")
    if _dop(layers[0]) == "acceptor" or _dop(layers[-1]) == "donor":
        v0_sel, gnd_sel = "bndlo", "bndhi"   # p형이 아래(적층 시작)
    elif _dop(layers[0]) == "donor" or _dop(layers[-1]) == "acceptor":
        v0_sel, gnd_sel = "bndhi", "bndlo"
    else:
        v0_sel, gnd_sel = "bndhi", "bndlo"
        log("  ⚠️ 극성 판별 불가 — 기본(위쪽 V0), J-V 부호 확인 필요")
    mc1 = semi.create("mc1", "MetalContact", dim - 1)
    mc1.selection().named(gnd_sel)
    mc2 = semi.create("mc2", "MetalContact", dim - 1)
    mc2.selection().named(v0_sel)
    mc2.set("V0", "V0")
    log(f"  접점 OK: V0 스윕={v0_sel}(p쪽), 접지={gnd_sel}(n쪽) [Box 선택 — 스파이크 검증 대상]")
    log("  물리 OK: 레이어별 물성 " + ", ".join(
        f"{l['material']}({l.get('material_version','?')})" for l in layers))

    msh = j.mesh().create("mesh1", "geom1")
    if dim == 2:
        # 기본 삼각 메시는 얇은 층(예: BCP 5nm)에서 수렴 실패 (2026-07-07 2D 4층 케이스).
        # 적층 사각형 구조의 정석인 Mapped(사각) 메시 적용 — 실패 시 기본 메시 폴백.
        try:
            msh.create("map1", "Map")
            log("  2D 메시: Mapped(사각) 적용 [검증 중]")
        except Exception as e:
            log(f"  2D Mapped 메시 생성 실패 ({type(e).__name__}) — 기본 메시 사용 (수렴 위험)")
    if dim == 3:
        # 3D 기본 사면체 메시는 얇은 층 스택에서 수렴 실패 (2026-07-07 3D 스파이크).
        # 평판 압출 구조의 정석인 스윕 메시 시도 — 실패 시 기본 메시로 폴백.
        try:
            msh.create("swp1", "Sweep")
            log("  3D 메시: Sweep 적용 (자동 소스/대상) [검증 중]")
        except Exception as e:
            log(f"  3D Sweep 메시 생성 실패 ({type(e).__name__}) — 기본 메시 사용 (수렴 위험)")

    std1 = j.study().create("std1")
    std1.create("semie", "SemiconductorEquilibrium")
    std2 = j.study().create("std2")
    stat = std2.create("stat", "Stationary")
    plist = f"range({vcfg['start']},{vcfg['step']},{vcfg['stop']})"
    stat.set("useparam", "on")
    stat.set("pname", ["V0"])
    stat.set("plistarr", [plist])
    stat.set("punit", ["V"])
    _try_set(stat, [("sweeptype", "sparse")], log, "stat")
    _try_set(stat, [("pcontinuation", "V0")], log, "stat")
    for pn, pv in [("initmethod", "sol"), ("initstudy", "std1"), ("initstudystep", "semie"),
                   ("notsolmethod", "sol"), ("notstudy", "std1"), ("notstudystep", "semie")]:
        _try_set(stat, [(pn, pv)], log, "stat.init")
    return model, area_cm2
