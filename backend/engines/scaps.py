"""SCAPS-1D 엔진 — 페로브스카이트 문헌 표준 1D 시뮬레이터 (겐트대, 연구용 무료).

강점: 계면 결함(재결합 속도 S)의 네이티브 지원 → 우리 COMSOL IDL(τ=d/S) 구현의
독립 대조군. 배포는 이메일 신청(비공개 아카이브)이라 설치는 사용자 몫.

.def(소자 정의) 파일 포맷은 버전 의존 바이너리성 텍스트라 **앱이 창작하지 않는다**
(임의 포맷 생성 금지 원칙). 대신:
  export : ① '레시피'(GUI에 입력할 물성 표 — materials.json 매핑, 출처 포함)
           ② SCAPS 스크립트 초안(.script — 액션 문법은 ⚠️ 첫 실행 검증)
           → 사용자가 GUI에서 소자를 한 번 만들어 .def로 저장하면, 이후 스크립트가
             그 .def를 불러 배치 실행 (SCAPS의 표준 사용 흐름)
  local  : scaps.exe에 스크립트 전달 시도 (⚠️ CLI 구동 방식 첫 실행 검증)
  import : SCAPS이 저장한 I-V(.iv)/QE(.qe) 파일 업로드 → 지표·플롯 (이건 즉시 사용 가능)
"""
import re
import subprocess
import time
from pathlib import Path

import numpy as np

from .. import jobs
from . import common, find_scaps


def _recipe_md(params, log):
    """GUI 입력용 물성 표 — materials.json에서 (SCAPS 입력 항목 명칭으로 변환)."""
    rows = []
    header = ("| 항목 (SCAPS 표기) | " + " | ".join(["SnO2 (ETL)", "MAPbI3 (흡수층)", "NiOx (HTL)"]) + " |")
    sep = "|---|---|---|---|"
    mats = [common.material("sno2"), common.material("mapbi3"), common.material("niox")]
    v = common.si_value

    def row(label, key, conv=lambda x: x, unit=""):
        cells = []
        for m in mats:
            try:
                cells.append(f"{conv(v(m['props'][key])):g}{unit}")
            except Exception:
                cells.append("—")
        rows.append(f"| {label} | " + " | ".join(cells) + " |")

    t_abs = float(params.get("t_abs_nm", 800))
    rows.append(f"| thickness [um] | {float(params.get('t_etl_nm', 20)) / 1000:g} | "
                f"{t_abs / 1000:g} | {float(params.get('t_htl_nm', 10)) / 1000:g} |")
    row("bandgap Eg [eV]", "Eg")
    row("electron affinity χ [eV]", "chi")
    row("dielectric permittivity (relative)", "epsr")
    row("CB effective DOS Nc [1/cm³]", "Nc")
    row("VB effective DOS Nv [1/cm³]", "Nv")
    row("electron mobility [cm²/Vs]", "mun")
    row("hole mobility [cm²/Vs]", "mup")
    rows.append("| shallow donor ND [1/cm³] | 1e19 | 0 | 0 |")
    rows.append("| shallow acceptor NA [1/cm³] | 0 | 0 | 1e18 |")
    taun = float(params.get("taun_ns", 38.7))
    s = float(params.get("s_ifc_cms", 1000))
    log(f"  레시피: 3층(n-i-p), τ={taun}ns → 흡수층 결함으로 환산 입력, 계면 S={s:g} cm/s")
    return "\n".join([
        "# SCAPS-1D 소자 레시피 (GUI 입력용)",
        "",
        "앱 materials.json(문헌 출처 있음)에서 자동 매핑. **숫자를 그대로 GUI에 입력**하고",
        "`.def`로 저장하면(예: `pin_mapi_app.def`) 이후 스크립트 배치 실행에 재사용됩니다.",
        "",
        "## 층 구조 (조명: ETL 쪽 = left contact 부터)",
        "front(=left) contact → SnO2 → MAPbI3 → NiOx → back contact",
        "",
        header, sep, *rows,
        "",
        "## 결함 (defects)",
        f"- MAPbI3 벌크: 중성 single level, midgap, τn=τp={taun}ns가 되도록",
        f"  Nt·σ·vth 설정 (예: vth=1e7cm/s, σ=1e-15cm² → Nt = 1/(τ·vth·σ) = "
        f"{1.0 / (taun * 1e-9 * 1e7 * 1e-15):.3g} cm⁻³)",
        f"- **계면 결함 2곳** (SnO2/MAPbI3, MAPbI3/NiOx): interface defect, neutral,",
        f"  **energetic distribution = uniform** (중심 midgap=0.775eV above highest EV,",
        f"  characteristic energy 0.1eV), σn=σp=1e-15cm², "
        f"Nt(total)={s / (1e-15 * 1e7):.3g} cm⁻²",
        f"  → S = σ·vth·Nt = {s:g} cm/s — COMSOL IDL(τ=d/S)과 같은 정의",
        "  ⚠️ single level 금지: 순방향 V≈Et(0.78V)에서 점유 스위칭으로 발산 (2026-07-13 실검증)",
        "",
        "## 수치 안정 필수 조정 (2026-07-13 GUI 실검증 — 없으면 1 sun V=0 발산)",
        "- NiOx: **χ=3.3, Eg=2.1** (문헌값 χ1.8/Eg3.6의 EV-고정 등가 — χ+Eg=5.4 유지,",
        "  ΔEc 2.1→0.6eV. 전자 차단은 여전히 완전 exp(-0.6/kT)≈4e-11, 정공 수송 불변)",
        "- SnO2: **χ=4.39 유지, Eg=1.66** (EV만 올려 ΔEv 2.38→0.6eV — 정공 차단 여전히 완전)",
        "- 근거: 문헌값 그대로면 소수캐리어 밀도가 1e-41까지 언더플로 → 뉴턴 발산 (원복 실험으로 확정)",
        "- **흡수 끄기 필수**: SnO2·NiOx 층의 Set absorption model에서 'sqrt() at Eg' 체크 해제(α=0)",
        "  — Eg 완화로 생기는 기생 흡수(20nm에 ~18%) 방지. MAPbI3 흡수는 그대로.",
        "- ⚠️ 대조 주의: 대리 (χ,Eg)는 CTL의 고유캐리어(ni)·소수캐리어 통계를 문헌값과",
        "  다르게 만듭니다 — COMSOL과의 비교는 '동일 물성' 정량 대조가 아니라 흡수층 τ·계면 S",
        "  중심의 준정량 대조로 취급하고, CTL 민감 지표는 참고용으로 보세요. (수치 대리모델임을",
        "  결과 보고에 명시)",
        "",
        "## 접점·조명",
        "- 접점: 이상 옴익(플랫밴드 or 일함수를 각 층 다수캐리어 준위에 정렬) — COMSOL v0와 동일 조건",
        "- **⚠️ 정의 패널 필수 설정 (2026-07-15 확정, 3소자 실증)**: 'apply voltage V to' =",
        "  **right contact(back)**, 'current reference' = **generator**. 왼쪽(조명측) 인가 +",
        "  consumer 기준이면 SCAPS 3.3.12가 순방향 0.76~0.82V에서 수렴 붕괴(비물리 가지).",
        "- 조명: AM1.5G 1sun, ETL(front) 쪽 입사 / 반사 무시(COMSOL BL 모드와 비교할 것)",
        "",
        "## 실행·저장",
        f"- I-V: 0 → {float(params.get('v_max', 1.3)):g} V, 스텝 {float(params.get('v_step', 0.02)):g} V",
        "- 결과를 `File > Save results > I-V`로 .iv 저장 → 앱 ③ '결과 가져오기'에 업로드",
    ])


