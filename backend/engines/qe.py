"""Quantum ESPRESSO 엔진 — 계면·원자 스케일 분석 (소자 시뮬레이션 아님).

역할: 소자 모델(COMSOL/SCAPS 등)에 들어가는 '계면 파라미터'(밴드 오프셋 ΔEc/ΔEv,
전위 정렬)를 원자 스케일에서 계산하기 위한 입력 덱 생성 + 결과 파서.

동작 모드: export 전용 (HPC 케이스와 같은 반출 철학)
  1) 이 앱에서 입력 덱 생성 (pw.x/pp.x/average.x 입력 + 실행 스크립트 + 절차 안내)
  2) 사용자가 서버(또는 QE 설치 PC)에서 실행
  3) 출력(.out, avg.dat)을 ③의 '결과 가져오기'로 업로드 → 파싱·플롯

워크플로우 종류:
  si_smoke    : Si 벌크 SCF — 설치·의사퍼텐셜·실행 경로 전체 점검용 (다이아몬드 구조,
                a=5.431Å 표준값. 기대: 수렴 + 총에너지 출력)
  cif_scf     : 업로드 CIF → SCF (+선택 relax) 입력 생성 (ase 필요)
  bandoffset  : 계면 슬랩의 정전 전위 평균(pp.x plot_num=11 → average.x)으로
                전위 정렬 ΔV̄ 계산 — Van de Walle-Martin 정렬법의 1단계.
                (완전한 ΔEv는 두 벌크의 밴드에지 계산 2건이 추가로 필요 — 절차 안내에 포함)

⚠️ 의사퍼텐셜(UPF)은 라이선스·정확도 때문에 앱이 내려받지 않는다 — SSSP 라이브러리에서
사용자가 받도록 안내(INSTRUCTIONS.md). 입력의 UPF 파일명은 관용 명명이므로 실제 파일명에
맞게 한 줄 수정이 필요할 수 있음(안내 포함).
"""
import os
import re
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

import numpy as np

from .. import jobs
from . import common

# Si 다이아몬드 구조 — 표준 격자상수 5.431 Å (NIST/교과서 값, 스모크 테스트 전용)
SI_A_ANG = 5.431
PSEUDO_CACHE = common.ROOT / "data" / "pseudo"   # 의사퍼텐셜 공용 캐시 (재다운로드 방지)
# QE 공식 의사퍼텐셜 테이블의 직링크 (PSLibrary 1.0.0 — 표준 배포물, 물성 아님)
PSEUDO_URLS = {
    "Si.pbe-n-rrkjus_psl.1.0.0.UPF":
        "https://pseudopotentials.quantum-espresso.org/upf_files/Si.pbe-n-rrkjus_psl.1.0.0.UPF",
}


# ---------------- WSL 실행 (local 모드, 2026-07-08 — 사용자 WSL+QE 설치 후 추가) ----------------

def wsl_exe():
    w = shutil.which("wsl")
    if w:
        return w
    p = Path(r"C:\Windows\System32\wsl.exe")
    return str(p) if p.exists() else None


def _wsl_path(p: Path):
    """D:\\a\\b → /mnt/d/a/b (공백 없는 경로 전제 — 작업 폴더는 항상 무공백)."""
    s = str(Path(p).resolve())
    return "/mnt/" + s[0].lower() + s[2:].replace("\\", "/")


def _wsl_distros():
    """설치된 WSL 배포판 이름 목록 (wsl -l -q 출력은 UTF-16LE — 2026-07-08 확인)."""
    exe = wsl_exe()
    if not exe:
        return []
    try:
        out = subprocess.run([exe, "-l", "-q"], capture_output=True, timeout=30).stdout
        txt = out.decode("utf-16-le", errors="ignore").replace("\x00", "")
        return [ln.strip() for ln in txt.splitlines()
                if ln.strip() and "docker" not in ln.lower()]
    except Exception:
        return []


