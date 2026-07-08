"""IonMonger 엔진 — 이온 공극+캐리어 결합 1D (히스테리시스·임피던스·과도).

COMSOL 소자 모델에 없는 물리(이동 이온)를 담당. MATLAB 필요(평가판으로 시작).

동작:
  export : 작업 폴더에 parameters.m(물성 매핑) + driver_app.m + 절차 안내 생성
  local  : 위 생성 후 matlab -batch로 driver_app 실행 → jv_out.csv 회수·플롯
  import : 수동 실행 결과(jv CSV 또는 simulation.mat) 업로드 → 지표·히스테리시스 플롯

✅ 실코드 대조 완료 (2026-07-08, 저장소 사본 tools/IonMonger 판독):
  - parameters.m은 `function params = parameters(iter)` 형식 → 생성 방식을
    '실제 parameters_template.m을 읽어 값 줄만 정규식 치환'으로 변경 (구조 창작 0)
  - 실행: master.m → params=parameters() → numericalsolver → save(workfolder,'simulation.mat','sol')
  - sol.V [V], sol.J [mA/cm²] (nondimensionalise.m: jay = q*G0*b/10 'to be in mAcm-2'),
    sol.time [s]
  - 프로토콜 문법: applied_voltage = {Vbi, 'tanh',5,V, 'linear',Δt,V, ...} (Δt초 동안 V로)
  - 주의: master.m 첫 줄 `clear;`가 드라이버 변수를 지움 → 경로를 환경변수로 보존
  - 계면 속도 vnE/vpE/vnH/vpH 단위 [m/s] (템플릿 주석 ms-1)
남은 검증(실행 시): MATLAB 설치 후 한 번 돌려 수치 sanity (Jsc ~20mA/cm² 크기).
"""
import re
from pathlib import Path

import numpy as np

from .. import jobs
from . import common, find_repo
from .matlab_util import run_matlab

Q = 1.602176634e-19
KT_Q_298 = 0.025693  # [V] kT/q @ 298K (템플릿 T와 동일 가정 — 확산계수 환산용)


def _subs_map(params, log):
    """materials.json → IonMonger 필드 값 (단위: 실코드 주석 기준 SI)."""
    ab = common.material("mapbi3")["props"]
    et = common.material("sno2")["props"]
    ht = common.material("niox")["props"]
    v = common.si_value
    s_ms = float(params.get("s_ifc_cms", 1000)) * 1e-2  # cm/s → m/s (템플릿 ms-1)
    taun = float(params.get("taun_ns", 38.7)) * 1e-9
    subs = {
        # 흡수층 (MAPbI3)
        "b": f"{float(params.get('t_abs_nm', 800)) * 1e-9:.4g}",
        "epsp": f"{v(ab['epsr']):g}*eps0",
        "Ec": f"{-v(ab['chi']):g}",
        "Ev": f"{-(v(ab['chi']) + v(ab['Eg'])):g}",
        "Dn": f"{v(ab['mun']) * 1e-4 * KT_Q_298:.4g}",
        "Dp": f"{v(ab['mup']) * 1e-4 * KT_Q_298:.4g}",
        "gc": f"{v(ab['Nc']) * 1e6:.4g}",
        "gv": f"{v(ab['Nv']) * 1e6:.4g}",
        # 이온 (N0만 폼 노출 — DI는 템플릿의 Eames 기반 Arrhenius 유지)
        "N0": str(params.get("ion_N0_m3", "1.6e25")).strip(),
        # ETL (SnO2)
        "bE": f"{float(params.get('t_etl_nm', 20)) * 1e-9:.4g}",
        "epsE": f"{v(et['epsr']):g}*eps0",
        "EcE": f"{-v(et['chi']):g}",
        "dE": "1e25",   # ND 1e19 cm^-3 (케이스 사양)
        "gcE": f"{v(et['Nc']) * 1e6:.4g}",
        "DE": f"{v(et['mun']) * 1e-4 * KT_Q_298:.4g}",
        # HTL (NiOx)
        "bH": f"{float(params.get('t_htl_nm', 10)) * 1e-9:.4g}",
        "epsH": f"{v(ht['epsr']):g}*eps0",
        "EvH": f"{-(v(ht['chi']) + v(ht['Eg'])):g}",
        "dH": "1e24",   # NA 1e18 cm^-3 (케이스 사양)
        "gvH": f"{v(ht['Nv']) * 1e6:.4g}",
        "DH": f"{v(ht['mup']) * 1e-4 * KT_Q_298:.4g}",
        # 재결합
        "tn": f"{taun:.4g}",
        "tp": f"{taun:.4g}",
        "beta": "0",
        "vnE": f"{s_ms:.4g}",
        "vpE": f"{s_ms:.4g}",
        "vnH": f"{s_ms:.4g}",
        "vpH": f"{s_ms:.4g}",
        # 배치 실행 설정
        "Verbose": "false",
    }
    if str(params.get("ion_D_m2s", "")).strip():
        subs["DI"] = str(params["ion_D_m2s"]).strip()
    log(f"  물성 매핑 {len(subs)}필드 (materials.json → SI) / S={s_ms:g} m/s (양 계면 4속도)")
    return subs


