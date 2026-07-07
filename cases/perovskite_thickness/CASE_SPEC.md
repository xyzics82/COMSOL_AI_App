# 케이스 사양서: 페로브스카이트 태양전지 두께 스윕

- 버전 v0.1 (2026-07-06) · 상태: **사용자 검토 대기**
- 질문: "페로브스카이트 태양전지의 두께에 따른 성능"

## 1. 목표와 산출물

MAPbI3 흡수층 두께 t를 스윕하며 J–V 특성에서 성능 지표를 추출:

| 산출물 | 형식 |
|---|---|
| 두께별 J–V 곡선 | PNG + CSV |
| Jsc, Voc, FF, PCE vs t | PNG + 요약 CSV |
| 두께별 솔브된 .mph | work/ 폴더 (결과 대화용) |

## 2. 모델링 접근 (단계적)

**v0 (이번 목표): 흡수층 단독 1D p–i–n**
Si 예제(si_solar_cell_1d)와 동일 골격의 1D 드리프트-확산. ETL/HTL 대신 흡수층 양끝에 얇은 고농도 도핑(전자/정공 선택성의 이상화) + 오믹 금속 접촉.

```
x=0 (광 입사, 접지)                          x=L (V0 인가)
│ n+ MAPbI3 30nm │  i-MAPbI3  t (스윕)  │ p+ MAPbI3 30nm │
```

**v1 (v0 검증 후): 실제 수송층 추가** — TiO2(또는 SnO2)/MAPbI3/Spiro-OMeTAD 헤테로 구조, 계면 재결합 포함.

근거: v0는 수렴이 안정적이고 두께 의존성(광 흡수 vs 재결합 손실 경쟁)의 핵심 물리를 담음. 문헌 벤치마크(아래 6절)도 ETL-free 구조라 비교 적합.

## 3. 물리 설정

| 항목 | 설정 |
|---|---|
| 인터페이스 | Semiconductor (semi), 1D, 단면적 1 cm² |
| 캐리어 통계 | Boltzmann (Eg 1.55 eV, 저도핑 — Fermi–Dirac 대비 차이 미미 예상) |
| 재결합 | SRH (Trap-Assisted), τn = τp = 38.7 ns (유도: 아래 표) |
| 광생성 | G(x) = ∫ α(λ)·φ(λ)·exp(−α(λ)x) dλ, α=4πk/λ, φ=λF/(hc), 적분 300–850 nm |
| 도핑 | n+: N_D 1e18 cm⁻³ (0~30 nm) / i: 도핑 없음 / p+: N_A 1e18 cm⁻³ (뒤 30 nm) |
| 접촉 | 양끝 오믹 Metal Contact, 앞 접지·뒤 V0 |
| 스터디 | ① Semiconductor Equilibrium → ② Stationary + Aux sweep V0: 0→1.2 V, 20 mV |
| 메시 | 접합부 최대 2 nm, 벌크 최대 10 nm (초기값, 수렴 보고 조정) |

## 4. 재료 파라미터 (MAPbI3) — 전부 출처 있음, 임의값 없음

| 파라미터 | 값 | 출처 |
|---|---|---|
| 밴드갭 Eg | 1.55 eV | Noh et al., Nano Lett. 13, 1764 (2013) |
| 전자친화도 χ | 3.9 eV | Hirasawa et al., Physica B 201, 427 (1994); Sahu & Dixit (arXiv:1806.03950) 표 1 |
| 비유전율 εr | 10 | Sahu & Dixit 표 1 (⚠️ Minemoto & Murata, JAP 116, 054505 (2014)는 6.5 사용 — 민감도 확인 항목) |
| 전자/정공 이동도 μ | 10 / 10 cm²/Vs | Wehrenfennig et al., Adv. Mater. 26, 1584 (2014) |
| Nc / Nv | 2.75e18 / 3.9e18 cm⁻³ | Giorgi et al., JPCL 4, 4213 (2013) 유효질량 기반 (m*e≈0.23, m*h≈0.29) |
| SRH 수명 τ | 38.7 ns | 유도값: L≈1 μm (Stranks et al., Science 342, 341 (2014)) + μ=10 → D=0.2585 cm²/s, τ=L²/D |
| n,k(λ) | Phillips 2015 데이터 | refractiveindex.info CH3NH3PbI3/Phillips (ellipsometry, 300–1500 nm) |
| 스펙트럼 F(λ) | AM1.5 근사 | COMSOL 예제 동봉 파일 (원출처 NREL) — ⚠️ 근사 데이터, 아래 7절 |

