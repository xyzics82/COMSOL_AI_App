"""
Phase 0 스파이크: MPh <-> COMSOL 6.4 검증 + 모델 구조 덤프
- si_solar_cell_1d 예제를 작업 폴더로 복사해 사용 (설치 폴더는 건드리지 않음)
- 각 체크는 독립적으로 try/except: 하나 실패해도 끝까지 진행
- 실행 후 '출력 전체'를 Claude에게 붙여넣을 것

불확실 API는 실행 결과로 확정한다 (이 스크립트 자체가 검증 도구).
"""
import shutil
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work"
WORK.mkdir(exist_ok=True)

COMSOL_APPS = Path(r"C:\Program Files\COMSOL\COMSOL64\Multiphysics_copy1\applications")
SRC_MPH = COMSOL_APPS / "Semiconductor_Module" / "Photonic_Devices_and_Sensors" / "si_solar_cell_1d.mph"

RESULTS = []


def check(name):
    def deco(fn):
        def wrapper(*a, **kw):
            print(f"\n{'='*60}\n[CHECK] {name}\n{'='*60}")
            try:
                out = fn(*a, **kw)
                RESULTS.append((name, "PASS"))
                print(f"[PASS] {name}")
                return out
            except Exception:
                RESULTS.append((name, "FAIL"))
                print(f"[FAIL] {name}")
                traceback.print_exc()
                return None
        return wrapper
    return deco


@check("0. import mph")
def c0():
    import mph  # noqa
    print("mph version:", getattr(mph, "__version__", "?"))
    return mph


@check("1. COMSOL 세션 시작 + 버전")
def c1(mph):
    client = mph.start(cores=2)
    print("COMSOL version:", client.version)
    return client


@check("2. 예제 복사 + 로드")
def c2(client):
    dst = WORK / "si_solar_cell_1d.mph"
    shutil.copy(SRC_MPH, dst)
    model = client.load(str(dst))
    print("loaded:", model.name())
    return model


@check("3. 모델 구조 덤프 (태그/타입/파라미터)")
def c3(model):
    print("--- parameters ---")
    for k, v in model.parameters().items():
        print(f"  {k} = {v}")
    # MPh Node 트리 순회. 그룹명은 MPh 문서 기준, 없으면 건너뜀.
    for group in ["functions", "geometries", "materials", "physics",
                  "meshes", "studies", "solutions", "datasets",
                  "evaluations", "tables", "plots", "exports"]:
        try:
            node = model / group
            print(f"--- {group} ---")
            for child in node.children():
                try:
                    typ = child.type()
                except Exception:
                    typ = "?"
                print(f"  {child.name()}  [tag={child.tag()}, type={typ}]")
                # 물리 피처는 한 단계 더
                if group in ("physics", "studies", "plots"):
                    for sub in child.children():
                        try:
                            styp = sub.type()
                        except Exception:
                            styp = "?"
                        print(f"      - {sub.name()}  [tag={sub.tag()}, type={styp}]")
        except Exception as e:
            print(f"--- {group}: 접근 실패 ({e}) ---")
    # 변수(G_ph) 정의 확인 — java 레이어
    try:
        j = model.java
        vtags = j.variable().tags()
        for t in vtags:
            v = j.variable(str(t))
            names = v.varnames()
            for n in names:
                print(f"  variable {t}: {n} = {v.get(str(n))}")
    except Exception as e:
        print("variables via java: 실패:", e)


@check("4. 파라미터 변경 + 미솔브 저장(save)")
def c4(model):
    model.parameter("V0", "0[V]")
    unsolved = WORK / "si_unsolved_copy.mph"
    model.save(str(unsolved))
    print("saved:", unsolved, unsolved.exists())


@check("5. 솔브 (전체 스터디)")
def c5(model):
    import time
    for s in (model / "studies").children():
        t0 = time.time()
        print(f"solving study: {s.name()} ...")
        model.solve(s.name())
        print(f"  done in {time.time()-t0:.1f}s")


@check("6. 결과 평가 (evaluate)")
def c6(model):
    import numpy as np  # noqa
    # 6a: 도메인 양 평가 (터미널 이름 추측 불필요)
    n = model.evaluate("n")
    print("electron conc array: shape/len =", getattr(n, "shape", len(n)))
    # 6b: 터미널 전류 후보 이름 자동 탐색
    candidates = ["semi.I0_1", "semi.I0_2", "semi.mc1.I0", "semi.mc2.I0",
                  "abs(semi.I0_1)", "semi.V0_1", "semi.V0_2"]
    for expr in candidates:
        try:
            val = model.evaluate(expr)
            print(f"  OK  {expr} -> {val}")
        except Exception as e:
            print(f"  --  {expr}: {type(e).__name__}")
    # 6c: 데이터셋 목록
    try:
        print("datasets:", [d.name() for d in (model / "datasets").children()])
    except Exception as e:
        print("datasets 실패:", e)


@check("7. 솔브된 파일 저장 → 재열기 → 재솔브 없이 평가")
def c7(client, model):
    solved = WORK / "si_solved.mph"
    model.save(str(solved))
    model2 = client.load(str(solved))
    n = model2.evaluate("n")
    print("재열기 후 evaluate OK, len =", getattr(n, "shape", len(n)))


def main():
    print("Python:", sys.version)
    print("작업 폴더:", WORK)
    if not SRC_MPH.exists():
        print("!! 예제 파일 없음:", SRC_MPH)
        print("!! COMSOL 설치 경로가 다르면 이 스크립트 상단 COMSOL_APPS 수정")
        return
    mph = c0()
    if not mph:
        return
    client = c1(mph)
    if not client:
        return
    model = c2(client)
    if model:
        c3(model)
        c4(model)
        c5(model)
        c6(model)
        c7(client, model)

    print(f"\n{'='*60}\n[요약]")
    for name, r in RESULTS:
        print(f"  {r:4s}  {name}")
    print("\n>> 이 출력 전체를 Claude에게 붙여넣어 주세요. <<")


if __name__ == "__main__":
    main()
