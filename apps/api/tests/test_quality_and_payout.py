import unittest

from lodia.domain import ContributionWeight, DataReadinessLevel, RevenueEvent
from lodia.payout import calculate_payout
from lodia.pipeline import process_text_case


class QualityAndPayoutTests(unittest.TestCase):
    def test_automatic_pipeline_never_promotes_to_commercial_drl(self):
        processed = process_text_case(
            raw_text="请分析这个客服投诉案例，输出处理步骤、验收结果和复盘规则。",
            owner_id="user_1",
            allowed_uses=["commercial_dataset", "training"],
        )

        self.assertEqual(processed.case.quality_gate.drl, DataReadinessLevel.DRL2)
        self.assertIn("human_review", processed.case.quality_gate.required_actions)

    def test_platform_keeps_only_twenty_percent_of_net_margin(self):
        event = RevenueEvent(event_id="use_1", gross_revenue_cents=100_000, direct_cost_cents=20_000)
        contributions = [
            ContributionWeight(
                case_id="case_a",
                contributor_id="user_a",
                quality_score=0.9,
                novelty_score=1.0,
                source_trust_score=0.9,
                license_weight=1.0,
                usage_count=3,
                duplicate_penalty=1.0,
                reviewed_level=DataReadinessLevel.DRL3,
            ),
            ContributionWeight(
                case_id="case_b",
                contributor_id="user_b",
                quality_score=0.6,
                novelty_score=0.7,
                source_trust_score=0.8,
                license_weight=1.0,
                usage_count=1,
                duplicate_penalty=1.0,
                reviewed_level=DataReadinessLevel.DRL3,
            ),
        ]

        plan = calculate_payout(event, contributions)

        self.assertEqual(plan.net_margin_cents, 80_000)
        self.assertEqual(plan.platform_share_cents, 16_000)
        self.assertEqual(plan.contributor_pool_cents, 64_000)
        self.assertEqual(sum(item.amount_cents for item in plan.allocations), 64_000)
        self.assertGreater(plan.allocations[0].amount_cents, plan.allocations[1].amount_cents)


if __name__ == "__main__":
    unittest.main()
