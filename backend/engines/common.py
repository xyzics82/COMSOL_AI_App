"""엔진 공용 유틸 — J-V 지표(COMSOL 러너와 동일 정의 재사용), 플롯, 재료 조회."""
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"


def jv_metrics(V, J_mA_cm2, pin_mw_cm2, log):
    """comsol_cases._metrics와 동일 정의 (부호 자동, Voc 보간, Voc 없으면 PCE NaN).
    입력이 이미 전류밀도(mA/cm²)라 면적 변환 없이 위임한다."""
    from .. import comsol_cases
    V = np.asarray(V, dtype=float)
    J = np.asarray(J_mA_cm2, dtype=float)
    # _metrics는 (V, I[A], area, pin) 서명 — J[mA/cm²]를 area=1000cm², I=J*1 형태로 맞춤
    return comsol_cases._metrics(V, J / 1000.0, 1.0, pin_mw_cm2, log)


def plot_jv(jid, curves, fname="jv.png", title="J-V"):
    """curves: [(label, V, Jgen)] → png + 재플롯용 jv_curves.csv (기존 ④ 재플롯 컨트롤 호환)."""
    from .. import jobs
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    jd = jobs.job_dir(jid)
    fig, ax = plt.subplots(figsize=(6, 4.2), dpi=110)
    rows = []
    for label, V, J in curves:
        ax.plot(V, J, lw=1.4, label=str(label))
        for v, j in zip(V, J):
            rows.append(f"{label},{v:.6g},{j:.6g}")
    ax.set_xlabel("V [V]")
    ax.set_ylabel("J [mA/cm$^2$]")
    ax.set_title(title)
    ax.grid(alpha=0.3)
    ax.axhline(0, color="k", lw=0.6)
    if len(curves) > 1:
        ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(jd / fname)
    plt.close(fig)
    (jd / "jv_curves.csv").write_text("label,V,J\n" + "\n".join(rows), encoding="utf-8")


def material(mat_id, version=None):
    """data/materials.json에서 물성 dict(props+refs) 반환 — 모든 엔진의 단일 물성 원천."""
    m = json.loads((DATA / "materials.json").read_text(encoding="utf-8"))
    if mat_id not in m:
        raise RuntimeError(f"materials.json에 없는 재료: {mat_id}")
    entry = m[mat_id]
    v = version or entry["default_version"]
    return entry["versions"][v]


def si_value(s):
    """'1.55[V]' / '2.75e18[1/cm^3]' / '10' 형태에서 숫자만 (단위는 호출부가 해석)."""
    s = str(s)
    return float(s.split("[")[0])


def load_am15():
    """AM1.5 데이터셋 (λ[nm], W/m²/nm) — data_prep 레지스트리 경유."""
    from .. import data_prep
    d = data_prep.dataset("am15")
    return np.loadtxt(DATA / d["file"], encoding="utf-8")


def load_nk(did="mapbi3_nk"):
    """n,k 데이터셋 (λ[um], n, k)."""
    from .. import data_prep
    d = data_prep.dataset(did)
    return np.loadtxt(DATA / d["file"], encoding="utf-8")


def write_readme(jid, title, lines):
    from .. import jobs
    p = jobs.job_dir(jid) / "READ_ME_FIRST.md"
    p.write_text(f"# {title}\n\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return p
