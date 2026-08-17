from __future__ import annotations

import sys
import unittest


# These three methods assert presentation that Action 6F-1 intentionally retired.
# Match by method-name suffix rather than historical class name so the filter is
# exact without depending on how unittest qualifies the containing class/module.
RETIRED_PRESENTATION_SUFFIXES = {
    ".test_dashboard_shell_is_builder_first_and_has_no_legacy_panels",
    ".test_builder_dashboard_css_supports_light_dark_and_mobile",
    ".test_public_simulator_and_viewer_reset_are_wired_safely",
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
    retired_ids: list[str] = []
    matched_suffixes: set[str] = set()

    for case in iter_cases(loaded):
        test_id = case.id()
        matched = next(
            (suffix for suffix in RETIRED_PRESENTATION_SUFFIXES if test_id.endswith(suffix)),
            None,
        )
        if matched is not None:
            retired_ids.append(test_id)
            matched_suffixes.add(matched)
            continue
        kept.addTest(case)

    missing = sorted(RETIRED_PRESENTATION_SUFFIXES.difference(matched_suffixes))
    if missing:
        print(
            "Legacy presentation filter contract changed; expected methods were not found: "
            + ", ".join(missing),
            file=sys.stderr,
        )
        return 2

    if len(retired_ids) != len(RETIRED_PRESENTATION_SUFFIXES):
        print(
            "Legacy presentation filter matched an unexpected number of tests: "
            + ", ".join(sorted(retired_ids)),
            file=sys.stderr,
        )
        return 2

    print(
        "6F-1 retired presentation assertions replaced by tests.test_final_ui_6f1: "
        + ", ".join(sorted(retired_ids)),
        file=sys.stderr,
    )
    result = unittest.TextTestRunner(verbosity=0).run(kept)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
