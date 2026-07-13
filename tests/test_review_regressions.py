import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from backend import comsol_cases, jobs, llm_propose, timeline, wo_optics


class _Dataset:
    def __init__(self, name):
        self._name = name

    def name(self):
        return self._name


class _Datasets:
    def __init__(self, names):
        self._names = names

    def children(self):
        return [_Dataset(name) for name in self._names]


class _Model:
    def __init__(self, values):
        self.values = values
        self.datasets = _Datasets(sorted({ds for _, ds in values if ds is not None}))

    def __truediv__(self, name):
        if name != "datasets":
            raise KeyError(name)
        return self.datasets

    def evaluate(self, expr, dataset=None):
        key = (expr, dataset)
        if key not in self.values:
            raise RuntimeError(f"no value for {key}")
        return self.values[key]


class SweepSelectionTests(unittest.TestCase):
    def test_actual_voltage_beats_point_count(self):
        coarse_v = np.linspace(0.0, 1.075, 26)
        fine_v = np.linspace(0.0, 0.65, 27)
        model = _Model({
            ("V0", "coarse"): coarse_v,
            ("V0", "fine"): fine_v,
            ("semi.I0_1", "coarse"): np.linspace(-1.0, 1.0, coarse_v.size),
            ("semi.I0_1", "fine"): np.linspace(-1.0, 0.2, fine_v.size),
        })
        points, last_v, dataset = comsol_cases._sweep_progress(model)
        self.assertEqual((points, dataset), (26, "coarse"))
        self.assertAlmostEqual(last_v, 1.075)
        voltage, current, expr = comsol_cases._extract_iv(model, lambda _text: None)
        self.assertEqual(expr, "semi.I0_1")
        self.assertEqual(voltage.size, current.size)
        self.assertAlmostEqual(float(voltage.max()), 1.075)

    def test_voltage_only_dataset_is_not_recoverable_jv(self):
        model = _Model({("V0", "voltage_only"): np.linspace(0.0, 1.125, 28)})
        points, last_v, _dataset = comsol_cases._sweep_progress(model)
        self.assertEqual(points, 28)
        self.assertAlmostEqual(last_v, 1.125)
        with self.assertRaisesRegex(RuntimeError, "전류 표현식"):
            comsol_cases._extract_iv(model, lambda _text: None)


class FailureDiagnosisTests(unittest.TestCase):
    def test_evaluation_failure_without_voltage_is_rendered(self):
        text = comsol_cases._diagnose_fail(
            "W2/g3", [(120.0, "성공", None, None, None),
                       (120.0, "eval", None, None, "dataset error")])
        self.assertIn("결과 데이터셋 평가", text)
        self.assertIn("dataset error", text)


class OpticalNormalizationTests(unittest.TestCase):
    def test_generation_profile_integral_matches_target(self):
        depth = np.array([0.0, 400.0, 800.0])
        generation = np.array([4.0e27, 2.0e27, 1.0e26])
        scaled, result = wo_optics._normalize_generation_profile(depth, generation, 22.14)
        self.assertTrue(np.all(scaled > 0))
        self.assertAlmostEqual(result, 22.14, places=10)


class ModificationPasteTests(unittest.TestCase):
    CASE_ID = "perovskite_ibc_3d_server"

    def test_partial_ai_params_preserve_current_values(self):
        current = comsol_cases.schema_defaults(self.CASE_ID)
        current["mesh_hmax_nm"] = 120
        raw = json.dumps({
            "params": {"v_max": "1.2"},
            "changed": ["v_max: 1.3 → 1.2"],
            "not_supported": [], "warnings": [], "rationale": "상한만 변경",
        }, ensure_ascii=False)
        result = llm_propose.parse_mod_response(raw, self.CASE_ID, current)
        self.assertEqual(result["params"]["v_max"], "1.2")
        self.assertEqual(result["params"]["mesh_hmax_nm"], "120")
        self.assertEqual(set(result["params"]), set(current))

    def test_invalid_select_is_rejected(self):
        raw = json.dumps({"params": {"mode": "invented"}})
        with self.assertRaisesRegex(ValueError, "허용 목록"):
            llm_propose.parse_mod_response(raw, self.CASE_ID, {})

    def test_scalar_messages_are_not_split_into_characters(self):
        raw = json.dumps({"params": {"v_max": "1.2"}, "warnings": "단일 경고"},
                         ensure_ascii=False)
        result = llm_propose.parse_mod_response(raw, self.CASE_ID, {})
        self.assertEqual(result["warnings"], ["단일 경고"])


class JobIdempotencyTests(unittest.TestCase):
    def test_active_review_resume_is_not_duplicated(self):
        old_db, old_dir = jobs.DB, jobs.JOBS_DIR
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                jobs.DB = root / "jobs.sqlite3"
                jobs.JOBS_DIR = root / "jobs"
                jobs.JOBS_DIR.mkdir()
                params = {"case_id": "case", "resume_from": "review-source"}
                first, created1 = jobs.create_job_once("case_run", params, "resume_from")
                second, created2 = jobs.create_job_once("case_run", params, "resume_from")
                self.assertTrue(created1)
                self.assertFalse(created2)
                self.assertEqual(first, second)
                jobs.set_status(first, "done")
                third, created3 = jobs.create_job_once("case_run", params, "resume_from")
                self.assertTrue(created3)
                self.assertNotEqual(first, third)
        finally:
            jobs.DB, jobs.JOBS_DIR = old_db, old_dir


class TimelineTests(unittest.TestCase):
    def test_midnight_rollover_is_monotonic(self):
        parsed = timeline._events(
            "[23:59:59] 시작\n[00:00:01] 완료\n")
        self.assertEqual(parsed[1][0] - parsed[0][0], 2)


if __name__ == "__main__":
    unittest.main()
