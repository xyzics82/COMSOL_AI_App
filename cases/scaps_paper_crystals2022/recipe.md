# 논문 재현 레시피 — Crystals 2022, 12(1), 68 (FTO/TiO2/MAPbI3/Spiro/Au)

GUI에서 아래 표대로 **4층** 소자를 만들어 `paper_crystals2022.def`로 저장하세요 (1회).
출처: doi:10.3390/cryst12010068 Table 1·2·3 (오픈액세스). 실험-시뮬 동시 검증 모델
(암비언트 공기 제작 소자, MAPbI₃ Eg 1.45 실측). **순정렬 밴드** — energies2023 케이스의
역정렬 발산 문제가 없는 구조입니다.

## 층 (왼쪽=조명 입사부터): FTO / TiO2 / Perovskite / Spiro-OMeTAD

| 항목 | FTO | TiO2(ETL) | Perovskite | Spiro(HTL) |
|---|---|---|---|---|
| thickness [um] | 0.25 | 0.60 | 0.51 | 0.08 |
| Eg [eV] | 3.5 | 3.2 | **1.45** | 2.9 |
| χ [eV] | 4.4 | 4.0 | 3.9 | 2.2 |
| ε_r | 9.0 | 100 | 22 | 3.0 |
| Nc [1/cm³] | 2.2e18 | 1e20 | 3.1e18 | 2.5e20 |
| Nv [1/cm³] | 1.8e19 | 2e20 | 3.1e18 | 2.5e20 |
| vth n,p [cm/s] | 1e7 | 1e7 | 1e7 | 1e7 |
| μn [cm²/Vs] | 2000 | 75 | 10 | 1e-4 |
| μp [cm²/Vs] | 100 | 50 | 10 | 1e-4 |
| ND [1/cm³] | 2e19 | 1e19 | 1e15 | 0 |
| NA [1/cm³] | 0 | 0 | 1e15 | 1e19 |

## 결함 (Table 3)

- **Perovskite 벌크**: neutral, single, **Et = 0.60 eV above Ev**, σn=σp=1e-15 cm²,
  **Nt = 9e16 cm⁻³** (실험 fit값 — 기준 소자. Nt 스윕이 논문 핵심 그림 Fig.3)
- **계면 2곳**:
  - TiO2/Perovskite (interface2): neutral, single, Et 0.60 above Ev, σn=1e-10, σp=2e-15 cm²
  - Perovskite/Spiro (interface3): neutral, single, Et 0.60 above Ev, σn=2e-15, σp=1e-10 cm²
  - 계면 Nt(total)는 논문 미기재 → **1e9 cm⁻² 가정**(기여 최소화 — 논문이 '벌크 재결합
    지배'로 fit했으므로. 편차 시 extra_set로 조정: `interface2.IFdefect1.ntotal 1e10`)

## ⚠️ 정의 패널 필수 설정 (2026-07-15 확정 — 없으면 0.78V 수렴 붕괴)

- **'apply voltage V to' = right contact(back)** / **'current reference' = generator**
- 왼쪽(조명측) 인가+consumer(SCAPS 기본)면 순방향 0.76~0.82V에서 비물리 가지로 붕괴
  (세 논문 소자 모두 실증 — Energies는 이 설정만으로 4지표 1% 이내 재현됨)
- 이미 만든 def는 앱이 `_vright` 수정 사본을 자동 사용

## 접점 (Table 2 — flat band 아님, 일함수 명시!)

- front(왼쪽, FTO 쪽): **일함수 4.06 eV**, Sn=Sp=1e7 cm/s
- back(오른쪽, Au): **일함수 5.10 eV**, Sn=Sp=1e7 cm/s
- GUI에서 flat band 체크 해제 후 Φm 직접 입력

## 조건

- 300 K, AM1.5G 1 sun (앱 스크립트가 자동 설정) / Rs·Rsh: 논문 미기재 → 0, 무한
  (앱 폼 기본 rs=0, rsh=1e30으로 실행 — energies와 다름 주의)
- 흡수: 층 기본 모델 유지 (Perovskite Eg 1.45 sqrt법칙. use_measured_alpha는 이 소자에
  부적합 — 우리 α는 Eg 1.55 MAPbI3라 사용하지 말 것)

## 재현 목표 (논문 시뮬 열 = 실험과 동시 일치)

| 지표 | 실험 | 시뮬(재현 목표) |
|---|---|---|
| Jsc [mA/cm²] | 25.71 | **25.81** |
| Voc [V] | 0.8000 | **0.8001** |
| FF [%] | 42.45 | **42.37** |
| η [%] | 8.73 | **8.75** |

스윕 정량 목표 (Fig.3, Nt 9e16→1e11): Voc 0.8001→0.9037 / FF 42.37→63.68 /
η 8.75→15.87 (1e15: 15.61, 1e14: 15.85) · (Fig.4, 두께 600→200nm): Jsc 25.81→26.97 /
Voc→0.8812 / FF→53.37 / η→12.69