def _script(params, out_path=None):
    vmax = float(params.get("v_max", 1.3))
    step = float(params.get("v_step", 0.02))
    npts = int(round(vmax / step)) + 1
    # save는 'save results.iv <파일>' 점 표기가 정문법 (2026-07-13 동봉 예제
    # 'which T gives Voc=0.500V.script' 실증 — 'save results iv'(공백)는 무시됨).
    # 절대경로(공백 포함)도 따옴표 없이 허용(예제 실증) → 앱 작업 폴더로 직접 저장해
    # Program Files 쓰기 권한/VirtualStore 문제를 회피한다. (ASCII 경로일 때만)
    out = "pin_mapi_app_out.iv"
    out_note = "// output: SCAPS results folder (default)"
    if out_path is not None:
        try:
            str(out_path).encode("ascii")
            out = str(out_path)
            out_note = ("// output goes straight to the app job folder -- edit the save "
                        "line if you run SCAPS on another PC")
        except UnicodeEncodeError:
            pass
    return f"""// SCAPS batch script (generated by app)
// Syntax verified 2026-07-13/14 against SCAPS 3.3.12 bundled examples + real runs:
//  - filenames are NOT quoted (quotes -> 'definition file not found')
//  - action grammar: action <block>.<field> <value>   (example: action cv.startv -1)
//  - illumination: 'action light' + 'load spectrumfile <name in [scaps]/spectrum>'
//    (a fresh CLI-launched SCAPS starts dark -- do not rely on GUI state)
//  - save grammar: save results.iv <relative file>  (goes to [scaps]/results;
//    matches bundled example -- absolute-path behavior untested)
//  - 'set quitscript.quitSCAPS' closes SCAPS when the script ends (enables full
//    automation; found in 'which T gives Voc=0.500V.script')
// Precondition: build the device once in the GUI per recipe.md, save as pin_mapi_app.def
{out_note}
set errorhandling.overwritefile
set errorhandling.outputlist.truncate
set script_display_mode.fully_suppressed
clear all
load definitionfile pin_mapi_app.def
clear actions
action light
load spectrumfile AM1_5G 1 sun.spe
action iv.doiv
action iv.startv 0.0000
action iv.stopv {vmax:.4f}
action iv.points {npts}
calculate singleshot
save results.iv {out}
set quitscript.quitSCAPS
"""


def _script_sweep(params, target, values):
    """변수 스윕 스크립트 — 루프 문법 대신 언롤(값별 set→calculate→save 블록).

    target: 'layer2.defect1.ntotal' 같은 set 경로 (동봉 예제 실증 필드만 UI에 노출).
    파일은 sweep_out_<k>.iv 로 저장 → 수집 후 값-지표 그래프.
    """
    vmax = float(params.get("v_max", 1.3))
    step = float(params.get("v_step", 0.02))
    npts = int(round(vmax / step)) + 1
    head = f"""// SCAPS sweep script (generated by app, unrolled -- no loop grammar risk)
// set <layer.field> grammar verified against bundled examples (2026-07-14)
set errorhandling.overwritefile
set errorhandling.outputlist.truncate
set script_display_mode.fully_suppressed
clear all
load definitionfile pin_mapi_app.def
clear actions
action light
load spectrumfile AM1_5G 1 sun.spe
action iv.doiv
action iv.startv 0.0000
action iv.stopv {vmax:.4f}
action iv.points {npts}
"""
    blocks = []
    for k, v in enumerate(values, 1):
        blocks.append(f"set {target} {v:g}\n"
                      f"calculate singleshot\n"
                      f"save results.iv sweep_out_{k}.iv\n")
    return head + "\n".join(blocks) + "\nset quitscript.quitSCAPS\n"


# 스윕 대상 — 동봉 예제에서 실증된 set 경로만 (계면 결함 등은 문법 미확인이라 제외)
SWEEP_TARGETS = {
    "absorber_defect_Nt [1/cm3] (τ=1/(Nt·σ·vth))": "layer2.defect1.ntotal",
    "absorber_thickness [um]": "layer2.thickness",
    "ETL_donor_ND [1/cm3]": "layer1.nd",
    "HTL_acceptor_NA [1/cm3]": "layer3.na",
}