도핑 영역(n+/p+) 파라미터는 흡수층과 동일 재료로 두고 농도만 부여 (이상화 — v1에서 실제 수송층으로 대체).

## 5. 결과 처리 (Python, COMSOL 밖에서)

- J–V 배열 추출 → Jsc=|J(0)|, Voc=J=0 교차 보간, MPP 탐색 → FF, PCE
- **PCE 분모(입사 전력)는 스펙트럼 파일을 실제 적분한 Pin 사용** (100 mW/cm² 가정 아님 — 근사 스펙트럼이므로 정직하게 계산, 값 병기)
- 플롯: matplotlib (COMSOL 네이티브 export는 부가 기능)

## 6. 검증 기준 (이 기준으로 "바르게 출력"을 판정)

정량 벤치마크 — Sahu & Dixit (arXiv:1806.03950, SCAPS, ETL-free 유사 구조):

| 지표 | 기대 범위 (t≈450–650 nm) |
|---|---|
| Jsc | ~20–26 mA/cm² |
| Voc | ~1.0–1.1 V |
| FF | ~0.7–0.85 |
| PCE | ~17–22% |
| 최적 두께 | ~550–650 nm에서 완만한 최대 |

정성 체크: ① G(x)가 입사면에서 단조 감소 ② Jsc는 t 증가 시 포화형 증가 ③ 얇을 때 흡수 손실, 두꺼울 때 재결합 손실로 PCE 극대 존재 ④ 수렴 실패 0건.

v0는 계면 재결합·수송층 손실이 없어 벤치마크보다 다소 낙관적(특히 FF, Voc)일 수 있음 — 범위 상단 초과 시 원인 분석.

## 7. v0의 한계 (결과 해석 시 필수 인지)

1. **간섭 무시**: Beer–Lambert는 박막 간섭을 못 담음. 실제 박막(수백 nm)에선 Jsc가 두께에 따라 진동 — v2에서 Wave Optics 결합(T2 연계)으로 개선 예정
2. 표면 반사 무시 (벤치마크 문헌도 동일 가정)
3. ETL/HTL·계면 재결합 없음 (v1)
4. 이온 이동/히스테리시스, radiative/Auger 재결합 없음
5. AM1.5 스펙트럼이 38점 근사 — Jsc 절대값 수 % 오차 가능. ③ 데이터 준비의 '자동 다운로드 시도'로 ASTM G-173 전체 데이터(발행: NLR, 구 NREL) 교체 가능 (2026-07-07 기능 추가)

## 8. 사용자 확인 요청 (검토 포인트)

1. 두께 범위/간격: **300–1100 nm, 100 nm 간격(9회 솔브)** 제안 — OK?
2. v0 → v1(수송층 포함) 순서 — OK? 아니면 처음부터 v1?
3. τ=38.7 ns (확산길이 1 μm 상당) 기본값 — OK? (실험값 있으면 교체)
4. 조명: AM1.5 근사 1-sun 고정 — OK?

## 참고문헌

- Sahu & Dixit, "Inverted structure perovskite solar cells: A theoretical study", arXiv:1806.03950 — 파라미터 표·두께 최적화 벤치마크
- Minemoto & Murata, J. Appl. Phys. 116, 054505 (2014) — 페로브스카이트 디바이스 모델링 원조
- COMSOL si_solar_cell_1d 모델 문서 (설치 폴더 doc) — 1D 태양전지 레시피·광생성 적분식
- Phillips 2015 n,k: https://refractiveindex.info/?shelf=other&book=CH3NH3PbI3&page=Phillips
