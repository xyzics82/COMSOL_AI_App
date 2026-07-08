# 멀티 엔진 가이드 — COMSOL 외 시뮬레이터 통합 (2026-07-08)

앱 상단의 프로그램 탭에서 엔진을 고르면 해당 엔진의 케이스만 보입니다.
모든 엔진은 COMSOL과 같은 3-모드: **local**(직접 실행) / **export**(입력 덱 생성) /
**결과 가져오기**(외부 실행 결과 업로드 → 지표·그림).

| 엔진 | 역할 | 설치 | 지금 상태 |
|---|---|---|---|
| COMSOL | 소자 (1D/2D/3D, 파동광학) | 설치됨 | ✅ 검증 완료 (기존) |
| Solcore | 광학 TMM·SQ 한계 (파이썬) | `install_engines.bat` | ✅ **검증 완료** (COMSOL과 A(λ) 4자리 일치) |
| SCAPS-1D | 1D 소자·계면 결함 (문헌 표준) | 겐트대 이메일 신청 | 🟡 레시피·파서 준비 — CLI 구동은 첫 실행 검증 |
| IonMonger | 이온 이동·히스테리시스 (MATLAB) | MATLAB R2026a 설치됨 | ✅ **실행 검증 완료** (2026-07-08: 0.1V/s 역 16.3%/순 8.2%, HI 49.6% — 전형적 히스테리시스. 분석은 안정화 구간 자동 제외) |
| Driftfusion | 시간의존 이온+캐리어 (MATLAB) | MATLAB R2026a 설치됨 | ✅ **실행 검증 완료** (2026-07-08: 역 15.7%/순 6.3%, HI 59.8% — IonMonger와 정성 일치 = 상호 교차검증. calcJ는 "sub" 옵션 확정) |
| QE | 계면·원자 (밴드 오프셋) — 소자 아님 | WSL(Ubuntu)에 설치됨 | ✅ **local(WSL) E2E 검증 완료** (2026-07-08: Si SCF 수렴, E=-22.839Ry — 덱→UPF 자동다운로드→pw.x→판독 클릭 한 번. 배포판 자동 탐지: QE는 'Ubuntu', 기본은 Ubuntu-24.04) |

## 설치 절차

### Solcore (+ ASE, scipy, h5py)
`install_engines.bat` 더블클릭 (앱을 한 번 실행해 .venv가 만들어진 뒤).
설치 후 앱 재시작 → Solcore 탭 초록 점.

### SCAPS-1D
1. scaps@elis.ugent.be 로 신청(연구용 무료) → 받은 압축을 예: `C:\SCAPS` 에 풀기
2. ② 탭 상단 엔진 패널에 scaps 실행 파일 경로 입력 → 저장
3. 설치 전에도: export로 레시피(recipe.md — GUI에 입력할 물성 표) 생성, GUI에서 계산한
   .iv/.qe를 '결과 가져오기'로 업로드하면 지표·그림이 나옵니다 (이 경로는 이미 검증됨)

### IonMonger / Driftfusion (MATLAB)
1. **MATLAB만 설치하면 됩니다** (평가판 30일로 시작 가능: mathworks.com → Get a Trial,
   MathWorks 계정 필요. R2021a 이상 — 평가판은 항상 최신이라 충족).
2. 저장소는 이미 `tools/IonMonger`, `tools/Driftfusion`에 동봉 (2026-07-08 클론) —
   앱이 자동 인식. 다른 위치를 쓰려면 ② 엔진 패널에서 경로 변경.
3. MATLAB이 PATH에 등록되지 않았으면 ② 엔진 패널에 matlab.exe 경로 입력.
4. 드라이버·파라미터 생성기는 **실제 저장소 코드와 대조 완료** (진입점·필드명·단위·
   프로토콜 문법 확정, IonMonger parameters.m은 원본 템플릿을 읽어 값만 치환).
   설치 후 1회 실행으로 수치 sanity(Jsc ~20mA/cm² 크기)만 확인하면 끝.
   실패 시 각 작업 폴더의 READ_ME_FIRST.md에 수동 절차가 있습니다.

### Quantum ESPRESSO (계면 분석 — 반출 전용)
- 이 PC에서는 실행하지 않습니다. 서버(리눅스 권장)에 QE 7.x 설치.
- 의사퍼텐셜: SSSP(materialscloud.org/discover/sssp)에서 원소별 UPF 다운로드.
- 권장 순서: ① `qe_si_smoke` 케이스로 파이프라인 점검(입력 덱 → 서버 실행 →
  .out 업로드 판독) → ② `qe_cif_scf`로 벌크 → ③ `qe_interface_bandoffset`으로
  계면 전위 정렬(ΔV̄). 계면 슬랩 구조(CIF)는 사용자가 준비(VESTA/ASE).
- CIF 업로드: 해당 케이스 선택 후 ③ '결과 가져오기'에 CIF를 올리면 덱 생성으로 동작.

## 물성의 단일 원천

모든 엔진의 물성은 `data/materials.json`(출처 포함)에서 매핑됩니다 — COMSOL과
SCAPS/IonMonger가 **같은 숫자**를 쓰므로 교차 검증이 의미를 가집니다.
계면 재결합 S는 전 엔진 공통 정의(케이스 폼의 s_ifc_cms).

## 교차 검증 지도 (왜 이 조합인가)

- Solcore TMM ↔ COMSOL 파동광학: **완료** (A(λ) 4자리, Jsc 같은 그리드 재현)
- SCAPS 계면 S ↔ COMSOL IDL(τ=d/S): 같은 물성·같은 S로 J-V 대조 (SCAPS 설치 후)
- IonMonger ↔ Driftfusion: 같은 이온 물리의 독립 구현 — 히스테리시스 상호 대조
- QE ΔEc ↔ 소자 모델의 장벽 파라미터: 원자 스케일 → 연속체 모델 입력 (멀티스케일)