def run_sweep(jid, params, log, case):
    """스윕 배치: SCAPS 자동 구동 1회로 전 값 계산 → J-V 겹침 + 지표-vs-값 그래프."""
    jd = jobs.job_dir(jid)
    target_label = str(params.get("sweep_param") or "")
    target = SWEEP_TARGETS.get(target_label)
    if not target:
        raise RuntimeError(f"지원하지 않는 스윕 대상: {target_label!r}")
    try:
        values = [float(v) for v in
                  str(params.get("sweep_values") or "").replace(" ", "").split(",") if v]
    except ValueError:
        raise RuntimeError("sweep_values는 쉼표로 구분한 숫자 목록이어야 합니다")
    if not 2 <= len(values) <= 12:
        raise RuntimeError("스윕 값은 2~12개로 제한합니다 (SCAPS 1회 구동으로 순차 계산)")
    exe = find_scaps()
    if not exe and str(params.get("mode", "local")) != "export":
        raise RuntimeError("SCAPS 실행 파일이 설정되지 않았습니다 — ② 엔진 설정 확인")
    log(f"SCAPS 스윕: {target} ← {values} ({len(values)}점, 소자=pin_mapi_app.def)")
    (jd / "batch.script").write_text(_script_sweep(params, target, values),
                                     encoding="ascii")
    if str(params.get("mode", "local")) == "export":
        log("반출용 스크립트 생성 완료 — SCAPS [Execute script]로 실행 후 "
            "sweep_out_*.iv를 ③에 업로드하세요")
        return
    import shutil
    scaps_dir = Path(exe).parent
    try:
        shutil.copy2(jd / "batch.script", scaps_dir / "script" / "app_sweep.script")
    except Exception as e:
        log(f"  (script 폴더 복사 실패: {type(e).__name__})")
    t_start = time.time()
    proc = subprocess.Popen([exe, str(jd / "batch.script")], cwd=str(jd))
    while proc.poll() is None:
        if time.time() - t_start > 120 * len(values) + 120:
            proc.kill()
            log("  SCAPS 타임아웃 — 프로세스 종료 후 완료분 수집")
            break
        jobs.check_cancel(jid)
        time.sleep(2)
    log(f"  종료 코드 {proc.returncode} ({time.time()-t_start:.0f}s)")
    # 수집: results(+VirtualStore)에서 sweep_out_*.iv
    import os
    rdirs = [scaps_dir / "results"]
    lad = os.environ.get("LOCALAPPDATA")
    if lad and len(scaps_dir.parts) > 1:
        rdirs.append(Path(lad) / "VirtualStore" / Path(*scaps_dir.parts[1:]) / "results")
    got = 0
    for rdir in rdirs:
        if rdir.exists():
            for f in sorted(rdir.glob("sweep_out_*.iv")):
                if f.stat().st_mtime >= t_start - 5:
                    shutil.copy2(f, jd / f.name)
                    got += 1
    log(f"  회수: sweep_out_*.iv {got}건 / 기대 {len(values)}건")
    if not got:
        raise RuntimeError("스윕 산출물이 없습니다 — SCAPSErrorLogFile.log 확인 필요"
                           " (set 경로 오타 시 여기에 기록됨)")
    # 판독: 값별 지표 + J-V 겹침 + 지표-vs-값 그래프
    pin = 100.0
    rows, plot = [], []
    for k, v in enumerate(values, 1):
        f = jd / f"sweep_out_{k}.iv"
        if not f.exists():
            rows.append({"value": v, "Jsc_mA_cm2": float("nan"), "Voc_V": float("nan"),
                         "FF": float("nan"), "PCE_pct": float("nan")})
            continue
        arr, _hdr = _numeric_block(f.read_text(encoding="utf-8", errors="replace"),
                                   log, f.name)
        # SCAPS는 세션의 이전 곡선을 누적 저장 → 파일 k에는 곡선 1..k가 들어 있음
        # (2026-07-14 실측: 66/132/198/264행). 마지막 세그먼트 = 이번 값의 곡선.
        x = arr[:, 0]
        br = np.where(np.diff(x) < 0)[0] + 1
        idx = (np.split(np.arange(x.size), br) if br.size else [np.arange(x.size)])[-1]
        V, J = x[idx], arr[idx, 1]
        m, Jgen = common.jv_metrics(V, J, pin, log)
        rows.append({"value": v, **{k2: m[k2] for k2 in
                                    ("Jsc_mA_cm2", "Voc_V", "FF", "PCE_pct")}})
        plot.append((f"{target.split('.')[-1]}={v:g}", V, Jgen))
        log(f"  [{v:g}] {m}")
    common.plot_jv(jid, plot, "jv_sweep.png", f"SCAPS sweep: {target}")
    _plot_sweep_metrics(jid, target, rows)
    with open(jd / "sweep_summary.csv", "w", encoding="utf-8") as fh:
        keys = list(rows[0].keys())
        fh.write(",".join(keys) + "\n")
        for r in rows:
            fh.write(",".join(str(r[k]) for k in keys) + "\n")
    log("스윕 완료: jv_sweep.png + sweep_metrics.png + sweep_summary.csv")