def _params_m(params, log):
    """실제 parameters_template.m을 읽어 값 줄만 치환 — 구조·문법 창작 없음 (실코드 대조 방식)."""
    repo = find_repo("ionmonger_path", "IonMonger")
    if not repo:
        raise RuntimeError("IonMonger 폴더를 찾을 수 없습니다 — tools/IonMonger 또는 ② 엔진 설정")
    tpl = repo / "parameters_template.m"
    if not tpl.exists():
        raise RuntimeError(f"parameters_template.m이 없습니다: {tpl}")
    src = tpl.read_text(encoding="utf-8", errors="replace")
    unmatched = []
    for key, rhs in _subs_map(params, log).items():
        pat = re.compile(rf"^(\s*{key}\s*=\s*)[^;%\n]*(;)", re.M)
        src, n = pat.subn(rf"\g<1>{rhs}\g<2> % [APP]", src, count=1)
        if n == 0:
            unmatched.append(key)
    # 스캔 프로토콜: 안정화(tanh) 후 Vmax→0→Vmax 왕복 (실코드 문법: 'linear', 소요시간, 목표V)
    vmax = float(params.get("v_max", 1.2))
    scan = float(params.get("scan_rate_Vps", 0.1))
    dur = vmax / scan
    proto = ("applied_voltage = ...\n"
             "    {Vbi, ... % steady-state initial value [APP protocol]\n"
             f"    'tanh', 5, {vmax:g}, ... % preconditioning at Vmax\n"
             f"    'linear', {dur:.6g}, 0, ... % reverse scan Vmax->0 ({scan:g} V/s)\n"
             f"    'linear', {dur:.6g}, {vmax:g} ... % forward scan 0->Vmax\n"
             "    };")
    src, n = re.subn(r"applied_voltage = \.\.\..*?\};", proto, src, count=1, flags=re.S)
    if n == 0:
        unmatched.append("applied_voltage(프로토콜 블록)")
    if unmatched:
        log(f"  ⚠️ 템플릿에서 못 찾은 필드(수동 확인 필요): {unmatched}")
    else:
        log("  parameters.m 생성: 템플릿 전 필드 치환 성공")
    return src


def _driver_m(job_dir):
    repo = find_repo("ionmonger_path", "IonMonger")
    return f"""% driver_app.m — IonMonger 배치 드라이버 (실코드 대조 확정판, 2026-07-08)
% master.m의 `clear;`가 변수를 지우므로 경로는 환경변수로 보존한다.
setenv('APP_JOB', {mstr(str(job_dir))});
setenv('APP_IM', {mstr(str(repo or ''))});
try
    IM = getenv('APP_IM');
    assert(~isempty(IM) && isfolder(IM), 'IonMonger 폴더가 없습니다');
    copyfile(fullfile(getenv('APP_JOB'),'parameters.m'), fullfile(IM,'parameters.m'));
    cd(IM);
    master;   % 확정: params=parameters() → numericalsolver → save('./Data/simulation.mat','sol')
    JOB = getenv('APP_JOB'); IM = getenv('APP_IM');
    L = load(fullfile(IM,'Data','simulation.mat'));
    sol = L.sol;
    V = sol.V(:); J = sol.J(:);   % 확정: V [V], J [mA/cm^2] (nondimensionalise.m jay 스케일)
    writematrix([V J], fullfile(JOB,'jv_out.csv'));
    copyfile(fullfile(IM,'Data','simulation.mat'), fullfile(JOB,'simulation.mat'));
    fprintf('max|J| = %g mA/cm^2 (protocol incl. preconditioning transient)\\n', max(abs(J)));
    fid = fopen(fullfile(JOB,'RUN_OK.txt'),'w'); fprintf(fid,'ok'); fclose(fid);
catch e
    JOB = getenv('APP_JOB');
    fid = fopen(fullfile(JOB,'run_error.txt'),'w');
    fprintf(fid, '%s\\n%s', e.identifier, getReport(e,'extended','hyperlinks','off'));
    fclose(fid);
    exit(1);
end
exit(0);
"""


