"""
페로브스카이트 두께 스윕 v0 — 물리 파라미터 (CASE_SPEC.md 4절과 1:1 대응)
모든 값에 출처 있음. 임의값 없음. 빌드 스크립트가 이 파일만 import.
"""

MATERIAL = {
    # Noh et al., Nano Lett. 13, 1764 (2013)
    "Eg": "1.55[V]",                # 밴드갭 (COMSOL 입력 단위 [V]=eV 상당) [검증 필요: 단위 표기]
    # Hirasawa 1994; Sahu & Dixit arXiv:1806.03950 표1
    "chi": "3.9[V]",                # 전자친화도
    # Sahu & Dixit 표1 (Minemoto 2014는 6.5 — 민감도 체크 항목)
    "epsr": "10",
    # Wehrenfennig et al., Adv. Mater. 26, 1584 (2014)
    "mun": "10[cm^2/(V*s)]",
    "mup": "10[cm^2/(V*s)]",
    # Giorgi et al., JPCL 4, 4213 (2013): m*e≈0.23, m*h≈0.29
    "Nc": "2.75e18[1/cm^3]",
    "Nv": "3.9e18[1/cm^3]",
}

RECOMBINATION = {
    # 유도: L=1um (Stranks 2014, Science 342, 341) & mu=10 -> D=0.2585 cm^2/s, tau=L^2/D
    "taun": "38.7[ns]",
    "taup": "38.7[ns]",
}

STRUCTURE = {
    "t_nplus": "30[nm]",     # 전자 선택층 이상화 (v0)
    "t_pplus": "30[nm]",     # 정공 선택층 이상화 (v0)
    "Nd_nplus": "1e18[1/cm^3]",
    "Na_pplus": "1e18[1/cm^3]",
    "area": "1[cm^2]",
}

GENERATION = {
    # Si 예제 G_ph와 동일 구조. x=0이 입사면. 부호는 감쇠(-) — 예제 PDF 식(1) 기준.
    # kref/F는 보간 함수명 (빌드 시 생성)
    "expr": ("4*pi/(h_const*c_const)*"
             "integrate(kref(lm)*F(lm)*exp(-4*pi*kref(lm)*x/lm),lm,300[nm],850[nm])"),
    "nk_file": "data/mapbi3_n_k.txt",        # 파장[um], n, k
    "spectrum_file": "data/am15_approx.txt",  # 파장[nm], F[W/m^2/nm] (COMSOL 예제 동봉본)
}

SWEEP = {
    "thickness_nm": [300, 400, 500, 600, 700, 800, 900, 1000, 1100],  # 사용자 확인 항목
    "V_start": 0.0,
    "V_stop": 1.2,
    "V_step": 0.02,
}

MESH = {
    "h_junction": "2[nm]",   # 접합 근방 최대 요소 (초기값, 수렴 보고 조정)
    "h_bulk": "10[nm]",
}
