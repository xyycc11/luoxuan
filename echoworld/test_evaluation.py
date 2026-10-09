"""评测口径测试，不使用真实模型或向量数据库。"""
import unittest
from echoworld.evaluation import evaluate, fixed_cases, score_case, summarize


class EvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = evaluate()

    def test_ids_unique_and_twelve_cases(self):
        self.assertEqual(len({case["id"] for case in fixed_cases()}), 12)

    def test_guarded_contract(self):
        self.assertEqual(self.report["guarded"]["passed"], 12)
        self.assertEqual(self.report["guarded"]["forbidden_returned"], 0)

    def test_unsafe_baseline_exposes_negative_cases(self):
        self.assertEqual(self.report["baseline"]["forbidden_returned"], 9)
        self.assertLess(self.report["baseline"]["passed"], 12)

    def test_templates_and_state(self):
        self.assertEqual(self.report["reply_passed"], 12)
        self.assertTrue(self.report["state_unchanged"])
        self.assertEqual(self.report["model_calls"], 0)

    def test_empty_denominators_are_not_fake_perfection(self):
        result = summarize([])
        self.assertIsNone(result["pass_rate"])
        self.assertIsNone(result["candidate_precision"])
        self.assertIsNone(result["positive_recall"])

    def test_correct_id_wrong_text_still_fails(self):
        case = fixed_cases()[0]
        self.assertFalse(score_case(case, [dict(id="m1", text="伪造文本")])["passed"])

    def test_miss_changes_recall(self):
        scored = score_case(fixed_cases()[0], [])
        self.assertFalse(scored["passed"])
        self.assertEqual(summarize([scored])["positive_recall"], 0)

    def test_reproducible(self):
        self.assertEqual(self.report, evaluate())


if __name__ == "__main__":
    unittest.main()