def _run_wsl(jid, log, jd: Path, bash_cmd: str, timeout_s=1800, distro=None):
    """WSL에서 bash 명령 실행. 콘솔 → wsl_console.txt (wsl.exe 자체 메시지의 UTF-16
    혼입을 피하려고 출력은 파일 리다이렉트 우선, 콘솔은 보조).
    distro: None=기본 배포판, 이름 지정 시 wsl -d <이름> (QE가 비기본 배포판에 있는 경우)."""
    exe = wsl_exe()
    if not exe:
        raise RuntimeError("wsl.exe를 찾을 수 없습니다 — WSL 설치 확인 (관리자 PowerShell: wsl --install)")
    con = jd / "wsl_console.txt"
    dtag = f"[{distro}] " if distro else ""
    log(f"  WSL {dtag}실행: {bash_cmd[:100]}{'...' if len(bash_cmd) > 100 else ''}")
    argv = [exe] + (["-d", distro] if distro else []) + ["-e", "bash", "-lc", bash_cmd]
    proc = subprocess.Popen(argv,
                            stdout=open(con, "w", encoding="utf-8", errors="replace"),
                            stderr=subprocess.STDOUT)
    t0 = time.time()
    try:
        while True:
            rc = proc.poll()
            if rc is not None:
                break
            if time.time() - t0 > timeout_s:
                proc.kill()
                raise RuntimeError(f"WSL 타임아웃({timeout_s}s) — wsl_console.txt 확인")
            try:
                jobs.check_cancel(jid)
            except jobs.Cancelled:
                proc.kill()
                raise
            time.sleep(2.0)
    finally:
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass
    log(f"  WSL 종료 코드 {rc} ({time.time() - t0:.0f}s)")
    return rc


def _ensure_pseudo(jid, names, log):
    """필요 UPF를 캐시(data/pseudo)에서 작업 폴더 pseudo/로. 없으면 알려진 직링크에서
    다운로드(+UPF 헤더 검증·출처 로그 — G-173 자동 다운로드와 같은 정책)."""
    jd = jobs.job_dir(jid)
    (jd / "pseudo").mkdir(exist_ok=True)
    PSEUDO_CACHE.mkdir(parents=True, exist_ok=True)
    ok = True
    for name in names:
        cached = PSEUDO_CACHE / name
        if not cached.exists():
            url = PSEUDO_URLS.get(name)
            if not url:
                log(f"  ⚠️ {name}: 자동 다운로드 링크 미등록 — SSSP에서 받아 data/pseudo/ 또는 "
                    "작업 폴더 pseudo/에 넣어주세요")
                ok = False
                continue
            try:
                log(f"  의사퍼텐셜 다운로드: {url}")
                req = urllib.request.Request(url, headers={"User-Agent": "comsol-ai-app"})
                data = urllib.request.urlopen(req, timeout=60).read()
                head = data[:400].decode("utf-8", errors="replace")
                if len(data) < 10000 or "UPF" not in head:
                    raise RuntimeError(f"UPF 형식 검증 실패 (크기 {len(data)}B)")
                cached.write_bytes(data)
                log(f"  캐시 저장: data/pseudo/{name} ({len(data) // 1024}KB, UPF 헤더 확인)")
            except Exception as e:
                log(f"  ⚠️ 다운로드 실패({type(e).__name__}: {e}) — 수동으로 pseudo/에 넣어주세요")
                ok = False
                continue
        (jd / "pseudo" / name).write_bytes(cached.read_bytes())
    return ok


_PROBE_CMD = ("command -v pw.x && exit 0; "
              "for d in /usr/local/bin $HOME/.local/bin $HOME/q-e*/bin $HOME/qe*/bin "
              "/opt/qe*/bin $HOME/miniconda3/bin $HOME/anaconda3/bin "
              "$HOME/miniconda3/envs/*/bin $HOME/anaconda3/envs/*/bin; do "
              "[ -x \"$d/pw.x\" ] && echo FOUND:$d && exit 0; done; "
              "echo NOTFOUND; echo PATH=$PATH; ls /usr/bin | grep -i '^pw' || true")


def _probe_one(jid, log, jd, distro):
    """한 배포판에서 pw.x 탐색 → (성공?, PATH 프리픽스)."""
    _run_wsl(jid, log, jd, _PROBE_CMD, 180, distro=distro)
    con = (jd / "wsl_console.txt").read_text(encoding="utf-8", errors="replace")
    first = con.strip().splitlines()[0] if con.strip() else ""
    if first.startswith("/") and first.endswith("pw.x"):
        log(f"  pw.x OK{f' [{distro}]' if distro else ''}: {first}")
        return True, ""
    for ln in con.splitlines():
        if ln.startswith("FOUND:"):
            d = ln.split("FOUND:", 1)[1].strip()
            log(f"  pw.x 발견(비표준 위치): {d}")
            return True, f"export PATH='{d}':$PATH && "
    return False, ""


