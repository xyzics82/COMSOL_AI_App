# COMSOL AI 모델링 어시스턴트

자연어 → 모델 제안 → 입력 폼 → COMSOL 실행(로컬/오프라인 서버 반출) → 결과 대화.
계획서: `docs/PLAN.md` · 페로브스카이트 케이스 사양: `cases/perovskite_thickness/CASE_SPEC.md`

**설계 전제**: 이 앱은 향후 연구 AI 에이전트의 한 구성요소가 된다. 따라서 모든 기능은
REST API(아래)로 노출되고, 웹 UI는 교체 가능한 껍데기다. 에이전트는 나중에 이 API를
도구(tool)로 직접 호출한다.

## 실행 방법 (터미널 불필요)

1. **`start.bat` 더블클릭** — 최초 1회는 가상환경 생성·패키지 설치로 수 분 소요
2. 브라우저가 자동으로 http://127.0.0.1:8712 을 엽니다
3. UI 순서: **① 환경 점검** (최초 필수 — 완료 후 '로그 복사'를 Claude에게 전달)
   → **② 데이터 준비** (MAPbI3 n,k 자동 다운로드 시도, 실패 시 수동 업로드)
   → **③ 케이스 실행** (Si 데모 먼저 → 페로브스카이트)
   → **④ 작업·결과** (로그·플롯·CSV·.mph 다운로드, 서버 솔브본 업로드)

문제 발생 시: 해당 작업의 **로그 복사** 버튼 → Claude에게 붙여넣기 → 수정본 반영 후 재실행.

## 케이스

| ID | 설명 | 상태 |
|---|---|---|
| si_demo | 설치된 COMSOL Si 예제 실행 → J-V/지표 | ✅ 검증 완료 (Voc 0.61V, Jsc 33mA/cm² — 문서값 일치) |
| perovskite_thickness | MAPbI3 1D p-i-n 두께 스윕 → Jsc/Voc/FF/PCE vs t | ✅ 검증 완료 (2026-07-06, 300–1100nm 9점: PCE 최대 20.86% @800–900nm, CASE_SPEC 6절 기준 통과) |

자연어 입력(Phase 2): ③ 탭 상단에서 문장으로 요청하면 Claude가 케이스 선택+폼 채움 → 사용자가 확인 후 실행.
활성화하려면 SETUP.md 5절(API 키) 참고. 임의값 금지 원칙: AI는 사용자가 말한 값만 채우고 나머지는 기본값+표시.

두 케이스 모두 `mode=export`로 미솔브 .mph + `run_server.bat`를 만들어 **오프라인 서버**에서
솔브한 뒤, 솔브본을 ④ 탭에서 업로드하면 재솔브 없이 결과를 추출한다.

## REST API (에이전트 연동용)

```
GET  /api/health                        GET  /api/cases
GET  /api/data/status                   POST /api/cases/{case_id}/run   → {job_id}
POST /api/data/nk/fetch                 GET  /api/jobs · /api/jobs/{id}
POST /api/data/nk/upload                GET  /api/jobs/{id}/log · /artifacts · /artifacts/{name}
POST /api/diagnostics    → {job_id}     POST /api/jobs/{id}/upload_solved
```

## 폴더 구조

```
backend/          FastAPI + 작업큐 + MPh 러너 (COMSOL은 여기서만 접근)
frontend/         단일 페이지 UI (v0 — Phase 5에서 Next.js PWA로 교체 예정)
cases/            케이스 사양서 + 물리 파라미터(전부 출처 있음)
data/             AM1.5 스펙트럼, MAPbI3 n,k
work/             작업별 로그·산출물(.mph/.png/.csv) — 자동 생성
scripts/          CLI 보조 도구 (웹앱이 기본 경로)
docs/PLAN.md      개발 계획서 v0.2
```

## 로드맵 (계획서 요약)

Phase 0 진단(지금) → 1 실행 골격(지금, Si 데모로 검증) → 2 LLM 파이프라인(자연어→제안→폼, Anthropic API 키 필요)
→ 3 결과 대화 → 4 멀티모델 비교 → 5 PWA(Next.js, Tailscale) → (이후) 연구 에이전트의 도구로 편입
