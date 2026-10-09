"""Unit tests for the relog policy in bartlebot/clients/relog.py.

The policy is pure on purpose: these run with no clock, no client, and no settings.
"""

import pytest

from bartlebot.clients.relog import GDI_QUOTA, GDI_RELOG_THRESHOLD, gdi_relog_due, relog_due

HOUR = 3600.0


class TestRelogDue:
    def test_not_due_before_the_interval_elapses(self):
        assert relog_due(now=3.9 * HOUR, last_relog_at=0.0, interval_hours=4.0) is False

    def test_due_once_the_interval_elapses(self):
        assert relog_due(now=4.0 * HOUR, last_relog_at=0.0, interval_hours=4.0) is True

    def test_due_measures_from_the_last_relog_not_from_zero(self):
        # Six hours into the run, but the last cycle was one hour ago.
        assert relog_due(now=6.0 * HOUR, last_relog_at=5.0 * HOUR, interval_hours=4.0) is False
        assert relog_due(now=9.0 * HOUR, last_relog_at=5.0 * HOUR, interval_hours=4.0) is True

    def test_zero_means_off_however_long_the_run(self):
        assert relog_due(now=100.0 * HOUR, last_relog_at=0.0, interval_hours=0.0) is False

    @pytest.mark.parametrize("interval", [-0.0, -1.0])
    def test_a_negative_interval_from_a_hand_edited_file_is_also_off(self, interval):
        assert relog_due(now=100.0 * HOUR, last_relog_at=0.0, interval_hours=interval) is False

    def test_a_fractional_interval_is_honored(self):
        # 0.1 hours is what the feature is verified with by hand.
        assert relog_due(now=359.0, last_relog_at=0.0, interval_hours=0.1) is False
        assert relog_due(now=360.0, last_relog_at=0.0, interval_hours=0.1) is True


class TestGdiRelogDue:
    def test_a_healthy_count_is_not_due(self):
        assert gdi_relog_due(3_200) is False

    def test_the_threshold_itself_is_due(self):
        assert gdi_relog_due(GDI_RELOG_THRESHOLD) is True

    def test_a_client_at_the_quota_is_due(self):
        assert gdi_relog_due(GDI_QUOTA) is True

    def test_a_failed_read_is_never_a_trigger(self):
        # 0 means GetGuiResources failed; a live client always holds thousands.
        assert gdi_relog_due(0) is False
        assert gdi_relog_due(-1) is False

    def test_the_threshold_leaves_headroom_below_the_quota(self):
        assert GDI_RELOG_THRESHOLD < GDI_QUOTA