def _probe_wsl_qe(jid, log, jd):
    """pw.x 사전 점검 — 기본 배포판에 없으면 전 배포판 탐색 (2026-07-08 실전 교훈:
    사용자 터미널의 배포판과 wsl 기본 배포판이 다를 수 있음). 반환: (프리픽스, 배포판)."""
    from . import load_settings, save_settings
    s = load_settings()
    saved = s.get("qe_distro", "")
    if saved:
        ok, prefix = _probe_one(jid, log, jd, saved)
        if ok:
            return prefix, saved
        log(f"  저장된 배포판({saved})에서 못 찾음 — 재탐색")
    ok, prefix = _probe_one(jid, log, jd, None)  # 기본 배포판
    if ok:
        return prefix, None
    distros = _wsl_distros()
    log(f"  기본 배포판에 없음 → 설치된 배포판 전체 탐색: {distros}")
    for d in distros:
        ok, prefix = _probe_one(jid, log, jd, d)
        if ok:
            save_settings({"qe_distro": d})
            log(f"  → QE 배포판 확정: {d} (settings.json에 기억)")
            return prefix, d
    con = (jd / "wsl_console.txt").read_text(encoding="utf-8", errors="replace")
    log("  ⚠️ 전 배포판에서 pw.x 미발견 — 마지막 콘솔:\n" + con[-400:])
    raise RuntimeError("어떤 WSL 배포판에서도 pw.x를 찾지 못했습니다. QE를 설치한 터미널에서 "
                       "`which pw.x`와 `echo $WSL_DISTRO_NAME` 결과를 알려주세요")


def _run_local_wsl(jid, params, log):
    """생성된 run_server.sh를 WSL에서 실행 → 출력 자동 판독 (완전 자동 파이프라인)."""
    jd = jobs.job_dir(jid)
    wd = _wsl_path(jd)
    timeout_s = int(float(params.get("timeout_min", 30)) * 60)
    prefix, distro = _probe_wsl_qe(jid, log, jd)
    rc = _run_wsl(jid, log, jd, f"{prefix}cd '{wd}' && bash run_server.sh", timeout_s,
                  distro=distro)
    outs = sorted(jd.glob("*.out"))
    if rc != 0 and not outs:
        con = (jd / "wsl_console.txt")
        tail = con.read_text(encoding="utf-8", errors="replace")[-600:] if con.exists() else ""
        raise RuntimeError("WSL 실행 실패 — QE 설치 확인 (Ubuntu: sudo apt install quantum-espresso). "
                           "콘솔 꼬리:\n" + tail)
    log("  실행 완료 — 출력 자동 판독:")
    _parse_outputs(jid, log)


def _pw_header(prefix, calculation, pseudo_note=""):
    return f"""&CONTROL
  calculation = '{calculation}'
  prefix = '{prefix}'
  outdir = './out'
  pseudo_dir = './pseudo'   ! UPF 파일을 이 폴더에 (INSTRUCTIONS.md 참조){pseudo_note}
  verbosity = 'high'
/
"""


def _si_scf_input():
    a_bohr = SI_A_ANG / 0.529177210903
    return (_pw_header("si_smoke", "scf") + f"""&SYSTEM
  ibrav = 2
  celldm(1) = {a_bohr:.6f}
  nat = 2
  ntyp = 1
  ecutwfc = 40.0
  ecutrho = 320.0
/
&ELECTRONS
  conv_thr = 1.0e-8
/
ATOMIC_SPECIES
  Si  28.0855  Si.pbe-n-rrkjus_psl.1.0.0.UPF
ATOMIC_POSITIONS crystal
  Si 0.00 0.00 0.00
  Si 0.25 0.25 0.25
K_POINTS automatic
  8 8 8 0 0 0
""")


def _pp_vtot_input(prefix):
    """pp.x: 정전 전위(V_bare+V_H, plot_num=11) 3D 큐브 → 후처리용."""
    return f"""&INPUTPP
  prefix = '{prefix}'
  outdir = './out'
  filplot = 'vtot.pp'
  plot_num = 11
/
&PLOT
  iflag = 3
  output_format = 6
  fileout = 'vtot.cube'
/
"""


