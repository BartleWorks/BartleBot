"""Unit tests for the page-fault delta in tools/probe_chat_log_size.py.

PageFaultCount is a DWORD, and the client that ran hottest in the end-of-fight lag work wrapped it inside a
single overnight run — reporting 5.46M after 9h47m when the truth was ~4.30 billion. A
delta that reads as a plain measurement when it is really a wrapped counter is worse than
no number at all, which is what these pin.
"""

from tools.probe_chat_log_size import fault_delta

_DWORD_MAX = 0xFFFFFFFF


def test_ordinary_delta_is_the_difference():
    assert fault_delta(1_000, 5_400) == (4_400, False)


def test_no_change_is_zero_and_unwrapped():
    assert fault_delta(9_999, 9_999) == (0, False)


def test_a_counter_that_went_backwards_is_reconstructed_across_the_wrap():
    # 100 faults short of rolling over, then 900 more counted after it did.
    span, wrapped = fault_delta(_DWORD_MAX - 99, 900)
    assert (span, wrapped) == (1_000, True)


def test_wrapping_exactly_onto_zero_counts_the_last_fault():
    span, wrapped = fault_delta(_DWORD_MAX, 0)
    assert (span, wrapped) == (1, True)


def test_the_reported_case_reconstructs_to_billions():
    # ~4.30 billion, reported as 5.46M by the unwrapped subtraction.
    span, wrapped = fault_delta(0, 5_464_000)
    assert (span, wrapped) == (5_464_000, False)
    span, wrapped = fault_delta(_DWORD_MAX - 1_000_000, 5_464_000)
    assert wrapped is True
    assert span == 6_464_001