def _plot_sweep_metrics(jid, target, rows):
    """지표-vs-스윕값 2×2 그래프 (값 범위가 10배 이상이면 로그 x축)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    jd = jobs.job_dir(jid)
    vals = [r["value"] for r in rows]
    logx = min(vals) > 0 and max(vals) / min(vals) >= 10
    fig, axes = plt.subplots(2, 2, figsize=(9, 6.5), dpi=110)
    for ax, key, lab in zip(axes.ravel(),
                            ("Jsc_mA_cm2", "Voc_V", "FF", "PCE_pct"),
                            ("Jsc [mA/cm²]", "Voc [V]", "FF", "PCE [%]")):
        ax.plot(vals, [r[key] for r in rows], "o-")
        if logx:
            ax.set_xscale("log")
        ax.set_xlabel(target)
        ax.set_ylabel(lab)
        ax.grid(alpha=0.3)
    fig.suptitle(f"SCAPS sweep: {target}")
    fig.tight_layout()
    fig.savefig(jd / "sweep_metrics.png")
    plt.close(fig)


# ---------------- 논문 재현: Energies 2023, 16(21), 7438 ----------------
# ITO/SnO2/MAPbI3/NiOx (doi:10.3390/en16217438, 오픈액세스). Table 1·2 전 파라미터 +
# Rs 1 Ω·cm² / Rsh 1e3 Ω·cm² / 300K / AM1.5G. 기준 성능(본문·Fig1b): η 22.13% /
# Voc 1.323 V / Jsc 20.93 mA/cm² / FF 79.86% (초록의 FF 70.86은 본문과 불일치 — 본문 채택).
PAPER_REF = {"eta_pct": 22.13, "Voc_V": 1.323, "Jsc_mA_cm2": 20.93, "FF_pct": 79.86,
             "src": "Energies 2023, 16(21), 7438 — doi:10.3390/en16217438"}
PAPER_SWEEPS = {  # 논문 그림 대응 스윕 (층 번호: 1=ITO, 2=SnO2, 3=MAPbI3, 4=NiOx)
    "none": None,
    "absorber_thickness_um (Fig.2-3)": ["set layer3.thickness {v:g}"],
    "bulk_defect_Nt [1/cm3] (Fig.5a)": ["set layer3.defect1.ntotal {v:g}"],
    "interface_defect_Nt [1/cm2] (Fig.5b, 양쪽 계면)": [
        "set interface2.IFdefect1.ntotal {v:g}",
        "set interface3.IFdefect1.ntotal {v:g}"],
}

# 케이스별 논문 설정 (2026-07-14 — 설정 주도형: 새 논문은 여기+case.json만 추가)
PAPERS = {
    "scaps_paper_energies2023": {
        "ref": PAPER_REF, "def": "paper_energies2023_vright.def", "abs_layer": 3,
        "sweeps": PAPER_SWEEPS, "recipe_builder": "energies2023",
        # 판정 이력: 2026-07-14 "재현 불가(0.8V 발산)" → **2026-07-15 정정: 4지표 완전
        # 재현** (Jsc -0.00%/Voc +0.01%/FF -0.54%/PCE -0.61%). 근본 원인 = 정의 패널의
        # 'voltage V applied to'. SCAPS 3.3.12에서 왼쪽(조명측) 인가+consumer 기준이면
        # 순방향 0.76~0.82V에서 수렴 붕괴(비물리 가지) — **오른쪽(back) 인가+generator
        # 기준**이면 동일 소자가 완주. 세 논문 소자 모두에서 재현·해소 확인 (성공했던
        # pin def만 우연히 right 인가였던 것이 단서). def는 _vright 수정본 사용.
    },
    "scaps_paper_crystals2022": {
        # Crystals 2022, 12(1), 68 — doi:10.3390/cryst12010068 (오픈액세스)
        # FTO/TiO2/MAPbI3(암비언트, Eg 1.45 실측)/Spiro/Au — 실험-시뮬 동시 검증 모델.
        # 순정렬 밴드(전자 0.1eV·정공 0.25eV downhill) — energies2023의 역정렬 문제 없음.
        # 기준(재현 목표 = 논문 시뮬 열, 흡수층 Nt=9e16 실험 fit):
        "ref": {"eta_pct": 8.75, "Voc_V": 0.8001, "Jsc_mA_cm2": 25.81, "FF_pct": 42.37,
                "src": "Crystals 2022, 12(1), 68 — doi:10.3390/cryst12010068 (Nt=9e16)"},
        "def": "paper_crystals2022_vright.def", "abs_layer": 3,  # vright 필수 (아래 참조)
        "sweeps": {
            "none": None,
            # 논문 정량 궤적: Nt 9e16→1e11에서 Voc 0.8001→0.9037, FF 42.37→63.68,
            # η 8.75→15.87 (1e15: 15.61 / 1e14: 15.85) — 비교 목표가 수치로 존재
            "absorber_defect_Nt [1/cm3] (Fig.3)": ["set layer3.defect1.ntotal {v:g}"],
            # 두께 600→200nm: Jsc 25.81→26.97, Voc→0.8812, FF→53.37, η→12.69
            "absorber_thickness_um (Fig.4)": ["set layer3.thickness {v:g}"],
            "ETL_chi [eV] (Fig.6, 3.4-4.3)": ["set layer2.chi {v:g}"],
            "HTL_chi [eV] (Fig.7, 2.0-2.7)": ["set layer4.chi {v:g}"],
        },
        "recipe_builder": "static",  # cases/<id>/recipe.md를 작업 폴더로 복사
    },
    "scaps_paper_matadv_snpsc": {
        # Materials Advances (RSC OA) "Numerical investigation of high-performance
        # bilayer tin-based perovskite solar cells with SCAPS-1D" — **버전 3.3.12 명시**.
        # 판별 목적: 우리 3.3.12 설치본에서 0.78V 벽이 '전 소자 공통'인지 '앞 두 논문
        # 조합 특이'인지 확정. 단층 CsSnI3 (FTO/PCBM/CsSnI3/CFTS/Au), Voc~0.9급.
        # 기준: 흡수층 1.0um에서 PCE 15.75% (본문 — Voc/Jsc/FF 개별값은 그림만 제공)
        "ref": {"eta_pct": 15.75, "Voc_V": float("nan"), "Jsc_mA_cm2": float("nan"),
                "FF_pct": float("nan"),
                "src": "Mater. Adv. — bilayer Sn PSC, SCAPS 3.3.12 명시 (단층 CsSnI3 1.0um 기준)"},
        "def": "paper_matadv_sn_vright.def", "abs_layer": 3,  # vright 필수 (energies 참조)
        "sweeps": {
            "none": None,
            "absorber_thickness_um (0.1-1.5)": ["set layer3.thickness {v:g}"],
            "absorber_defect_Nt [1/cm3]": ["set layer3.defect1.ntotal {v:g}"],
            # 논문: 계면 결함 임계 1e14 cm-2 이상에서 급락 — 좋은 대조 스윕
            "interface_defect_Nt [1/cm2] (양쪽)": [
                "set interface2.IFdefect1.ntotal {v:g}",
                "set interface3.IFdefect1.ntotal {v:g}"],
        },
        "recipe_builder": "static",
    },
}


def _recipe_paper_md():
    return "\n".join([
        "# 논문 재현 레시피 — Energies 2023, 16(21), 7438 (ITO/SnO2/MAPbI3/NiOx)",
        "",
        "GUI에서 아래 표대로 **4층** 소자를 만들어 `paper_energies2023.def`로 저장하세요 (1회).",
        "이후 실행·스윕·QE는 앱이 전부 자동입니다. 출처: doi:10.3390/en16217438 Table 1·2.",
        "",
        "## 층 (왼쪽=조명 입사부터): ITO / SnO2 / MAPbI3 / NiOx",
        "",
        "| 항목 | ITO | SnO2(ETL) | MAPbI3 | NiOx(HTL) |",
        "|---|---|---|---|---|",
        "| thickness [um] | 0.30 | 0.05 | 0.40 | 0.05 |",
        "| Eg [eV] | 3.5 | 3.6 | 1.6 | 3.7 |",
        "| χ [eV] | 4.00 | 3.93 | 4.1 | 2.1 |",
        "| ε_r | 9.0 | 8.0 | 10.0 | 10.7 |",
        "| Nc [1/cm³] | 2.2e18 | 3.1e18 | 2e18 | 2.8e19 |",
        "| Nv [1/cm³] | 1.8e19 | 2.5e19 | 1e18 | 1.8e19 |",
        "| μn [cm²/Vs] | 20 | 15 | 100 | 12 |",
        "| μp [cm²/Vs] | 10 | 0.1 | 100 | 25 |",
        "| ND [1/cm³] | 1e21 | 1e19 | 1e9 | 0 |",
        "| NA [1/cm³] | 0 | 0 | 1e9 | 1e15 |",
        "| defect Nt [1/cm³] | 1e15 | 1e14 | 1e14 | 1e14 |",
        "",
        "- 각 층 defect: neutral, single, **midgap**(above Ev = Eg/2), σn=σp=1e-15 cm²",
        "  (논문 미기재 → SCAPS 통상 기본값 가정 — 편차 원인 후보로 기록)",
        "- 흡수: 논문은 문헌 α 파일(ref 46-47, 상세 미공개) — 기본은 층 기본 모델 유지,",
        "  앱 옵션 use_measured_alpha=yes면 MAPbI3에 우리 Phillips α를 스크립트로 주입",
        "- 계면 결함 2곳 (SnO2/MAPbI3 = interface2, MAPbI3/NiOx = interface3):",
        "  neutral, single, **Et = 0.6 eV above highest EV**, σn=σp=1e-19 cm²,",
        "  기준 Nt = 1e10 cm⁻² (논문 스윕 범위 1e10-1e20의 최소 = ALD 무결함 시나리오)",
        "- 접점: 좌우 flat band (back contact 일함수는 논문에서 별도 스윕 — 기준값 미명시 가정)",
        "- 조건: 300 K, AM1.5G 1 sun / Rs·Rsh는 앱이 스크립트로 설정 (1, 1000 Ω·cm²)",
        "",
        f"## 기준 성능 (재현 목표): η {PAPER_REF['eta_pct']}% / Voc {PAPER_REF['Voc_V']} V /",
        f"Jsc {PAPER_REF['Jsc_mA_cm2']} mA/cm² / FF {PAPER_REF['FF_pct']}%",
        "",
        "편차 예상 요인(정직 고지): α 파일 미공개, 벌크 결함 σ·준위 미기재, back contact",
        "일함수 미명시, ITO 광학 — 수 % 수준 편차는 이 가정들의 차이로 해석합니다.",
    ])


def _script_paper(params, cfg):
    aL = int(cfg.get("abs_layer", 3))  # 흡수층 layer 번호 (def 구조에 따름)
    vmax = float(params.get("v_max", 1.4))
    step = float(params.get("v_step", 0.02))
    npts = int(round(vmax / step)) + 1
    rs = float(params.get("rs_ohmcm2", 1.0))
    rsh = float(params.get("rsh_ohmcm2", 1000.0))
    def_name = str(params.get("def_name") or cfg["def"])
    do_qe = str(params.get("do_qe", "yes")) == "yes"
    use_alpha = str(params.get("use_measured_alpha", "no")) == "yes"
    sweep_key = str(params.get("sweep_what", "none"))
    lines = [
        "// SCAPS paper-reproduction script (generated by app, 2026-07-14)",
        "// target: Energies 2023, 16(21), 7438  ITO/SnO2/MAPbI3/NiOx",
        "// grammar sources: bundled examples + manual 10.4 (set external.Rs/Rsh,",
        "// set layerN.absorptionAfile, set interfaceN.IFdefectM.ntotal, action qe.*)",
        "// dialogs freeze CLI automation (2026-07-14: convergence popup waited for OK",
        "// for 5+ min) -> suppress screen/dialogs, route errors to file, blank failed pts",
        "set errorhandling.overwritefile",
        "set errorhandling.outputlist.truncate",
        "set script_display_mode.fully_suppressed",
        "clear all",
        f"load definitionfile {def_name}",
        "clear actions",
        "action light",
        "load spectrumfile AM1_5G 1 sun.spe",
    ]
    # SCAPS는 0/음수를 거부 ("Value not recognised as a positive number",
    # 2026-07-14 실검증) — Rs=0·Rsh=무한은 명령 자체를 생략 (기본 상태가 무저항)
    if rs > 0:
        lines.append(f"set external.Rs {rs:g}")
    if 0 < rsh < 1e28:
        lines.append(f"set external.Rsh {rsh:g}")
    if use_alpha:
        lines.append(f"set layer{aL}.absorptionAfile mapbi3_alpha.abs")
    stab = str(params.get("stabilize", "none"))
    if stab in ("bulk_uniform", "bulk+interface_uniform"):
        # 수치 안정화 (2026-07-14, V=0.80=흡수층 midgap 발산 대응): single 준위의
        # 점유 스위칭을 +-0.1eV 대역으로 분산. 총 Nt 유지 = 재결합 등가.
        # 논문 명시는 single이므로 '수치 등가 근사'로 보고서에 표기할 것.
        lines += [f"set layer{aL}.defect1.uniform", f"set layer{aL}.defect1.Echar 0.1"]
    if stab == "bulk+interface_uniform":
        lines += ["set interface2.IFdefect1.uniform", "set interface2.IFdefect1.Echar 0.1",
                  "set interface3.IFdefect1.uniform", "set interface3.IFdefect1.Echar 0.1"]
    sig = float(params.get("bulk_sigma_cm2", 1e-15))
    if sig != 1e-15:
        # 논문 미기재 벌크 결함 σ — Voc 맞춤 노브 (2026-07-14: σ=1e-15 가정 시 Voc 0.77V
        # vs 논문 1.323V. 논문 Voc는 Eg1.6 복사한계급 → 실효 SRH가 매우 약해야 함)
        lines += [f"set layer{aL}.defect1.capture_cross_section.electrons {sig:g}",
                  f"set layer{aL}.defect1.capture_cross_section.holes {sig:g}"]
    extra = str(params.get("extra_set") or "").strip()
    if extra:  # 진단·미세조정용 자유 set 명령 (세미콜론 구분, 예: layer1.nd 1e19)
        for cmd in extra.split(";"):
            cmd = cmd.strip()
            if cmd:
                lines.append(cmd if cmd.startswith(("set ", "action ", "load "))
                             else f"set {cmd}")
    lines += [
        "action iv.doiv",
        "action iv.startv 0.0000",
        f"action iv.stopv {vmax:.4f}",
        f"action iv.points {npts}",
        "action iv.continueaftervoc",  # Voc 후 정지 방지 (2026-07-14: 0.80V 정지 원인)
    ]
    if do_qe:
        lines += ["action qe.doqe", "action qe.startlambda 300",
                  "action qe.stoplambda 900", "action qe.increment 10"]
    lines += ["calculate singleshot", "save results.iv paper_base.iv"]
    if do_qe:
        lines.append("save results.qe paper_base.qe")
    tmpl = (cfg.get("sweeps") or {}).get(sweep_key)
    values = []
    if tmpl:
        values = [float(v) for v in
                  str(params.get("sweep_values") or "").replace(" ", "").split(",") if v]
        for k, v in enumerate(values, 1):
            for t in tmpl:
                lines.append(t.format(v=v))
            lines.append("calculate singleshot")
            lines.append(f"save results.iv paper_sweep_{k}.iv")
    lines.append("set quitscript.quitSCAPS")
    return "\n".join(lines) + "\n", values


def run_paper(jid, params, log, case):
    """논문 재현 실행: 기준 J-V(+QE) → 논문 지표와 비교표 → (옵션) 논문 그림 스윕.

    설정 주도형 (2026-07-14): PAPERS[case_id]에서 기준지표/def/흡수층 번호/스윕을 조회.
    새 논문 추가 = PAPERS 항목 + case.json + (recipe.md 정적 파일) 만으로 끝.
    """
    cfg = PAPERS.get((case or {}).get("id") or "", PAPERS["scaps_paper_energies2023"])
    ref = cfg["ref"]
    jd = jobs.job_dir(jid)
    if cfg.get("recipe_builder") == "static":
        from .. import library
        src_recipe = library.CASES_DIR / (case or {}).get("id", "") / "recipe.md"
        if src_recipe.exists():
            (jd / "recipe_paper.md").write_text(
                src_recipe.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        (jd / "recipe_paper.md").write_text(_recipe_paper_md(), encoding="utf-8")
    script, values = _script_paper(params, cfg)
    (jd / "batch.script").write_text(script, encoding="ascii")
    if str(params.get("mode", "local")) == "export":
        log("반출용 생성 완료 — recipe_paper.md(1회 GUI 제작) + batch.script")
        return
    exe = find_scaps()
    if not exe:
        raise RuntimeError("SCAPS 실행 파일이 설정되지 않았습니다 — ② 엔진 설정 확인")
    import shutil
    scaps_dir = Path(exe).parent
    def_name = str(params.get("def_name") or cfg["def"])
    if not (scaps_dir / "def" / def_name).exists():
        raise RuntimeError(
            f"{def_name}이 SCAPS def 폴더에 없습니다 — ④의 recipe_paper.md 표대로 GUI에서"
            " 1회 생성·저장 후 다시 실행하세요 (그 뒤로는 전부 자동)")
    try:
        shutil.copy2(jd / "batch.script", scaps_dir / "script" / "app_paper.script")
    except Exception as e:
        log(f"  (script 폴더 복사 실패: {type(e).__name__})")
    n_calc = 1 + len(values)
    log(f"논문 재현 실행: {def_name} / 계산 {n_calc}회 (기준{'+스윕 ' + str(len(values)) + '점' if values else ''})")
    t_start = time.time()
    proc = subprocess.Popen([exe, str(jd / "batch.script")], cwd=str(jd))
    while proc.poll() is None:
        if time.time() - t_start > 120 * n_calc + 180:
            proc.kill()
            log("  SCAPS 타임아웃 — 완료분 수집")
            break
        jobs.check_cancel(jid)
        time.sleep(2)
    log(f"  종료 코드 {proc.returncode} ({time.time()-t_start:.0f}s)")
    import os
    rdirs = [scaps_dir / "results"]
    lad = os.environ.get("LOCALAPPDATA")
    if lad and len(scaps_dir.parts) > 1:
        rdirs.append(Path(lad) / "VirtualStore" / Path(*scaps_dir.parts[1:]) / "results")
    for rdir in rdirs:
        if rdir.exists():
            for f in rdir.glob("paper_*.*"):
                if f.suffix.lower() in (".iv", ".qe") and f.stat().st_mtime >= t_start - 5:
                    shutil.copy2(f, jd / f.name)
    base = jd / "paper_base.iv"
    if not base.exists():
        raise RuntimeError("paper_base.iv가 없습니다 — SCAPSErrorLogFile.log 회신 요청")
    pin = 100.0
    # 기준 J-V (마지막 세그먼트) + 논문 비교표
    arr, hdr = _numeric_block(base.read_text(encoding="utf-8", errors="replace"), log, base.name)
    x = arr[:, 0]
    br = np.where(np.diff(x) < 0)[0] + 1
    idx = (np.split(np.arange(x.size), br) if br.size else [np.arange(x.size)])[-1]
    V, J = x[idx], arr[idx, 1]
    m, Jgen = common.jv_metrics(V, J, pin, log)
    common.plot_jv(jid, [("app 재현", V, Jgen)], "jv_paper.png",
                   "Paper reproduction J-V (Energies 2023, 16, 7438)")
    try:
        _plot_iv_components(jid, "paper_base", arr[idx], hdr, log)
    except Exception:
        pass
    ff_pct = m["FF"] * 100 if m["FF"] == m["FF"] else float("nan")
    comp = [("Jsc [mA/cm2]", ref["Jsc_mA_cm2"], m["Jsc_mA_cm2"]),
            ("Voc [V]", ref["Voc_V"], m["Voc_V"]),
            ("FF [%]", ref["FF_pct"], ff_pct),
            ("PCE [%]", ref["eta_pct"], m["PCE_pct"])]
    log("\n=== 논문 대조 (" + ref["src"] + ") ===")
    with open(jd / "paper_comparison.csv", "w", encoding="utf-8") as fh:
        fh.write("metric,paper,app,diff_pct\n")
        for name, ref, got in comp:
            d = (got - ref) / ref * 100 if ref and got == got else float("nan")
            log(f"  {name}: 논문 {ref} / 재현 {got:.4g} / 편차 {d:+.2f}%")
            fh.write(f"{name},{ref},{got},{d}\n")
    qe = jd / "paper_base.qe"
    if qe.exists():
        try:
            arrq, _h = _numeric_block(qe.read_text(encoding="utf-8", errors="replace"),
                                      log, qe.name)
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(figsize=(6, 4), dpi=110)
            yq = arrq[:, 1]
            ax.plot(arrq[:, 0], yq * (100 if yq.max() <= 1.5 else 1), lw=1.4)
            ax.set_xlabel("wavelength [nm]")
            ax.set_ylabel("QE [%]")
            ax.grid(alpha=0.3)
            ax.set_title("Paper reproduction QE (Fig.3b 대응)")
            fig.tight_layout()
            fig.savefig(jd / "qe_paper.png")
            plt.close(fig)
            log("QE 그래프: qe_paper.png")
        except Exception as e:
            log(f"  (QE 판독 생략: {type(e).__name__})")
    if values:
        rows, plot = [], []
        for k, v in enumerate(values, 1):
            f = jd / f"paper_sweep_{k}.iv"
            if not f.exists():
                rows.append({"value": v, "Jsc_mA_cm2": float("nan"), "Voc_V": float("nan"),
                             "FF": float("nan"), "PCE_pct": float("nan")})
                continue
            arr2, _h2 = _numeric_block(f.read_text(encoding="utf-8", errors="replace"),
                                       log, f.name)
            x2 = arr2[:, 0]
            br2 = np.where(np.diff(x2) < 0)[0] + 1
            i2 = (np.split(np.arange(x2.size), br2) if br2.size else [np.arange(x2.size)])[-1]
            V2, J2 = x2[i2], arr2[i2, 1]
            m2, Jg2 = common.jv_metrics(V2, J2, pin, log)
            rows.append({"value": v, **{k2: m2[k2] for k2 in
                                        ("Jsc_mA_cm2", "Voc_V", "FF", "PCE_pct")}})
            plot.append((f"{v:g}", V2, Jg2))
            log(f"  [스윕 {v:g}] {m2}")
        sweep_key = str(params.get("sweep_what", "none"))
        common.plot_jv(jid, plot, "jv_sweep.png", f"Paper sweep: {sweep_key}")
        _plot_sweep_metrics(jid, sweep_key, rows)
        with open(jd / "sweep_summary.csv", "w", encoding="utf-8") as fh:
            keys = list(rows[0].keys())
            fh.write(",".join(keys) + "\n")
            for r in rows:
                fh.write(",".join(str(r[k]) for k in keys) + "\n")
        log("논문 그림 대응 스윕 그래프: jv_sweep.png + sweep_metrics.png")
    log("논문 재현 완료 — paper_comparison.csv 확인")


def check(jid, params, log):
    """환경 점검: 실행 파일 탐지 + .iv 파서 자기검사 (동글 불필요)."""
    exe = find_scaps()
    if exe:
        log(f"SCAPS 실행 파일 OK: {exe} (CLI 자동 구동·자동 종료 검증 완료 2026-07-14)")
    else:
        log("SCAPS 미탐지 — 겐트대(scaps@elis.ugent.be) 신청 후 설치, ② 엔진 설정에 경로 입력")
        log("  설치 전에도 가능: export(레시피·스크립트 생성) / GUI 계산 결과 .iv 업로드 판독")
    sample = "v(V)\t jtot(mA/cm2)\n 0.0\t-21.0\n 0.5\t-20.5\n 1.0\t-8.0\n 1.1\t 5.0\n"
    arr, _ = _numeric_block(sample, log, "selftest.iv")
    assert arr.shape == (4, 2), "파서 자기검사 실패"
    log(".iv 파서 자기검사 OK")
    log("SCAPS 점검 완료" + ("" if exe else " (실행 파일만 설치되면 끝)"))


def run(jid, params, log, case):
    if (case or {}).get("id") == "scaps_pin_sweep":  # 변수 스윕 배치 (2026-07-14)
        return run_sweep(jid, params, log, case)
    if (case or {}).get("id") in PAPERS:  # 논문 재현 케이스들 (설정 주도형, 2026-07-14)
        return run_paper(jid, params, log, case)
    jd = jobs.job_dir(jid)
    mode = str(params.get("mode", "export"))
    log(f"SCAPS-1D {mode}: n-i-p 레시피 + 스크립트 생성")
    # save는 예제와 동일한 '상대 파일명' 형태 사용 (기본 저장 위치 [scaps]/results,
    # 권한 리다이렉트 포함해 아래 수집 스캔이 회수). 절대경로 동작 여부는 미검증 —
    # 2026-07-14 시험은 리로더 낌으로 옛 스크립트가 실행돼 판정 무효.
    (jd / "recipe.md").write_text(_recipe_md(params, log), encoding="utf-8")
    (jd / "batch.script").write_text(_script(params), encoding="ascii")
    common.write_readme(jid, "SCAPS-1D 사용 절차", [
        "1. (최초 1회) SCAPS GUI에서 recipe.md의 표대로 소자 생성 → `pin_mapi_app.def` 저장",
        "   (SCAPS의 def 폴더 또는 이 작업 폴더 — 스크립트와 같은 곳)",
        "2. 배치 실행: mode=local이면 앱이 자동 구동·회수 (검증 완료 2026-07-14) / 수동은",
        "   SCAPS [Script set-up]→[Execute script] 또는 GUI에서 직접 I-V 계산 후 .iv 저장",
        "3. .iv(및 .qe) 파일을 앱 ③ '결과 가져오기'에 업로드 → 지표·플롯 자동",
        "",
        "이 케이스의 목적: COMSOL IBC 케이스의 계면 재결합(IDL τ=d/S) 구현을",
        "SCAPS의 네이티브 계면 S와 **독립 대조**하는 것 (같은 물성·같은 S).",
    ])
    if mode == "local":
        exe = find_scaps()
        if not exe:
            raise RuntimeError("SCAPS 실행 파일이 설정되지 않았습니다 — ② 엔진 설정에 경로 입력 "
                               "(설치 전이면 export로 레시피만 생성하세요)")
        log(f"  scaps.exe 스크립트 구동 (CLI 인자·자동종료 검증됨 2026-07-14): {exe}")
        import shutil
        scaps_dir = Path(exe).parent
        try:  # GUI Execute script와 같은 조건이 되도록 script 폴더에도 복사
            shutil.copy2(jd / "batch.script", scaps_dir / "script" / "app_batch.script")
            log("  batch.script → SCAPS script 폴더 복사됨 (app_batch.script)")
        except Exception as e:
            log(f"  (script 폴더 복사 실패: {type(e).__name__} — 관리자 권한 필요할 수 있음)")
        t_start = time.time()
        try:
            proc = subprocess.Popen([exe, str(jd / "batch.script")], cwd=str(jd))
            while proc.poll() is None:
                if time.time() - t_start > 600:
                    proc.kill()
                    log("  SCAPS 600s 타임아웃 — 프로세스 종료 후 산출물 수집 시도")
                    break
                jobs.check_cancel(jid)
                time.sleep(2)
            if proc.returncode is not None:
                log(f"  종료 코드 {proc.returncode}")
        except FileNotFoundError:
            raise RuntimeError("scaps.exe 실행 실패 — 경로 확인")
        # 산출물 수집: 기본은 batch.script가 작업 폴더 절대경로로 직접 저장.
        # 폴백으로 SCAPS results 폴더 + Windows 권한 리다이렉트(VirtualStore)도 스캔
        # (2026-07-13: Program Files 하위 쓰기는 관리자 권한 없이는 리다이렉트됨)
        import os
        rdirs = [scaps_dir / "results"]
        lad = os.environ.get("LOCALAPPDATA")
        if lad and len(scaps_dir.parts) > 1:
            rdirs.append(Path(lad) / "VirtualStore"
                         / Path(*scaps_dir.parts[1:]) / "results")
        for rdir in rdirs:
            if rdir.exists():
                for f in rdir.glob("*.iv"):
                    if f.stat().st_mtime >= t_start - 5:
                        shutil.copy2(f, jd / f.name)
                        log(f"  {rdir}에서 회수: {f.name}")
        ivs = sorted(jd.glob("*.iv"))
        if ivs:
            import_results(jid, params, log)
        else:
            log("  ⚠️ .iv 산출물이 없음 — CLI 구동 방식이 다를 수 있음. READ_ME_FIRST.md의 "
                "GUI 절차로 실행 후 '결과 가져오기'를 사용하세요")
    else:
        log("반출용 생성 완료 — recipe.md + batch.script + READ_ME_FIRST.md (④에서 다운로드)")


def _plot_iv_components(jid, stem, arr, header, log):
    """SCAPS .iv의 전류 성분 열(jtot/j_rec/j_gen/jbulk/jifr...)을 V에 대해 플롯."""
    import re as _re
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    jd = jobs.job_dir(jid)
    labels = [t for t in _re.split(r"[\s\t]+", header or "") if t]
    ncol = arr.shape[1]
    if len(labels) < ncol:
        labels = ["v(V)"] + [f"col{i}" for i in range(2, ncol + 1)]
    fig, ax = plt.subplots(figsize=(7, 4.6), dpi=110)
    for i in range(1, min(ncol, 7)):
        ax.plot(arr[:, 0], arr[:, i], lw=1.3, label=labels[i][:24])
    ax.set_xlabel("V [V]")
    ax.set_ylabel("J [mA/cm²]")
    ax.axhline(0, lw=0.6, color="k")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    ax.set_title("SCAPS current components")
    fig.tight_layout()
    fig.savefig(jd / f"components_{stem}.png")
    plt.close(fig)
    log(f"  전류 성분 그래프: components_{stem}.png ({min(ncol,7)-1}개 열)")


# ---------------- .iv/.qe 파서 (즉시 사용 가능 — 관용 텍스트 표 판독) ----------------

def _numeric_block(text, log, fname):
    """파일에서 숫자 표 블록(≥2열)을 추출. 헤더 줄(있으면)도 반환."""
    lines = text.splitlines()
    rows, header = [], ""
    for ln in lines:
        toks = re.split(r"[\s,;\t]+", ln.strip())
        vals = []
        ok = len(toks) >= 2
        for t in toks:
            if not t:
                continue
            try:
                vals.append(float(t.replace("E", "e")))
            except ValueError:
                ok = False
                break
        if ok and len(vals) >= 2:
            rows.append(vals)
        elif not rows and ln.strip():
            header = ln.strip()  # 숫자 블록 직전의 텍스트 줄을 헤더 후보로
    if not rows:
        raise RuntimeError(f"{fname}: 숫자 표를 찾지 못함")
    ncol = min(len(r) for r in rows)
    arr = np.array([r[:ncol] for r in rows], dtype=float)
    log(f"  [{fname}] {arr.shape[0]}행 × {ncol}열 (헤더 후보: {header[:80] or '없음'})")
    return arr, header


def _scaps_footer_metrics(text, log):
    """SCAPS가 파일에 적어주는 지표(Voc, Jsc, FF, eta)가 있으면 회수 (대조용)."""
    out = {}
    for key, pat in [("Voc_V", r"Voc\s*[=:]?\s*([\d.eE+-]+)\s*V"),
                     ("Jsc_mA_cm2", r"Jsc\s*[=:]?\s*([-\d.eE+]+)\s*mA/cm"),
                     ("FF_pct", r"FF\s*[=:]?\s*([\d.eE+-]+)\s*%"),
                     ("eta_pct", r"eta\s*[=:]?\s*([\d.eE+-]+)\s*%")]:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            out[key] = float(m.group(1))
    if out:
        log(f"  SCAPS 자체 지표(파일 기재): {out}")
    return out


def import_results(jid, params, log):
    jd = jobs.job_dir(jid)
    cand = [f for f in sorted(jd.iterdir())
            if f.suffix.lower() in (".iv", ".qe", ".txt", ".dat")
            and f.name not in ("log.txt",)]
    if not cand:
        raise RuntimeError("업로드된 .iv/.qe/.txt 파일이 없습니다")
    curves, qe_done = [], False
    for f in cand:
        text = f.read_text(encoding="utf-8", errors="replace")
        try:
            arr, header = _numeric_block(text, log, f.name)
        except RuntimeError as e:
            log(f"  {e} — 건너뜀")
            continue
        _scaps_footer_metrics(text, log)
        x = arr[:, 0]
        if f.suffix.lower() == ".qe" or (x.min() >= 250 and x.max() > 100):  # λ[nm]로 판단
            qe = arr[:, 1]
            qe = qe / 100.0 if qe.max() > 1.5 else qe  # % → fraction
            am = common.load_am15()
            H, C, Q = 6.62607015e-34, 299792458.0, 1.602176634e-19
            F = np.interp(x, am[:, 0], am[:, 1])
            phi = F * (x * 1e-9) / (H * C)
            jsc_qe = Q * np.trapezoid(qe * phi, x) * 0.1
            log(f"  [{f.name}] QE 스펙트럼: Jsc(QE적분) = {jsc_qe:.2f} mA/cm²")
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(figsize=(6, 4), dpi=110)
            ax.plot(x, qe * 100, lw=1.4)
            ax.set_xlabel("wavelength [nm]")
            ax.set_ylabel("QE [%]")
            ax.grid(alpha=0.3)
            fig.tight_layout()
            fig.savefig(jd / f"qe_{f.stem}.png")
            plt.close(fig)
            qe_done = True
        else:  # I-V: 1열 V, 2열 J[mA/cm²] (SCAPS 관용)
            # SCAPS I-V 패널 save는 쌓인 모든 시뮬레이션(dark 포함)을 한 파일에 이어
            # 저장할 수 있음 (2026-07-13 실검증: 513행 = 여러 스윕 연결) → V가 감소하는
            # 지점에서 스윕 분리, 곡선마다 dark/light 자동 라벨
            br = np.where(np.diff(x) < 0)[0] + 1
            segs = np.split(np.arange(x.size), br) if br.size else [np.arange(x.size)]
            if len(segs) == 1 and arr.shape[1] >= 5:
                # SCAPS와 동일한 전류 성분 그래프 (jtot/j_rec/j_gen/jbulk/jifr —
                # .iv에 열로 저장됨, 2026-07-14): 단일 스윕 파일일 때 자동 생성
                try:
                    _plot_iv_components(jid, f.stem, arr, header, log)
                except Exception as e_c:
                    log(f"  (성분 그래프 생략: {type(e_c).__name__})")
            for k, idx in enumerate(segs, 1):
                if idx.size < 3:
                    continue
                Vk, Jk = x[idx], arr[idx, 1]
                tag = "light" if abs(Jk[np.argmin(np.abs(Vk))]) > 0.5 else "dark"
                name = f.stem if len(segs) == 1 else f"{f.stem}#{k}({tag})"
                if len(segs) > 1:
                    log(f"  [{f.name}] 스윕 {k}/{len(segs)}: {idx.size}점 → {tag}")
                curves.append((name, Vk, Jk))
    if curves:
        pin = 100.0
        plot = []
        for label, V, J in curves:
            o = np.argsort(V)
            m, Jgen = common.jv_metrics(V[o], J[o], pin, log)
            plot.append((label, V[o], Jgen))
            log(f"  [{label}] 앱 계산 지표: {m}")
            log("    (COMSOL 대조 시 주의: SCAPS 조명이 반사 무시 설정이면 COMSOL "
                "beer_lambert 모드와, 반사 포함이면 wave_optics 모드와 비교)")
        common.plot_jv(jid, plot, "jv_scaps.png", "SCAPS-1D J-V")
    if not curves and not qe_done:
        raise RuntimeError("판독된 곡선이 없습니다 — 파일이 SCAPS의 I-V/QE 저장본인지 확인")
    log("SCAPS 결과 판독 완료")