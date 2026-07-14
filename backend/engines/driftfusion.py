"""Driftfusion 엔진 — 시간의존 이온+캐리어 1D (Imperial College, MATLAB).

IonMonger과 상보적: Driftfusion은 임의 층 구성·풍부한 해석 함수(dfana)·프로토콜
(doCV/doJV/doTPV 등)이 강점. MATLAB 필요(구입 예정 기준 개발).

전략(임의값 최소화): 저장소가 제공하는 3층 페로브스카이트 입력 CSV 템플릿을 그대로
장전(par = pc(템플릿)) 후, 사용자가 폼에서 바꾼 값만 프로그램적으로 오버라이드.
→ 우리가 CSV 포맷을 창작하지 않아 포맷 리스크가 없음.

✅ 실코드 대조 완료 (2026-07-08, tools/Driftfusion 판독):
  - doCV(sol_ini, light, V0, Vmax, Vmin, scan_rate, cycles, tpoints) 서명 확정
  - dfana(Core/dfana.m): calcVapp(sol), [J,j,x]=calcJ(sol,mesh_option) → J.tot
  - pc.m: d 단위 cm / taun,taup 층 배열 [s] / sn,sp = '계면' 배열(층수-1) [cm s-1]
  - 템플릿 spiro_mapi_tio2.csv 존재 (CSV 열: layer_type,material,thickness,...,
    taun,taup,sn,sp,... — 향후 CSV 직접 생성 시 참조)
  - refresh_device(par) 존재 (Core/)
남은 확인(실행 시): J.tot 절대 단위(A/cm² 가정 — 드라이버가 크기를 로그로 출력해 판정),
equilibrate/doCV 수렴. 참고: 템플릿 물성은 Spiro/MAPI/TiO2 — v0는 두께·τ·S만 오버라이드
(우리 SnO2/NiOx 물성 전체 매핑은 CSV 직접 생성으로 v1에서).
"""
from pathlib import Path

import numpy as np

from .. import jobs
from . import common, find_repo
from .matlab_util import run_matlab
from .ionmonger import _analyze_curves, mstr  # 왕복 스캔 분석·MATLAB 문자열 재사용


def _driver_m(jd, params):
    df = find_repo("driftfusion_path", "Driftfusion")
    t_abs_cm = float(params.get("t_abs_nm", 800)) * 1e-7   # 확정: Driftfusion 길이 단위 cm
    taun = float(params.get("taun_ns", 38.7)) * 1e-9
    s_cms = float(params.get("s_ifc_cms", 1000))           # 확정: par.sn/sp 단위 [cm s-1]
    vmax = float(params.get("v_max", 1.2))
    sr = float(params.get("scan_rate_Vps", 0.1))
    light = float(params.get("light_sun", 1.0))
    pts = int(float(params.get("points", 241)))
    return f"""% driver_app.m — Driftfusion 배치 드라이버 (실코드 대조 확정판, 2026-07-08)
% 확정 근거(tools/Driftfusion 판독): doCV(sol_ini,light,V0,Vmax,Vmin,scan_rate,cycles,tpoints)
%   / dfana.calcVapp(sol) / [J,j,x]=dfana.calcJ(sol,mesh_option) → J.tot(시간×위치)
%   / pc.m: d 단위 cm(d=400e-7), sn/sp = '계면' 재결합 속도 [cm s-1], 길이 = 층수-1
%   / refresh_device(par) 존재. 남은 확인: J.tot 절대 단위(A/cm^2 가정 — 크기 로그로 판정)
JOB = {mstr(str(jd))};
DF  = {mstr(str(df or ''))};
try
    assert(~isempty(DF) && isfolder(DF), 'Driftfusion 폴더가 없습니다');
    cd(DF);
    initialise_df;
    tpl = fullfile(DF,'Input_files','spiro_mapi_tio2.csv');   % 동봉 3층 페로브스카이트
    if ~isfile(tpl)
        cand = dir(fullfile(DF,'Input_files','*mapi*.csv'));
        assert(~isempty(cand), 'Input_files에 3층 템플릿이 없습니다');
        tpl = fullfile(cand(1).folder, cand(1).name);
    end
    fprintf('device template: %s\\n', tpl);
    par = pc(tpl);
    % ----- 오버라이드: 활성층(가운데 층) 두께·SRH τ, 계면 sn/sp
    iabs = ceil(numel(par.d)/2);
    par.d(iabs) = {t_abs_cm:.4g};       % [cm] (800nm = 8e-5cm)
    par.taun(iabs) = {taun:.4g};        % [s]
    par.taup(iabs) = {taun:.4g};
    par.sn(:) = {s_cms:.4g};            % [cm/s] — 계면 배열 (층수-1)
    par.sp(:) = {s_cms:.4g};
    par = refresh_device(par);
    % ----- 평형 → CV 스캔 (0 → Vmax → 0 왕복 1회)
    soleq = equilibrate(par);
    sol_CV = doCV(soleq.ion, {light:g}, 0, {vmax:g}, 0, {sr:g}, 1, {pts});
    V = dfana.calcVapp(sol_CV);
    % 확정(첫 실행 2026-07-08): calcJ의 "whole" 분기는 x를 sub 메시로 덮어써
    % gradient 차원 불일치 오류 — "sub"가 정합 (n/p/a/c도 sub로 변환됨)
    J = dfana.calcJ(sol_CV, "sub");
    Jt = J.tot(:, end);
    fprintf('max|J.tot| = %g (A/cm^2 가정 — mA/cm^2로 20 수준이면 x1e3 환산이 맞음)\\n', max(abs(Jt)));
    writematrix([V(:) Jt(:)*1e3], fullfile(JOB,'jv_out.csv'));   % A/cm^2 → mA/cm^2
    fid = fopen(fullfile(JOB,'RUN_OK.txt'),'w'); fprintf(fid,'ok'); fclose(fid);
catch e
    fid = fopen(fullfile(JOB,'run_error.txt'),'w');
    fprintf(fid, '%s\\n%s', e.identifier, getReport(e,'extended','hyperlinks','off'));
    fclose(fid);
    exit(1);
end
exit(0);
"""


