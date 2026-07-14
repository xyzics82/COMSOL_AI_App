# 논문 재현 레시피 — Materials Advances (RSC OA), Sn계 PSC (SCAPS 3.3.12 명시)

GUI에서 아래 표대로 **4층** 소자를 만들어 `paper_matadv_sn.def`로 저장하세요 (1회).
출처: pubs.rsc.org Materials Advances "Numerical investigation of high-performance
bilayer tin-based perovskite solar cells with SCAPS-1D" Table 1·3 (오픈액세스).
**목적**: 이 논문은 우리와 같은 SCAPS 3.3.12 사용을 명시 — 우리 설치본에서 0.78V 벽이
전 소자 공통인지, 앞 두 논문(Energies·Crystals) 조합 특이인지 판별하는 기준 소자.

## 층 (왼쪽=조명 입사부터): FTO / PCBM / CsSnI3 / CFTS  (단층 셀 — CsSnCl3 없이)

| 항목 | FTO | PCBM(ETL) | CsSnI3(흡수) | CFTS(HTL) |
|---|---|---|---|---|
| thickness [um] | 0.5 | 0.05 | **1.0** (기준=최적창) | 0.1 |
| Eg [eV] | 3.5 | 2.0 | 1.3 | 1.3 |
| χ [eV] | 4.0 | 3.9 | 3.6 | 3.3 |
| ε_r | 9 | 3.9 | 9.93 | 9 |
| Nc [1/cm³] | 2.2e18 | 2.5e21 | 1e19 | 2.2e18 |
| Nv [1/cm³] | 1.8e19 | 2.5e21 | 1e18 | 1.8e19* |
| μn [cm²/Vs] | 20 | 0.2 | 1500 | 21.98 |
| μp [cm²/Vs] | 10 | 0.2 | 585 | 21.98 |
| NA [1/cm³] | 0 | 0 | **1e21** | 1e18 |
| ND [1/cm³] | 1e21 | 2.93e17 | 0 | 0 |
| defect Nt [1/cm³] | 1e15 | 1e15 | 1e13 | 1e15 |

*CFTS Nv는 표에 "1.819"로 인쇄 — 1.8e19 오식으로 추정 (표기).
CsSnI3 NA=1e21은 표 그대로 (Sn 공석 p+ 관행 — 높지만 원문 유지).

## 결함

- 각 층 벌크: neutral, single, **midgap**(above Ev=Eg/2), σn=σp=1e-15 cm²
  (σ·준위는 논문 미기재 → 통상 가정, 표기)
- 계면 2곳 (PCBM/CsSnI3 = interface2, CsSnI3/CFTS = interface3):
  neutral, σn=σp=**1e-19 cm²**, **Nt = 1e10 cm⁻²**
  (표의 "1×10−10"은 위첨자 렌더 손상 — 1e10 cm⁻²로 추정, 표기.
  분포·기준은 각주 소실 → single, 0.65 eV above highest EV(=CsSnI3 midgap) 가정)

## ⚠️ 정의 패널 필수 설정 (2026-07-15 확정 — 없으면 0.78V 수렴 붕괴)

- **'apply voltage V to' = right contact(back)** / **'current reference' = generator**
- 왼쪽(조명측) 인가+consumer(SCAPS 기본)면 순방향 0.76~0.82V에서 비물리 가지로 붕괴

## 접점

- 논문에 일함수 표 미확인 → **양쪽 flat band** (가정, 표기). back은 Au 통상.
- 접점 S: 기본 1e7 cm/s 유지

## 조건

- 300 K, AM1.5G 1 sun (앱 스크립트 자동) / Rs·Rsh 미기재 → 0·무한 (폼 기본)
- 흡수: 층 기본 모델(sqrt) 유지

## 판별 기준

- **성공** = 스윕이 V≈0.9(Voc) 넘어 진행 + PCE ~15.75%(1.0μm) 근처
  → 설치본 무죄, 앞 두 논문 조합 특이성 확정
- **0.76-0.82V에서 또 죽으면** → 설치본/환경 문제로 확정 (구버전 병렬 설치 단계로)
