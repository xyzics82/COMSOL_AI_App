# Figure/SI 재현 맵

아래의 `논문값 준비`는 원문에 보고된 비교 목표가 있다는 뜻이며, solver가 준비됐다는
뜻이 아니다. raw curve가 없는 그림의 육안 판독값은 solver 입력으로 사용하지 않는다.

| 연구 | 원문 패널 | 주요 출력 | 입력 상태 | solver 상태 |
|---|---|---|---|---|
| `fig1_optics` | Fig. 1, S1-S4 | 2D G, Abs/R/Para spectra, Jph | 대표 current budget 준비; 전 층 n,k/PML 누락 | 차단 |
| `baseline_jv` | Fig. 3c, Table S1 | JV, Jsc, Voc, FF, PCE | Table S1 준비; G(x,y), 전압/solver 누락 | 차단 |
| `fig2_electrical_sweeps` | Fig. 2, S5-S11 | Dit/Stop/Nt/mobility/light sweeps | 범위 준비; trap mapping와 exact grid 누락 | 차단 |
| `fig3_transport` | Fig. 3, S12-S15 | 재결합 분율, Jn/Jp, potential | 패널 정의 준비; MP/OC bias/API 누락 | 차단 |
| `fig4_doping` | Fig. 4, S16-S17 | doping JV/PCE/Jsc/FF | caption subset 준비; broad grid 누락 | 차단 |
| `fig5_band_offsets` | Fig. 5, Table S2 | CBO/VBO heatmap, material markers | Table S2 결과 준비; 전체 물성/grid 누락 | 참조표만 가능 |
| `fig6_ions` | Fig. 6, S18-S20 | ion/PCE/profile/maps | 농도와 cutline 준비; ion mobility/BC/API 누락 | 차단 |
| `fig7_photon_recycling` | Fig. 7, S21-S22 | Pe/Pa/Pr, PCE map/JV/roadmap | 대표 결과 준비; spectral coupling/case4 누락 | 차단 |

## SI 전수 라우팅

- S1-S4: optical baseline/optimization/parasitic/sandwich
- S5-S11: interface, top surface, mobility, bulk trap, light-intensity 보조 지표
- S12-S15: SC/MP/OC recombination, current, potential maps
- S16-S17: perovskite와 transport-layer doping 보조 지표
- S18-S20: ion cutline, potential, anion/cation maps
- S21-S22: poor-interface photon recycling과 roadmap JV
- Table S1: electrical material inputs. 마지막 `Na=1e20 cm^-3` 중복 행은 오탈자
  의미가 해결되지 않아 사용하지 않는다.
- Table S2: CBO/VBO/material-combination PCE reference table. 다른 물성은 제공하지 않는다.

## acceptance 순서

1. geometry-only topology/치수 육안 검토
2. Fig. 1 optical current budget: 24.26 / 2.50 / 0.73 mA/cm2
3. high-quality baseline JV/PCE; 보고된 25.4%에 맞추기 위한 임의 튜닝 금지
4. 각 sweep의 정량 오차와 물리 경향을 별도로 기록
5. ion과 photon recycling은 누락 입력을 문헌으로 보완한 뒤 별도 validation
