"""작업 log.txt → 타임라인(Gantt) 구간 파싱 (2026-07-13).

전체 프로세스를 '시간별로 어떤 작업을 얼마나' 그래프로 보기 위한 백엔드.
로그 형식 가정: 각 줄 "[HH:MM:SS] 내용" (jobs.log 포맷). 날짜가 없으므로
시간이 줄어들면 자정 넘김(+24h)으로 처리. IBC 그리드 로그는 셀 단위 행으로
구조화하고, 그 밖의 로그는 이벤트 간격을 그대로 구간화하는 일반 파서로 처리.
출력: {"rows": [{"label", "segs": [{"s","e","kind","label","detail"}]}], "t0", "t1"}
(s/e = t0 기준 상대 초)
"""
import re

_TS = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\]\s?(.*)$")
_CELL = re.compile(r"=====\s*\[(\d+)/(\d+)\]\s*(.+?)\s*=====")


def _events(text):
    """[(절대초, 메시지)] — 자정 넘김 보정 포함. 타임스탬프 없는 줄은 직전에 연결."""
    ev, day, prev = [], 0, None
    for line in text.splitlines():
        m = _TS.match(line)
        if not m:
            if ev and line.strip():
                ev[-1] = (ev[-1][0], ev[-1][1] + " ¶ " + line.strip())
            continue
        t = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
        if prev is not None and t < prev - 1:
            day += 1
        prev = t
        ev.append((t + day * 86400, m[4].strip()))  # 들여쓰기 제거 (분류 단순화)
    return ev


def _kind(start_msg, end_msg):
    """구간 종류 분류 (프론트 색상 매핑용)."""
    if "[광학]" in start_msg or start_msg.startswith("[") and "λ=" in start_msg:
        return "optics"
    if "unsolved 저장" in end_msg or "지오메트리 OK" in start_msg or "[1단계]" in start_msg:
        return "build"
    if start_msg.startswith("솔브:"):
        if "완료" in end_msg:
            return "solve_ok"
        if "부분" in end_msg:
            return "solve_partial"
        return "solve_fail"
    if "데이터셋" in start_msg or "V0 평가" in start_msg or "지표" in start_msg:
        return "eval"
    return "other"


def parse(text):
    ev = _events(text)
    if len(ev) < 2:
        return {"rows": [], "t0": 0, "t1": 0}
    t0, t1 = ev[0][0], ev[-1][0]
    rows = []

    # ---- 행 분할: 광학 / 1단계(모델 생성) / 셀별(===== [i/N] ... =====) / 기타 ----
    idx_cells = [(k, _CELL.search(msg)) for k, (_, msg) in enumerate(ev)]
    cell_marks = [(k, m) for k, m in idx_cells if m]

    def add_segments(row_label, sub):
        """sub = [(t, msg)] 연속 이벤트 → 인접 구간화 (1초 미만은 합쳐서 스킵)."""
        segs = []
        for a in range(len(sub) - 1):
            ta, ma = sub[a]
            tb, mb = sub[a + 1]
            if tb - ta < 1:
                continue
            k = _kind(ma, mb)
            lab = ma if len(ma) <= 60 else ma[:57] + "…"
            segs.append({"s": ta - t0, "e": tb - t0, "kind": k,
                         "label": lab, "detail": f"{ma} → {mb[:80]} ({tb - ta:.0f}s)"})
        if segs:
            rows.append({"label": row_label, "segs": segs})

    if cell_marks:
        first_cell = cell_marks[0][0]
        head = ev[:first_cell + 1]
        # 광학 구간과 1단계(생성) 구간을 헤더에서 분리
        opt = [(t, m) for t, m in head if "[광학]" in m or "λ=" in m]
        if len(opt) >= 2:
            rows.append({"label": "광학 λ 스윕", "segs": [{
                "s": opt[0][0] - t0, "e": opt[-1][0] - t0, "kind": "optics",
                "label": f"λ {max(0, len(opt) - 2)}점", "detail":
                f"파동광학 흡수 스펙트럼 계산 ({opt[-1][0] - opt[0][0]:.0f}s)"}]})
        gen = [(t, m) for t, m in head if "[1단계]" in m or "unsolved 저장" in m]
        if len(gen) >= 2:
            rows.append({"label": "모델 생성(1단계)", "segs": [{
                "s": gen[0][0] - t0, "e": gen[-1][0] - t0, "kind": "build",
                "label": f"unsolved {sum('unsolved 저장' in m for _, m in gen)}개",
                "detail": f"unsolved 모델 생성 ({gen[-1][0] - gen[0][0]:.0f}s)"}]})
        # 셀별 행
        for c, (k, m) in enumerate(cell_marks):
            k_end = cell_marks[c + 1][0] if c + 1 < len(cell_marks) else len(ev) - 1
            sub = ev[k:k_end + 1]
            label = m.group(3).replace("W=", "W").replace("gap=", "g").replace("um", "")
            # 셀 내부: 솔브/평가 이벤트만 구간화 (로그 잡음 제거)
            keep = [sub[0]] + [(t, msg) for t, msg in sub[1:]
                               if msg.startswith(("솔브:", "  솔브:")) or "완료" in msg
                               or "솔브 실패" in msg or "셀 실패" in msg or "부분" in msg
                               or "지표:" in msg or "데이터셋" in msg] + [sub[-1]]
            # 중복 제거(시간순 유지)
            seen, ordered = set(), []
            for t, msg in keep:
                if (t, msg) not in seen:
                    seen.add((t, msg))
                    ordered.append((t, msg))
            ordered.sort(key=lambda x: x[0])
            add_segments(label, ordered)
    else:
        # 일반 로그(비 그리드): 이벤트 간격을 그대로 구간화 (최대 400개)
        add_segments("전체", ev[:400])
    return {"rows": rows, "t0": t0, "t1": t1}