def _average_input(idir=3, awin_ang=5.0):
    """average.x: 면내 평균 → 1D 전위 프로파일(avg.dat). idir=적층 방향(3=z).
    awin: 매크로 평균 창(Å) — 각 재료의 면간 주기와 비슷하게 조정 권장."""
    return f"""1
vtot.pp
1.0
1000
{idir}
{awin_ang:.3f}
"""


def _nproc(params):
    return max(1, int(float(params.get("nproc", min(8, os.cpu_count() or 4)))))


def _copy_cached_pseudos(jid, species, log):
    """캐시(data/pseudo)에 있는 원소별 UPF를 작업 폴더 pseudo/로 복사 (CIF 흐름 보조)."""
    jd = jobs.job_dir(jid)
    (jd / "pseudo").mkdir(exist_ok=True)
    for el in species:
        hits = sorted(PSEUDO_CACHE.glob(f"{el}.*UPF")) + sorted(PSEUDO_CACHE.glob(f"{el}.*upf"))
        if hits:
            (jd / "pseudo" / hits[0].name).write_bytes(hits[0].read_bytes())
            log(f"  캐시 UPF 복사: {hits[0].name} (⚠️ .in의 ATOMIC_SPECIES 파일명 일치 확인)")
        else:
            log(f"  {el}: 캐시에 UPF 없음 — SSSP에서 받아 pseudo/에 넣어주세요")


def _run_script(files, mpi_np=8):
    sh = ["#!/bin/bash", "# QE 실행 (리눅스 서버). pw.x/pp.x/average.x가 PATH에 있어야 함",
          "set -e", "mkdir -p out pseudo"]
    bat = ["@echo off", "rem QE 실행 (Windows용 QE 설치 시). PATH에 pw.exe 필요",
           "if not exist out mkdir out", "if not exist pseudo mkdir pseudo"]
    for f, prog in files:
        sh.append(f"mpirun -np {mpi_np} {prog} -in {f} | tee {f.replace('.in', '.out')}")
        bat.append(f"{prog.replace('.x', '.exe')} -in {f} > {f.replace('.in', '.out')} 2>&1")
    sh.append("echo DONE")
    bat += ["echo DONE", "pause"]
    return "\n".join(sh) + "\n", "\r\n".join(bat) + "\r\n"


def _instructions(kind, extra_lines=()):
    L = [
        "## 실행 절차 (서버 또는 QE 설치 PC)",
        "1. 이 폴더 전체를 서버로 복사.",
        "2. 의사퍼텐셜: https://www.materialscloud.org/discover/sssp 에서 SSSP Efficiency",
        "   세트를 받아 필요한 원소의 .UPF 파일을 `pseudo/` 폴더에 넣기.",
        "   ⚠️ 입력(.in)의 ATOMIC_SPECIES에 적힌 UPF 파일명이 실제 파일명과 다르면",
        "   파일명에 맞게 그 줄만 수정 (관용 명명으로 생성했음).",
        "3. 리눅스: `bash run_server.sh` / Windows QE: `run_server.bat` 더블클릭.",
        "4. 생성된 `*.out`(+ bandoffset이면 `avg.dat`)을 앱 ③의 '결과 가져오기'에 업로드.",
        "",
        "## 필요한 것",
        "- Quantum ESPRESSO 7.x (pw.x" + (", pp.x, average.x" if kind == "bandoffset" else "") + ")",
        "- 계산량: Si 스모크는 노트북 수 분 / 계면 슬랩은 코어 수·원자 수에 따라 수 시간~",
    ]
    return list(L) + list(extra_lines)


def _gen_si_smoke(jid, params, log):
    jd = jobs.job_dir(jid)
    (jd / "01_scf.in").write_text(_si_scf_input(), encoding="utf-8")
    _ensure_pseudo(jid, ["Si.pbe-n-rrkjus_psl.1.0.0.UPF"], log)
    sh, bat = _run_script([("01_scf.in", "pw.x")], mpi_np=_nproc(params))
    (jd / "run_server.sh").write_text(sh, encoding="utf-8", newline="\n")
    (jd / "run_server.bat").write_text(bat, encoding="utf-8")
    common.write_readme(jid, "QE Si 스모크 테스트 (설치 점검)", _instructions("scf", [
        "", "## 판정", "- `01_scf.out`에 `!    total energy` 줄과 `convergence has been achieved`가",
        "  나오면 정상 — 업로드하면 앱이 자동 판독합니다.",
        "- 필요 UPF: Si 1개 (SSSP: Si.pbe-n-rrkjus_psl.1.0.0.UPF)"]))
    log("Si 스모크 덱 생성: 01_scf.in + run_server.sh/bat + READ_ME_FIRST.md")
    log("  (다이아몬드 Si, a=5.431Å 표준값, ecutwfc 40Ry, k 8×8×8 — 설치 점검 전용)")