def _gen_deck(jid, params, log):
    jd = jobs.job_dir(jid)
    (jd / "driver_app.m").write_text(_driver_m(jd, params), encoding="utf-8")
    common.write_readme(jid, "Driftfusion 실행 안내", [
        "## 자동 실행이 실패할 때의 수동 절차 (MATLAB)",
        "1. Driftfusion 폴더에서 `initialise_df` 실행",
        "2. `par = pc('Input_files/<3층 페로브스카이트 템플릿>.csv');`",
        "3. 이 폴더 driver_app.m의 오버라이드 블록을 참고해 두께·τ·S 수정",
        "4. `soleq = equilibrate(par);`",
        f"5. `sol = doCV(soleq.ion, {params.get('light_sun', 1)}, 0, {params.get('v_max', 1.2)}, 0, "
        f"{params.get('scan_rate_Vps', 0.1)}, 1, {params.get('points', 241)});`",
        "6. `V=dfana.calcVapp(sol); J=dfana.calcJ(sol);`",
        "   `writematrix([V(:) J.tot(:,end)*0.1],'jv_out.csv')`  % 단위 확인",
        "7. jv_out.csv를 앱 ③ '결과 가져오기'에 업로드",
        "",
        "⚠️ 이 드라이버는 Driftfusion 설치 전 작성된 초안 — 함수 서명은 저장소",
        "   README/도움말과 대조하며 첫 실행 때 확정 (확정 후 완전 자동화).",
    ])
    log("생성: driver_app.m(템플릿 장전+오버라이드) + READ_ME_FIRST.md")
    log("  물성 창작 없음 — 저장소 템플릿 기반, 폼 값만 오버라이드")


def check(jid, params, log):
    """환경 점검: 저장소 파일 + MATLAB -batch 실구동 (동글 불필요, ~1분)."""
    repo = find_repo("driftfusion_path", "Driftfusion")
    if not repo:
        raise RuntimeError("Driftfusion 폴더 없음 — tools/Driftfusion 또는 ② 엔진 설정")
    for f in ("initialise_df.m", "Core/pc.m", "Protocols/doCV.m",
              "Input_files/spiro_mapi_tio2.csv"):
        if (repo / f).exists():
            log(f"저장소 파일 OK: {f}")
        else:
            log(f"⚠️ 저장소에 {f} 없음 — 버전에 따라 이름이 다를 수 있음 (실행 시 자동 탐색)")
    from .matlab_util import check_matlab
    check_matlab(jid, log, jobs.job_dir(jid))
    log("Driftfusion 점검 완료 — local 실행 가능 (검증 이력: 2026-07-08 CV E2E 통과)")


