"""
refractiveindex.info CSV -> COMSOL 보간용 3열 txt (파장[um], n, k)

refractiveindex.info의 CSV는 보통 'wl,n' 블록과 'wl,k' 블록이 세로로 쌓인 형식.
형식이 다르면 오류 출력을 Claude에게 붙여넣을 것.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "mapbi3_nk_phillips.csv"
DST = ROOT / "data" / "mapbi3_n_k.txt"


def main():
    if not SRC.exists():
        print("!! 입력 파일 없음:", SRC)
        print("   SETUP.md 3절대로 다운로드 후 다시 실행")
        return

    text = SRC.read_text(encoding="utf-8", errors="ignore")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    blocks = []  # [(header, [(wl, val), ...])]
    cur = None
    for ln in lines:
        low = ln.lower().replace(" ", "")
        if low.startswith("wl,") or low.startswith("wavelength"):
            cur = (low, [])
            blocks.append(cur)
            continue
        parts = ln.split(",")
        if cur is not None and len(parts) >= 2:
            try:
                cur[1].append((float(parts[0]), float(parts[1])))
            except ValueError:
                pass

    if len(blocks) == 1 and lines and lines[0].lower().count(",") >= 2:
        # 단일 블록에 wl,n,k 3열인 경우
        rows = []
        for ln in lines[1:]:
            p = ln.split(",")
            if len(p) >= 3:
                try:
                    rows.append((float(p[0]), float(p[1]), float(p[2])))
                except ValueError:
                    pass
        write(rows)
        return

    if len(blocks) < 2:
        print("!! 예상과 다른 CSV 구조. 블록 수:", len(blocks))
        print("   파일 앞 10줄:")
        for ln in lines[:10]:
            print("   ", ln)
        return

    nmap = dict(blocks[0][1])
    kmap = dict(blocks[1][1])
    common = sorted(set(nmap) & set(kmap))
    rows = [(wl, nmap[wl], kmap[wl]) for wl in common]
    write(rows)


def write(rows):
    if not rows:
        print("!! 데이터 0행 — CSV 내용을 Claude에게 붙여넣을 것")
        return
    with open(DST, "w", encoding="utf-8") as f:
        for wl, n, k in rows:
            f.write(f"{wl}\t{n}\t{k}\n")
    print(f"OK: {DST} ({len(rows)}행, 파장 {rows[0][0]}–{rows[-1][0]} um)")
    print("   앞 3행:", rows[:3])


if __name__ == "__main__":
    main()