def _gen_from_cif(jid, params, log, relax=False):
    jd = jobs.job_dir(jid)
    cifs = sorted(jd.glob("*.cif"))
    from .. import data_prep  # noqa: F401 (향후 CIF 보관함 연동 자리)
    if not cifs:
        raise RuntimeError("CIF 파일이 없습니다 — ③ '결과 가져오기'가 아니라 이 케이스 폼의 "
                           "안내대로 CIF를 함께 업로드하거나(현재는 export 후 폴더에 직접 추가), "
                           "cif_scf 케이스는 ③의 '구조 파일 업로드'를 사용하세요")
    try:
        from ase.io import read as ase_read
    except ImportError:
        raise RuntimeError("CIF 변환에는 ase가 필요합니다 — install_engines.bat 실행 후 재시도")
    atoms = ase_read(str(cifs[0]))
    cell = atoms.cell[:]
    species = sorted(set(atoms.get_chemical_symbols()))
    kind = "vc-relax" if relax else "scf"
    lines = [_pw_header(Path(cifs[0]).stem, kind)]
    lines.append(f"""&SYSTEM
  ibrav = 0
  nat = {len(atoms)}
  ntyp = {len(species)}
  ecutwfc = 50.0
  ecutrho = 400.0
  occupations = 'smearing'
  smearing = 'gaussian'
  degauss = 0.01
/
&ELECTRONS
  conv_thr = 1.0e-8
  mixing_beta = 0.4
/
""")
    if relax:
        lines.append("&IONS\n/\n&CELL\n/\n")
    lines.append("CELL_PARAMETERS angstrom")
    for v in cell:
        lines.append(f"  {v[0]:.8f} {v[1]:.8f} {v[2]:.8f}")
    lines.append("ATOMIC_SPECIES")
    from ase.data import atomic_masses, atomic_numbers
    for s in species:
        lines.append(f"  {s} {atomic_masses[atomic_numbers[s]]:.4f} {s}.upf   "
                     f"! SSSP 파일명으로 교체")
    lines.append("ATOMIC_POSITIONS angstrom")
    for a in atoms:
        lines.append(f"  {a.symbol} {a.position[0]:.6f} {a.position[1]:.6f} {a.position[2]:.6f}")
    lines.append("K_POINTS automatic\n  4 4 1 0 0 0   ! 슬랩 가정(z 성김) — 벌크면 4 4 4로")
    (jd / "01_scf.in").write_text("\n".join(lines) + "\n", encoding="utf-8")
    sh, bat = _run_script([("01_scf.in", "pw.x")], mpi_np=_nproc(params))
    (jd / "run_server.sh").write_text(sh, encoding="utf-8", newline="\n")
    (jd / "run_server.bat").write_text(bat, encoding="utf-8")
    _copy_cached_pseudos(jid, species, log)
    common.write_readme(jid, f"QE {kind} — {cifs[0].name}", _instructions("scf", [
        "", f"- 원소 {', '.join(species)}의 UPF를 pseudo/에 넣고 ATOMIC_SPECIES의 파일명 확인",
        "- ⚠️ ecutwfc/k그리드는 보수적 기본값 — 원소에 따라 SSSP 권장값으로 조정"]))
    log(f"CIF→pw.x 입력 생성: {cifs[0].name} ({len(atoms)}원자, {', '.join(species)})")