# 스윕 가능한 변수 (2026-07-14 — IonMonger와 동일 UX, 전부 params 경유)
SWEEP_PARAMS = {
    "scan_rate [V/s]": "scan_rate_Vps",
    "taun [ns] (흡수층 SRH)": "taun_ns",
    "S_interface [cm/s] (계면 배열 sn/sp)": "s_ifc_cms",
    "t_abs [nm] (흡수층 두께)": "t_abs_nm",
    "light [sun] (광강도)": "light_sun",
}


def run(jid, params, log, case):
    jd = jobs.job_dir(jid)
    mode = str(params.get("mode", "export"))
    sweep_label = str(params.get("sweep_param") or "").strip()
    sweep_key = SWEEP_PARAMS.get(sweep_label)
    if sweep_key and mode == "local":
        values = [float(x) for x in str(params.get("sweep_values") or "")
                  .replace(" ", "").split(",") if x]
        if not 1 <= len(values) <= 10:
            raise RuntimeError("sweep_values는 1~10개 숫자 (MATLAB 값당 수 분)")
        log(f"Driftfusion 스윕: {sweep_key} ← {values}")
        curves, labels_vals = [], []
        for v in values:
            jobs.check_cancel(jid)
            p2 = dict(params)
            p2[sweep_key] = v
            _gen_deck(jid, p2, log)
            rc = run_matlab(jid, log, jd, "driver_app",
                            timeout_s=int(float(params.get("timeout_min", 30)) * 60))
            err = jd / "run_error.txt"
            if rc != 0 or err.exists():
                detail = (err.read_text(encoding="utf-8", errors="replace")[:800]
                          if err.exists() else f"코드 {rc}")
                raise RuntimeError("Driftfusion 실행 실패 (스윕 중단) — 상세: " + detail)
            arr = np.loadtxt(jd / "jv_out.csv", delimiter=",")
            (jd / f"jv_out_{sweep_key}_{v:g}.csv").write_bytes(
                (jd / "jv_out.csv").read_bytes())
            label = f"{sweep_key}={v:g}"
            curves.append((label, arr[:, 0], arr[:, 1]))
            labels_vals.append((label, v))
            log(f"  {label} 완료 ({len(arr)}점)")
        rows = _analyze_curves(jid, curves, log)
        if len(labels_vals) >= 2:
            from .ionmonger import _plot_sweep_summary
            _plot_sweep_summary(jid, sweep_key, labels_vals, rows, log)
        return
    log(f"Driftfusion {mode}: CV 스캔 {params.get('scan_rate_Vps', 0.1)} V/s, "
        f"0→{params.get('v_max', 1.2)}V→0 왕복")
    _gen_deck(jid, params, log)
    if mode == "export":
        log("반출용 생성 완료 — MATLAB에서 실행 후 jv_out.csv를 ③ '결과 가져오기'로 업로드")
        return
    rc = run_matlab(jid, log, jd, "driver_app",
                    timeout_s=int(float(params.get("timeout_min", 30)) * 60))
    err = jd / "run_error.txt"
    if rc != 0 or err.exists():
        detail = err.read_text(encoding="utf-8", errors="replace")[:800] if err.exists() else f"코드 {rc}"
        raise RuntimeError("Driftfusion 실행 실패 — matlab_console.txt/run_error.txt 확인 후 "
                           "READ_ME_FIRST.md 수동 절차 사용. 상세: " + detail)
    arr = np.loadtxt(jd / "jv_out.csv", delimiter=",")
    _analyze_curves(jid, [("CV", arr[:, 0], arr[:, 1])], log)


def import_results(jid, params, log):
    from .ionmonger import import_results as _imp
    _imp(jid, params, log)  # V,J CSV / .mat 공용 판독 (왕복 스캔 분리 포함)
