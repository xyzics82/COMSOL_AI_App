"""v1: p-i-n + C60/BCP ETL 스택 — 물리 파라미터 (CASE_SPEC.md 2절과 1:1, 전부 출처 있음).

- C60: RSC Adv. 14, D4RA00634H (2024) Table 2 (open access)
- BCP: Touafek, Dridi & Mahamdi, J. Tech. Innov. Renew. Energy 9, 1 (2020) Table 1
       (실험 J-V 재현으로 검증된 SCAPS 세트; HOMO-LUMO 3.5 eV는 Hill & Kahn, JAP 86, 4515 (1999))
- MAPbI3/τ/스펙트럼/n,k: v0 케이스(cases/perovskite_thickness/params.py)와 동일
"""

C60 = {
    "Eg": "1.70[V]",
    "chi": "3.90[V]",
    "epsr": "4.2",
    "Nc": "8.0e19[1/cm^3]",
    "Nv": "8.0e19[1/cm^3]",
    "mun": "0.08[cm^2/(V*s)]",
    "mup": "3.5e-3[cm^2/(V*s)]",
    "Nd": "2.6e17[1/cm^3]",
}

BCP = {
    "Eg": "3.5[V]",
    "chi": "3.90[V]",
    "epsr": "4.0",
    "Nc": "2.2e15[1/cm^3]",
    "Nv": "1.8e17[1/cm^3]",
    "mun": "0.001[cm^2/(V*s)]",
    "mup": "0.002[cm^2/(V*s)]",
    # Touafek 2020: ND>1e17부터 성능 향상, 1e20에서 최대 — 기본은 보수적 1e18, 폼에서 수정 가능
    "Nd_default": "1e18",
}

STRUCTURE = {
    "t_pplus": "30[nm]",          # p측 이상화 (v0 방식)
    "Na_pplus": "1e18[1/cm^3]",
    "t_abs_nm": 800,              # 사용자 확정: 흡수층 고정
    "area": "1[cm^2]",
}

SWEEP = {
    "c60_nm": [20, 30, 40, 50, 60],   # 사용자 기본 20nm에서 증가
    "bcp_nm": [5, 10, 15],            # 사용자 기본 5nm에서 증가
    "V_start": 0.0,
    "V_stop": 1.2,
    "V_step": 0.02,
}
