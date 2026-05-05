from __future__ import annotations

import math
from typing import Iterable, List

from .domain import ContributionWeight, DataReadinessLevel, PayoutAllocation, PayoutPlan, RevenueEvent


PLATFORM_NET_MARGIN_RATE = 0.20
CONTRIBUTOR_NET_MARGIN_RATE = 0.80


def calculate_payout(event: RevenueEvent, contributions: Iterable[ContributionWeight]) -> PayoutPlan:
    if not event.billable:
        return PayoutPlan(
            event_id=event.event_id,
            gross_revenue_cents=event.gross_revenue_cents,
            direct_cost_cents=event.direct_cost_cents,
            net_margin_cents=0,
            platform_share_cents=0,
            contributor_pool_cents=0,
            allocations=[],
            warning="non_billable_event",
        )

    net_margin = max(event.gross_revenue_cents - event.direct_cost_cents, 0)
    contributor_pool = int(round(net_margin * CONTRIBUTOR_NET_MARGIN_RATE))
    platform_share = net_margin - contributor_pool
    weighted = [(item, contribution_weight(item)) for item in contributions]
    weighted = [(item, weight) for item, weight in weighted if weight > 0]

    if not weighted or contributor_pool == 0:
        return PayoutPlan(
            event_id=event.event_id,
            gross_revenue_cents=event.gross_revenue_cents,
            direct_cost_cents=event.direct_cost_cents,
            net_margin_cents=net_margin,
            platform_share_cents=platform_share,
            contributor_pool_cents=contributor_pool,
            allocations=[],
            warning="no_eligible_contributions",
        )

    total_weight = sum(weight for _, weight in weighted)
    raw_allocations = [
        (item, weight, contributor_pool * weight / total_weight)
        for item, weight in weighted
    ]
    allocations: List[PayoutAllocation] = []
    used = 0
    for item, weight, amount in raw_allocations:
        cents = int(math.floor(amount))
        used += cents
        allocations.append(
            PayoutAllocation(
                event_id=event.event_id,
                contributor_id=item.contributor_id,
                case_id=item.case_id,
                amount_cents=cents,
                weight=round(weight, 6),
            )
        )

    remainder = contributor_pool - used
    if remainder:
        ranked_indexes = sorted(
            range(len(raw_allocations)),
            key=lambda index: raw_allocations[index][2] - math.floor(raw_allocations[index][2]),
            reverse=True,
        )
        for index in ranked_indexes[:remainder]:
            allocation = allocations[index]
            allocations[index] = PayoutAllocation(
                event_id=allocation.event_id,
                contributor_id=allocation.contributor_id,
                case_id=allocation.case_id,
                amount_cents=allocation.amount_cents + 1,
                weight=allocation.weight,
            )

    return PayoutPlan(
        event_id=event.event_id,
        gross_revenue_cents=event.gross_revenue_cents,
        direct_cost_cents=event.direct_cost_cents,
        net_margin_cents=net_margin,
        platform_share_cents=platform_share,
        contributor_pool_cents=contributor_pool,
        allocations=allocations,
    )


def contribution_weight(contribution: ContributionWeight) -> float:
    if contribution.reviewed_level in {DataReadinessLevel.DRL0, DataReadinessLevel.DRL1}:
        return 0.0
    usage_weight = 1.0 + math.log1p(max(contribution.usage_count, 0))
    review_multiplier = {
        DataReadinessLevel.DRL2: 0.35,
        DataReadinessLevel.DRL3: 1.0,
        DataReadinessLevel.DRL4: 1.35,
        DataReadinessLevel.DRL5: 1.8,
    }.get(contribution.reviewed_level, 0.0)
    return max(
        0.0,
        contribution.quality_score
        * contribution.novelty_score
        * contribution.source_trust_score
        * contribution.license_weight
        * usage_weight
        * contribution.duplicate_penalty
        * review_multiplier,
    )
