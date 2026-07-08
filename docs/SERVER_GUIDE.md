# 서버 컴퓨터에서 무거운 케이스 돌리기 — 완전 가이드

작성 2026-07-08 · 대상: "[서버 전용] IBC 3D 정밀 그리드 (HPC)" 등 export 케이스

## 개요 (두 가지 방법)

| | 방법 A: 솔브 전용 (권장) | 방법 B: 앱 전체 설치 |
|---|---|---|
| 서버에 필요한 것 | COMSOL 6.4+ 와 동글뿐 | + Python 3.12, 첫 실행 시 인터넷 |
| 복사할 것 | unsolved .mph + run_server.bat | comsol_ai_app 폴더 전체 |
| 실행 | run_server.bat 더블클릭 | start.bat 더블클릭 |
| 결과 회수 | *_solved.mph → 이 PC ④에 업로드 | 서버 앱에서 직접 확인 |

## 방법 A — 솔브 전용 (권장)

**1. 이 PC에서 준비**
- ② "[서버 전용] IBC 3D — 정밀 그리드 (HPC)" 케이스 선택 → 조건(W·gap 목록,
  핑거/팁 길이, 메시 hmax, 계면 재결합 S) 확인 → ③ 실행.
  이 케이스는 mode=export만 허용되어 이 PC에서는 솔브하지 않고 파일만 만듭니다.
- ④ 산출물에서 `ibc3d_*_unsolved.mph` 전부 + `run_server.bat` 다운로드.

**2. 서버로 복사**
- USB 등으로 **아무 폴더**나 복사 (예: `D:\comsol_jobs\ibc3d_0708\`).
  조건: unsolved.mph들과 run_server.bat이 **같은 폴더**에 있을 것.

**3. COMSOL 경로 지정 (딱 한 곳)**
- 서버에서 comsolbatch가 PATH에 없으면, `run_server.bat`을 메모장으로 열어
  상단의 `set COMSOLBIN=` 줄에 서버의 COMSOL bin 폴더를 적습니다:
  ```
  set COMSOLBIN=C:\Program Files\COMSOL\COMSOL64\Multiphysics\bin\win64
  ```
  (버전·설치 위치는 서버마다 다름 — comsolbatch.exe가 들어 있는 폴더)
- PATH에 이미 있으면 그대로 두면 됩니다. 잘못돼 있으면 스크립트가
  "[ERROR] comsolbatch not found"로 알려줍니다.

**4. 실행** — run_server.bat 더블클릭. 파일별로 순차 솔브하며 `*_solved.mph` 생성.
동글이 서버에 꽂혀 있어야 합니다.

**5. 회수** — `*_solved.mph`들을 USB로 가져와, 이 PC 앱의 ④ 아래
"solved .mph 업로드 → 결과 추출"에 하나씩 업로드. 재솔브 없이 J-V·지표가 추출됩니다.
(HPC 케이스는 풀 J-V(0~2V)라 Voc/FF/PCE까지 나옵니다.)

## 방법 B — 앱 전체를 서버에 설치

- 복사·설치 절차: `docs/INSTALL_OTHER_PC.md` 참조 (요약: comsol_ai_app 폴더 복사,
  단 .venv/work/.env 제외 → Python 3.12 설치 → start.bat).
- **COMSOL 설치 경로가 다른 서버**: 프로젝트 폴더에 `.env` 파일을 만들고 한 줄 추가:
  ```
  COMSOL_ROOT=C:\Program Files\COMSOL\COMSOL64\Multiphysics
  ```
  (applications 폴더가 들어 있는 상위 폴더. 전체 점검·Si 데모의 예제 경로에 사용.
  케이스 솔브 자체는 MPh가 COMSOL을 자동 탐지하므로 대부분 설정 불필요.)
- 서버 앱에서 같은 케이스를 mode 제약 없이 만들려면 일반 IBC 3D 케이스에서
  jv_mode=full_jv, mesh_hmax_nm을 낮게 설정해 local로 돌리면 됩니다.

## 주의·팁

- **메시를 촘촘하게 쓰는 이유**: 기본(자동) 메시는 Jsc를 ~24% 과소평가하는 것이
  확인됐습니다(2D 수렴 연구 — cases/perovskite_ibc_2d/CASE_SPEC.md 6.9절).
  정량 결과는 hmax≤120nm 필수, 권장 60~100nm. 그래서 이 케이스가 서버 전용입니다.
- HPC 케이스 계산량: 조합 수 × (메시 hmax에 강하게 의존). 참고 실측: 3D 1조합
  hmax 120nm·Jsc 전용이 이 PC에서 ~7분 → hmax 80nm·풀 J-V(41점)는 조합당
  수십 분~시간 단위 가능. 처음엔 1~2조합으로 시간을 가늠하세요.
- run_server.bat은 순차 실행입니다. 서버 코어가 많으면 폴더를 나눠 여러 창에서
  병렬 실행해도 됩니다 (COMSOL 라이선스 동시 실행 수 확인).
- 솔브 파일은 큽니다(조합당 수십 MB~) — USB 용량 확인.
- 문제가 생기면 서버 창의 오류 메시지를 그대로 Claude에게 붙여넣어 주세요.
