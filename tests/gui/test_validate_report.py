from bartlebot.gui.main_window import validation_report


def test_no_problems_says_so():
    assert validation_report([], []) == ("Validate Bot", "No problems found.")


def test_lists_errors_then_warnings():
    _, text = validation_report(["bad edge"], ["unreachable"])
    assert text == "Errors:\n• bad edge\n\nWarnings:\n• unreachable"


def test_warnings_only():
    _, text = validation_report([], ["unreachable"])
    assert text == "Warnings:\n• unreachable"