def _gen_bandoffset(jid, params, log):
    """계면 슬랩 전위 정렬 덱: scf → pp(plot_num=11) → average.x. CIF 필요."""
    jd = jobs.job_dir(jid)
    _gen_from_cif(jid, params, log, relax=False)  # 01_scf.in 생성 (계면 슬랩 CIF)
    prefix = sorted(jd.glob("*.cif"))[0].stem
    (jd / "02_pp_vtot.in").write_text(_pp_vtot_input(prefix), encoding="utf-8")
    idir = int(float(params.get("stack_dir", 3)))
    awin = float(params.get("avg_window_ang", 5.0))
    (jd / "03_average.in").write_text(_average_input(idir, awin), encoding="utf-8")
    sh, bat = _run_script([("01_scf.in", "pw.x"), ("02_pp_vtot.in", "pp.x")],
                          mpi_np=_nproc(params))
    # average.x는 표준입력 사용 — 스크립트에 별도 줄
    sh = sh.replace("echo DONE", "average.x < 03_average.in | tee 03_average.out\necho DONE")
    bat = bat.replace("echo DONE", "average.exe < 03_average.in > 03_average.out 2>&1\r\necho DONE")
    (jd / "run_server.sh").write_text(sh, encoding="utf-8", newline="\n")
    (jd / "run_server.bat").write_text(bat, encoding="utf-8")
    common.write_readme(jid, "QE 계면 밴드 오프셋 — 전위 정렬(1/2단계)", _instructions("bandoffset", [
        "",
        "## 이 덱이 계산하는 것 (Van de Walle–Martin 정렬법)",
        "- 계면 슬랩의 평면 평균 정전 전위 V̄(z) (avg.dat) → 두 재료 영역의 플래토 차 ΔV̄",
        "- 업로드하면 앱이 V̄(z) 플롯 + ΔV̄를 자동 계산합니다 (플래토 구간은 수동 조정 가능)",
        "",
        "## 완전한 ΔEv/ΔEc까지 가려면 (2/2단계 — 벌크 2건 추가)",
        "- 각 벌크의 (E_VBM − V̄_bulk)를 같은 방법으로 계산: cif_scf 케이스로 벌크 2건 실행",
        "- ΔEv = (E_VBM−V̄)_B − (E_VBM−V̄)_A + ΔV̄, ΔEc = ΔEv + (Eg_B − Eg_A)",
        "- ⚠️ 표준 DFT(PBE)는 Eg 과소평가 — 오프셋 부호·경향 판단용, 정량은 HSE/GW 필요",
        "",
        f"- average.x 창(awin)={awin}Å, 적층방향 idir={idir} — 03_average.in에서 조정 가능"]))
    log("계면 전위 정렬 덱 생성: scf + pp(plot_num=11) + average.x + 절차 안내")
    log("  ⚠️ 계면 슬랩 CIF는 사용자가 준비 (구조 생성은 이 앱 범위 밖 — VESTA/ASE 활용)")


def run(jid, params, log, case):
    kind = case.get("qe_kind") or params.get("qe_kind") or "si_smoke"
    mode = str(params.get("mode", "export"))
    log(f"QE 워크플로우: {kind} (mode={mode})")
    if kind == "si_smoke":
        _gen_si_smoke(jid, params, log)
    elif kind == "cif_scf":
        _gen_from_cif(jid, params, log, relax=str(params.get("relax", "no")) == "yes")
    elif kind == "bandoffset":
        _gen_bandoffset(jid, params, log)
    else:
        raise RuntimeError(f"알 수 없는 QE 워크플로우: {kind}")
    if mode == "local":  # WSL에서 즉시 실행 → 자동 판독 (2026-07-08)
        _run_local_wsl(jid, params, log)
    else:
        log("반출용 생성 완료 — ④ 산출물에서 내려받아 서버에서 실행 후, 출력(.out/avg.dat)을 "
            "③ '결과 가져오기'로 업로드하세요")


# ---------------- 결과 파서 (import) ----------------

def _parse_pw_out(text, log):
    """pw.x 출력에서 핵심 판독: 수렴, 총에너지, 페르미/HOMO, 밴드갭 추정."""
    out = {}
    if "convergence has been achieved" in text or "End of self-consistent calculation" in text:
        out["converged"] = True
    m = re.findall(r"!\s+total energy\s+=\s+([-\d.]+)\s+Ry", text)
    if m:
        out["total_energy_Ry"] = float(m[-1])
    m = re.search(r"the Fermi energy is\s+([-\d.]+)\s+ev", text)
    if m:
        out["fermi_eV"] = float(m.group(1))
    m = re.search(r"highest occupied, lowest unoccupied level \(ev\):\s+([-\d.]+)\s+([-\d.]+)", text)
    if m:
        vbm, cbm = float(m.group(1)), float(m.group(2))
        out["VBM_eV"], out["CBM_eV"], out["gap_eV"] = vbm, cbm, round(cbm - vbm, 4)
    m = re.search(r"highest occupied level \(ev\):\s+([-\d.]+)", text)
    if m:
        out["VBM_eV"] = float(m.group(1))
    if "JOB DONE" in text:
        out["job_done"] = True
    for k, v in out.items():
        log(f"  {k}: {v}")
    if not out:
        log("  ⚠️ pw.x 표식을 찾지 못함 — 파일이 pw.x 표준출력인지 확인")
    return out


