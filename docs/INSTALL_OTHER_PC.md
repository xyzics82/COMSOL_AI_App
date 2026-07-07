# 다른 컴퓨터(COMSOL 설치됨)에서 이 앱 구동하기

작성: 2026-07-07

## 준비물

- 대상 PC: COMSOL 6.4 이상 + Semiconductor 모듈, 라이선스 동글
- Python 3.10~3.13 (3.12 권장) — 없으면 https://www.python.org/downloads/ 에서 설치.
  설치 화면에서 **"Add python.exe to PATH"** 체크
- 첫 실행 1회만 인터넷 필요 (파이썬 패키지 자동 설치)

## 절차

1. 이 폴더(`comsol_ai_app`) 전체를 USB 등으로 복사. 단 아래는 **빼고** 복사:
   - `.venv\` — 대상 PC에서 start.bat이 자동으로 새로 만듦 (복사하면 오히려 문제)
   - `work\` — 작업 이력·임시 파일 (필요하면 포함해도 무방)
   - `.env` — API 키. 보안상 절대 복사하지 말 것 (필요하면 대상 PC에서 새로 작성)
   - `data\`는 **반드시 포함** — 준비된 스펙트럼·n,k·재료·참고문헌·케이스가 그대로 이전됨
     (데이터 재업로드 불필요)
2. 대상 PC에서 `start.bat` 더블클릭
   - 첫 실행: 가상환경 생성 + 패키지 설치로 수 분 소요
   - 브라우저가 자동으로 http://127.0.0.1:8712 를 엶
3. ① 환경 점검 탭에서 **빠른 점검** — COMSOL 연결(동글)과 데이터 준비 상태 확인

## 주의사항

- **COMSOL 설치 경로가 다른 경우**: 전체 점검과 si_demo 케이스가
  `C:\Program Files\COMSOL\COMSOL64\Multiphysics_copy1\applications\...` 예제 경로를 참조함.
  경로가 다르면 이 두 기능만 실패하고 **페로브스카이트 케이스들은 영향 없음**.
  경로를 Claude에게 알려주면 수정해 줌 (backend/diagnostics.py, backend/comsol_cases.py 상단).
- **인터넷이 없는 PC**: 앱 전체 구동은 첫 pip 설치 때문에 어려움.
  이런 PC는 앱 대신 **솔브 전용**으로 쓰는 것을 권장:
  ④에서 `*_unsolved.mph` + `run_server.bat`을 받아 USB로 옮기고 더블클릭
  (COMSOL comsolbatch가 순차 솔브 — Python 불필요) → 생긴 `*_solved.mph`를
  원래 PC의 ④ "solved .mph 업로드"로 가져오면 결과 추출됨.
- 두 PC에서 각각 앱을 쓰면 작업 이력(work\)은 PC별로 따로 쌓임 (동기화 없음).
  데이터·재료·케이스는 복사한 시점의 상태로 감.
- 동글은 솔브하는 PC에 꽂혀 있어야 함.
