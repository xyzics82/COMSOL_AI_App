# Yang 2020 QIBC 2D 논문 재현 케이스

## 범위

이 케이스는 Z. Yang et al., *Energy & Environmental Science* **13** (2020)
1753-1765, DOI `10.1039/C9EE04203B`와 Supporting Information의 QIBC 셀만을
대상으로 한다. 기존 `perovskite_ibc_2d`, `perovskite_ibc_3d_server`의 기하,
MAPbI3 가정, IDL 2 nm, 수명, 메시 사다리, 전압 사다리를 가져오지 않는다.

논문은 absorber를 조성명 없이 `perovskite`로만 기술하므로 이 케이스도 특정 조성으로
이름 붙이지 않는다.

## 현재 실행 모드

- `paper_reference`: COMSOL을 호출하지 않는다. 논문 보고값, 구조도, Figure/SI별
  재현 준비도, 누락 입력을 작업 산출물로 만든다. 이 모드가 기본값이다.
- `geometry_only`: 보고된 치수와 SI 그림에서 재구성한 좌표만 사용해 별도의 2D
  geometry-only `.mph`를 만든다. 해석 물리와 임의 재료값은 넣지 않는다.
- `local`, `export`: 첫 버전에서는 비활성이다. 원문 입력 누락과 solver 구현 미완료를
  작업 폴더에 기록한 뒤 명시적으로 중단한다. 입력과 검증된 API가 모두 채워진 다음
  패치에서 study별 실행기를 연다.

`paper_reference`의 그래프와 표는 **새 시뮬레이션 결과가 아니라 논문 보고값**이다.

## 기본 QIBC 구조

- pitch 2 um, HTL 폭 1 um, fill fraction 0.5
- SnO2 ETL 100 nm, NiOx HTL 50 nm, Al2O3 100 nm, Ni 50 nm, FTO 500 nm
- 최적 구조: PMMA 80 nm, perovskite 전체 높이 600 nm, island 위 cap 400 nm,
  effective perovskite 500 nm, opaque Ag rear
- Fig. 1b 2D 계산의 면외 z 두께는 본문 p.1755에 보고된 1 mm이다.
- `effective = 600 - 0.5 * (100 + 50 + 50) = 500 nm` 관계와 Al2O3/Ni/HTL의
  동일 lateral 폭·중앙 배치는 SI Fig. S2 형상으로부터 재구성한 값이다. 원문 CAD가
  없으므로 `inferred_geometry`로 유지한다.
- Ag 두께는 논문에 없어서 geometry domain으로 임의 생성하지 않는다.

## 논문 재현이 아직 차단되는 이유

1. Fig. 1에는 평판 TMM이 아니라 lateral `G(x,y)`를 내는 full 2D Wave Optics가
   필요하다. 전 층의 실제 `n(lambda), k(lambda)`, 입사 편광/각도, PML와 메시가 없다.
2. 계면 `Dit` 및 bulk `Nt`를 재결합률로 바꾸는 capture cross section과 thermal
   velocity가 없다. COMSOL 계면 trap API도 이 프로젝트에서 검증되지 않았다.
3. Fig. 6d-f의 operating point는 short circuit로 보고됐지만, 이온 이동도/확산계수,
   초기조건, blocking boundary, bias history와 steady-state constraint가 없다.
4. photon recycling의 spectral front/rear absorptance와 공간 재주입 방식이 없다.
5. MP/OC map의 실제 전압, 대부분의 sweep exact grid와 raw curve가 없다.

누락값을 25.4%에 맞추도록 역조정하거나 기존 케이스의 값을 복사하지 않는다.

## 산출물

- `yang2020_structure.svg`: 논문 QIBC 구조와 출처 상태
- `paper_reported_targets.svg/.csv`: 논문에 인쇄된 대표 정량 결과
- `figure_readiness.csv`: Fig. 1-7, S1-S22, Table S1-S2의 구현 상태
- `missing_inputs.csv`: 연구별 차단 입력
- `study_manifest.json`, `paper_params.json`: 기계 판독 가능한 연구/물성 출처
- `RESULTS_README.md`: 이번 작업 결과의 의미와 다음 서버 실행 순서

## 서버 최소 검증 순서

1. 앱/worker를 재시작해 이전 dead MPh client를 폐기한다.
2. `paper_reference`를 실행해 COMSOL 없이 산출물이 정상 생성되는지 확인한다.
3. `geometry_only` 한 건을 실행해 `.mph`를 열고 domain topology와 치수를 육안 확인한다.
4. 누락 optical 입력을 확보한 뒤에만 Fig. 1 baseline 한 점을 canary로 실행한다.
5. canary는 point별 `build -> save -> solve -> evaluate -> save -> remove`를 완주해야 한다.
6. 연결 단절은 개별 물리 실패로 삼키지 않고 배치 전체를 즉시 실패 처리한다.

## 출처

- Yang et al., *Energy Environ. Sci.* 13 (2020) 1753-1765,
  https://doi.org/10.1039/C9EE04203B
- Supporting Information, RSC, `c9ee04203b1.pdf`, 특히 Fig. S1-S22와 Table S1-S2
