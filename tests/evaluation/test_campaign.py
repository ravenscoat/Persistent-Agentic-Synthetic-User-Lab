from synthetic_lab.evaluation import PERSONA_SCENARIOS, scenario_by_id


def test_campaign_covers_each_initial_persona_and_seeded_fault() -> None:
    assert {scenario.persona_kind for scenario in PERSONA_SCENARIOS} == {
        "new_customer", "power_user", "administrator", "interrupted_user",
    }
    assert {scenario.fault for scenario in PERSONA_SCENARIOS} == {
        "trial_expires_day_5", "duplicate_charge", "owner_transfer_leak", "onboarding_resets",
    }
    assert scenario_by_id("ownership_transfer").return_route == "/api/owner-only"