def mstr(s):
    return "'" + str(s).replace("'", "''") + "'"


def _gen_deck(jid, params, log):
    jd = jobs.job_dir(jid)
    (jd / "parameters.m").write_text(_params_m(params, log), encoding="utf-8")
    (jd / "driver_app.m").write_text(_driver_m(jd), encoding="utf-8")
    common.write_readme(jid, "IonMonger 실행 안내", [
        "이 parameters.m은 IonMonger의 실제 parameters_template.m을 읽어 값 줄만",
        "치환한 것입니다 ([APP] 표시 줄). 구조·문법은 저장소 원본 그대로입니다.",
        "",
        "## 자동 실행(local 모드)이 실패할 때의 수동 절차",
        "1. 이 폴더의 parameters.m을 IonMonger 폴더에 복사 (parameters.m 이름 그대로)",
        "2. MATLAB에서 IonMonger 폴더로 이동 후 master 실행",
        "3. 결과 내보내기: writematrix([sol.V(:) sol.J(:)],'jv_out.csv')",
        "4. jv_out.csv(또는 Data/simulation.mat)를 앱 ③ '결과 가져오기'에 업로드",
        "",
        "✅ 실코드 대조 완료 — 남은 것은 MATLAB 설치 후 실제 1회 실행(수치 sanity)뿐.",
    ])
    log("생성: parameters.m(템플릿 치환) + driver_app.m + READ_ME_FIRST.md")


def run(jid, params, log, case):
    jd = jobs.job_dir(jid)
    mode = str(params.get("mode", "export"))
    scan_list = [float(x) for x in str(params.get("scan_rates_Vps", params.get("scan_rate_Vps", "0.1")))
                 .replace(" ", "").split(",") if x]
    log(f"IonMonger {mode}: 스캔 속도 {scan_list} V/s (이온 이동 → 히스테리시스)")
    if len(scan_list) > 1 and mode == "local":
        log("  여러 스캔 속도는 속도별 순차 실행")
    if mode == "export":
        params = dict(params)
        params["scan_rate_Vps"] = scan_list[0]
        _gen_deck(jid, params, log)
        log("반출용 생성 완료 — MATLAB에서 실행 후 결과를 ③ '결과 가져오기'로 업로드")
        return
    # local: matlab -batch
    curves = []
    for sr in scan_list:
        jobs.check_cancel(jid)
        p2 = dict(params)
        p2["scan_rate_Vps"] = sr
        _gen_deck(jid, p2, log)
        rc = run_matlab(jid, log, jd, "driver_app", timeout_s=int(float(params.get("timeout_min", 30)) * 60))
        err = jd / "run_error.txt"
        if rc != 0 or err.exists():
            detail = err.read_text(encoding="utf-8", errors="replace")[:800] if err.exists() else f"코드 {rc}"
            raise RuntimeError("IonMonger 실행 실패 — matlab_console.txt/run_error.txt 확인 후 "
                               "READ_ME_FIRST.md의 수동 절차 사용. 상세: " + detail)
        arr = np.loadtxt(jd / "jv_out.csv", delimiter=",")
        (jd / f"jv_out_scan{sr:g}.csv").write_bytes((jd / "jv_out.csv").read_bytes())
        curves.append((f"{sr:g} V/s", arr[:, 0], arr[:, 1]))
        log(f"  스캔 {sr:g} V/s 완료 ({len(arr)}점)")
    _analyze_curves(jid, curves, log)


def _split_scan(V, J):
    """전압 프로토콜을 '단조 구간'들로 전부 분리 (안정화/역방향/순방향 등).
    전환점의 dv=0(중복 V)은 직전 부호를 승계 (2026-07-08 단위테스트 교훈).
    첫 실행 교훈(2026-07-08): IonMonger sol에는 안정화(tanh) 구간까지 포함 —
    2개로만 자르면 안정화 과도전류가 스캔에 섞여 지표가 오염(FF>1)됨 → 전 구간 분리."""
    V = np.asarray(V, float)
    J = np.asarray(J, float)
    s = np.sign(np.diff(V))
    for i in range(1, len(s)):
        if s[i] == 0:
            s[i] = s[i - 1]
    turns = list(np.where(s[:-1] * s[1:] < 0)[0] + 1)
    cuts = [0] + turns + [len(V) - 1]
    segs = []
    for a, b in zip(cuts[:-1], cuts[1:]):
        if b - a < 3:
            continue
        direction = "forward(0→V)" if V[b] > V[a] else "reverse(V→0)"
        segs.append((direction, V[a:b + 1], J[a:b + 1]))
    return segs or [("scan", V, J)]