def _parse_avg_dat(jid, path, log, win_frac=(0.15, 0.35, 0.65, 0.85)):
    """average.x의 avg.dat: (z, 평면평균, 매크로평균). 두 플래토 창 평균차 ΔV̄."""
    arr = np.loadtxt(path)
    z, vmac = arr[:, 0], arr[:, 2] if arr.shape[1] >= 3 else arr[:, 1]
    n = len(z)
    i1, i2, i3, i4 = (int(f * n) for f in win_frac)
    vA = float(np.mean(vmac[i1:i2]))
    vB = float(np.mean(vmac[i3:i4]))
    dv = vB - vA
    log(f"  전위 정렬: V̄_A={vA:.4f} / V̄_B={vB:.4f} (Ry 단위면 ×13.606eV) → ΔV̄={dv:.4f}")
    log("  ⚠️ 플래토 창은 기본 15-35% / 65-85% 구간 — 그림을 보고 창이 재료 중앙에 오는지 확인")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(6.6, 4.0), dpi=110)
    ax.plot(z, arr[:, 1], lw=0.7, alpha=0.6, label="planar avg")
    ax.plot(z, vmac, lw=1.6, label="macroscopic avg")
    for a, b, c in ((i1, i2, "tab:blue"), (i3, i4, "tab:red")):
        ax.axvspan(z[a], z[min(b, n - 1)], color=c, alpha=0.12)
    ax.set_xlabel("z")
    ax.set_ylabel("V (electrostatic)")
    ax.set_title(f"Potential alignment: ΔV̄ = {dv:.4f}")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(jobs.job_dir(jid) / "potential_alignment.png")
    plt.close(fig)
    return {"V_A": vA, "V_B": vB, "dV": dv}


def import_results(jid, params, log):
    jd = jobs.job_dir(jid)
    # CIF가 업로드된 경우 = 결과 판독이 아니라 '구조 → 입력 덱 생성' 요청으로 처리
    # (케이스 폼에서 cif_scf/bandoffset 케이스를 고른 뒤 '결과 가져오기'에 CIF를 올리는 흐름)
    if sorted(jd.glob("*.cif")):
        kind = params.get("qe_kind") or "cif_scf"
        log(f"CIF 업로드 감지 → QE 입력 덱 생성 모드: {kind}")
        if kind == "bandoffset":
            _gen_bandoffset(jid, params, log)
        else:
            _gen_from_cif(jid, params, log, relax=str(params.get("relax", "no")) == "yes")
        if str(params.get("mode", "export")) == "local" and wsl_exe():
            log("mode=local — WSL에서 바로 실행합니다")
            _run_local_wsl(jid, params, log)
        else:
            log("반출용 생성 완료 — ④ 산출물에서 내려받아 서버 실행 후 출력을 다시 업로드하세요")
        return
    _parse_outputs(jid, log)


def _parse_outputs(jid, log):
    jd = jobs.job_dir(jid)
    found = False
    for f in sorted(jd.glob("*.out")) + sorted(jd.glob("*.txt")):
        if f.name in ("log.txt", "matlab_console.txt", "wsl_console.txt"):
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        if "PWSCF" in text or "Program PWSCF" in text:
            log(f"[{f.name}] pw.x 출력 판독:")
            _parse_pw_out(text, log)
            found = True
    for f in sorted(jd.glob("avg*.dat")) + sorted(jd.glob("*.dat")):
        try:
            log(f"[{f.name}] average.x 프로파일 판독:")
            _parse_avg_dat(jid, f, log)
            found = True
            break
        except Exception as e:
            log(f"  {f.name}: 프로파일 형식 아님({type(e).__name__}) — 건너뜀")
    if not found:
        raise RuntimeError("판독 가능한 QE 출력(.out에 PWSCF 표식 / avg*.dat)이 없습니다")
    log("QE 결과 판독 완료")
