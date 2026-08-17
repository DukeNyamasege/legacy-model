from __future__ import annotations

import sys
import unittest


# These three cases assert the presentation that Action 6F-1 intentionally
# retired. They are replaced by tests.test_final_ui_6f1, while every other test
# in the two large legacy modules still runs here.
RETIRED_PRESENTATION_TESTS = {
    "test_strategy_logic.DashboardMetricsTests.test_dashboard_shell_is_builder_first_and_has_no_legacy_panels",
    "test_strategy_logic.DashboardMetricsTests.test_builder_dashboard_css_supports_light_dark_and_mobile",
    "test_rf_dir5.RiseFallContractTests.test_public_simulator_and_viewer_reset_are_wired_safely",
}


def iter_cases(suite: unittest.TestSuite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from iter_cases(item)
        else:
            yield item


def main() -> int:
    loaded = unittest.defaultTestLoader.loadTestsFromNames(
        ["test_rf_dir5", "test_strategy_logic"]
    )
    kept = unittest.TestSuite()
    retired: list[str] = []
    for case in iter_cases(loaded):
        test_id = case.id()
        if test_id in RETIRED_PRESENTATION_TESTS:
            retired.append(test_id)
            continue
        kept.addTest(case)

    missing = sorted(RETIRED_PRESENTATION_TESTS.difference(retired))
    if missing:
        print(
            "Legacy presentation filter contract changed; expected tests were not found: "
            + ", ".join(missing),
            file=sys.stderr,
        )
        return 2

    print(
        "6F-1 retired presentation assertions replaced by tests.test_final_ui_6f1: "
        + ", ".join(sorted(retired)),
        file=sys.stderr,
    )
    result = unittest.TextTestRunner(verbosity=0).run(kept)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