def _analyze_curves(jid, curves, log):
    pin = 100.0
    rows = []
    plot_curves = []
    for label, V, J in curves:
        segs = _split_scan(np.asarray(V, float), np.asarray(J, float))
        if len(segs) >= 3:  # [안정화(초기 과도 포함), 역방향, 순방향, ...] → 안정화 제외
            log(f"  [{label}] 구간 {len(segs)}개 감지 — 첫 구간(안정화/과도)은 지표에서 제외")
            segs = segs[-2:]
        for tag, Vs, Js in segs:
            o = np.argsort(Vs)
            m, Jgen = common.jv_metrics(Vs[o], Js[o], pin, log)
            rows.append((f"{label} {tag}", m))
            plot_curves.append((f"{label} {tag}", Vs[o], Jgen))
            log(f"  [{label} {tag}] {m}")
    if len(rows) >= 2:
        try:
            p1 = rows[-2][1]["PCE_pct"]
            p2 = rows[-1][1]["PCE_pct"]
            hi = (max(p1, p2) - min(p1, p2)) / max(p1, p2) * 100.0
            log(f"  히스테리시스 지수(HI) ≈ {hi:.1f}% (역/순방향 PCE 차/최대)")
        except Exception:
            pass
    common.plot_jv(jid, plot_curves, "jv_hysteresis.png", "IonMonger J-V (scan direction split)")


def import_results(jid, params, log):
    jd = jobs.job_dir(jid)
    csvs = [f for f in sorted(jd.glob("*.csv")) if f.name != "jv_curves.csv"]
    mats = sorted(jd.glob("*.mat"))
    curves = []
    for f in csvs:
        try:
            arr = np.loadtxt(f, delimiter=",")
            if arr.ndim == 2 and arr.shape[1] >= 2:
                curves.append((f.stem, arr[:, 0], arr[:, 1]))
                log(f"[{f.name}] {len(arr)}점 로드 (1열=V, 2열=J[mA/cm²] 가정)")
        except Exception as e:
            log(f"[{f.name}] CSV 판독 실패({type(e).__name__}) — 건너뜀")
    if not curves and mats:
        curves = _load_mat_jv(mats[0], log)
    if not curves:
        raise RuntimeError("판독 가능한 결과(V,J CSV 또는 simulation.mat)가 없습니다 — "
                           "READ_ME_FIRST.md 4번 절차로 CSV를 만들어 업로드하세요")
    _analyze_curves(jid, curves, log)
    log("IonMonger 결과 판독 완료")


def _load_mat_jv(path, log):
    """simulation.mat에서 sol.V/sol.J 추출 시도 (v7 → scipy, v7.3 → h5py)."""
    try:
        from scipy.io import loadmat
        d = loadmat(str(path), squeeze_me=True, struct_as_record=False)
        sol = d.get("sol")
        if sol is not None and hasattr(sol, "V") and hasattr(sol, "J"):
            log(f"[{path.name}] sol.V/sol.J 추출 OK")
            return [("mat", np.ravel(sol.V), np.ravel(sol.J))]
        log(f"[{path.name}] sol.V/sol.J 필드를 찾지 못함 — 변수: {list(d.keys())[:8]}")
    except NotImplementedError:
        log(f"[{path.name}] v7.3(HDF5) 형식 — h5py 시도")
        try:
            import h5py
            with h5py.File(str(path), "r") as h:
                V = np.ravel(h["sol"]["V"][()])
                J = np.ravel(h["sol"]["J"][()])
                return [("mat", V, J)]
        except Exception as e:
            log(f"  h5py 추출 실패({type(e).__name__}) — CSV 경로 사용 권장")
    except ImportError:
        log("scipy 미설치 — install_engines.bat 실행 또는 CSV 업로드 사용")
    except Exception as e:
        log(f"[{path.name}] .mat 판독 실패({type(e).__name__})")
    return []
