"""제네릭 1D 반도체 스택 빌더 — v0.3 아키텍처의 핵심 (계획서 14절).

library.resolve_layers()가 만든 레이어 목록을 받아 COMSOL 모델을 조립한다.
COMSOL API 속성명은 2026-07-06 진단 덤프 + v0 페로브스카이트 케이스 실증으로 확정된 것만 사용.

레이어 항목: {name, thickness_nm(float), props(Eg/chi/epsr/Nc/Nv/mun/mup),
             absorber(bool), doping{type, conc}?, srh{taun, taup}?}
"""
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def _try_set(node, pairs, log, label):
    for prop, val in pairs:
        try:
            node.set(prop, val)
            return True
        except Exception as e:
            log(f"    set {label}.{prop}: {type(e).__name__} (다음 후보)")
    return False


def build(client, name, layers, gen_cfg, vcfg, area, log):
    """스택 모델 생성. 반환: (model, area_ok)."""
    model = client.create(name)
    j = model.java

    try:
        j.component().create("comp1", True)
    except Exception:
        j.modelNode().create("comp1")
    comp = j.component("comp1")
    geom = comp.geom().create("geom1", 1)
    geom.lengthUnit("nm")
    j.param().set("V0", "0[V]")

    # 지오메트리: 누적 좌표 (그리드 조합마다 재빌드하므로 숫자 직접 사용)
    coords = [0.0]
    for lay in layers:
        coords.append(coords[-1] + lay["thickness_nm"])
    i1 = geom.create("i1", "Interval")
    i1.set("specify", "coord")
    i1.set("coord", [f"{c:g}" for c in coords])
    geom.run()
    stack_str = " / ".join(f"{l['name']}({l['thickness_nm']:g}nm)" for l in layers)
    log(f"  지오메트리 OK: {stack_str}")

    # 보간 함수 (데이터셋 파일 → 테이블 주입)
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

    # 광생성: absorber 레이어에서만, x는 absorber 시작점 기준
    abs_idx = next(i for i, l in enumerate(layers) if l["absorber"])  # 0-base
    x0 = coords[abs_idx]
    lam0, lam1 = gen_cfg["lambda_nm"]
    expr = (f"4*pi/(h_const*c_const)*integrate(kref(lm)*F(lm)*"
            f"exp(-4*pi*kref(lm)*(x-{x0:g}[nm])/lm),lm,{lam0}[nm],{lam1}[nm])")
    try:
        var = comp.variable().create("var1")
    except Exception:
        var = j.variable().create("var1")
        var.model("comp1")
    var.set("G_ph", expr)
    log(f"  광생성 OK: absorber(도메인 {abs_idx+1}), λ {lam0}-{lam1}nm, x0={x0:g}nm")

    # 물리 + 레이어별 물성/도핑/SRH
    semi = comp.physics().create("semi", "Semiconductor", "geom1")
    area_ok = _try_set(semi, [("A", area)], log, "semi.A")
    if not area_ok:
        try:
            semi.prop("d").set("A", area)
            area_ok = True
        except Exception as e:
            log(f"    단면적 설정 실패 → 기본 1 m² 보정 ({type(e).__name__})")

    ftag = 0
    for i, lay in enumerate(layers, start=1):
        ftag += 1
        smm = semi.create(f"smm{ftag+1}", "SemiconductorMaterialModel", 1)
        smm.selection().set(i)
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
            adm = semi.create(f"adm{i}", "AnalyticDopingModel", 1)
            adm.selection().set(i)
            if d["type"] == "donor":
                adm.set("impurityType", "donor")
                adm.set("NDc", d["conc"])
            else:
                adm.set("impurityType", "acceptor")
                adm.set("NAc", d["conc"])
        if "srh" in lay:
            tar = semi.create(f"tar{i}", "TrapAssistedRecombination", 1)
            tar.selection().set(i)
            tar.set("taun_mat", "userdef")
            tar.set("taun", lay["srh"]["taun"])
            tar.set("taup_mat", "userdef")
            tar.set("taup", lay["srh"]["taup"])
    udg = semi.create("udg1", "UDGeneration", 1)
    udg.selection().set(abs_idx + 1)
    udg.set("Gn", "G_ph")
    udg.set("Gp", "G_ph")

    # 접촉 극성: V0(+)는 반드시 p형 쪽 접점에 — 0→+1.2V 스윕이 순방향이 되도록.
    # (2026-07-07 v1 첫 실행 교훈: 오른쪽이 n형인 스택에 오른쪽 V0를 걸면
    #  역바이어스 → J-V 평탄, Voc 없음)
    def _dop(lay):
        return (lay.get("doping") or {}).get("type")
    if _dop(layers[0]) == "acceptor" or _dop(layers[-1]) == "donor":
        v0_bnd, gnd_bnd = 1, len(layers) + 1              # p형이 왼쪽
    elif _dop(layers[0]) == "donor" or _dop(layers[-1]) == "acceptor":
        v0_bnd, gnd_bnd = len(layers) + 1, 1              # p형이 오른쪽 (v0와 동일)
    else:
        v0_bnd, gnd_bnd = len(layers) + 1, 1
        log("  ⚠️ 레이어 도핑으로 극성 판별 불가 — 기본(오른쪽 V0) 사용, J-V 부호 확인 필요")
    mc1 = semi.create("mc1", "MetalContact", 0)
    mc1.selection().set(gnd_bnd)
    mc2 = semi.create("mc2", "MetalContact", 0)
    mc2.selection().set(v0_bnd)
    mc2.set("V0", "V0")
    log(f"  접점 OK: V0 스윕=경계{v0_bnd}(p쪽), 접지=경계{gnd_bnd}(n쪽)")
    log("  물리 OK: 레이어별 물성 " + ", ".join(
        f"{l['material']}({l['material_version']})" for l in layers))

    j.mesh().create("mesh1", "geom1")

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
    return model, area_ok
