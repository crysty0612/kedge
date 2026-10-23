"""
Tests for host configuration discovery and cron scheduling guardrails.
"""

from pathlib import Path
import tempfile
import unittest

from kedge.discovery.host_sniff import HostSniffer
from kedge.scheduler.cron_guard import CronGuard


class TestHostAndScheduler(unittest.TestCase):
    def test_cron_guard_rate_limit_validation(self):
        # 1. Reject sub-hourly every-minute wildcard: * * * * *
        ok, msg = CronGuard.validate_expression("* * * * *")
        self.assertFalse(ok)
        self.assertIn("Sub-hourly", msg)

        # 2. Reject sub-hourly interval: */15 * * * *
        ok, msg = CronGuard.validate_expression("*/15 * * * *")
        self.assertFalse(ok)
        self.assertIn("too frequent", msg)

        # 3. Reject invalid field count
        ok, msg = CronGuard.validate_expression("0 3 * *")
        self.assertFalse(ok)

        # 4. Accept hourly: 0 * * * *
        ok, msg = CronGuard.validate_expression("0 * * * *")
        self.assertTrue(ok)

        # 5. Accept daily: 0 3 * * *
        ok, msg = CronGuard.validate_expression("0 3 * * *")
        self.assertTrue(ok)

        # 6. Accept twice daily: 0 3,15 * * *
        ok, msg = CronGuard.validate_expression("0 3,15 * * *")
        self.assertTrue(ok)

    def test_host_sniffer_system_bom(self):
        items = HostSniffer.sniff_system_bom()
        self.assertGreater(len(items), 0)
        bom_item = next((i for i in items if i.source_path == "__host_bom__"), None)
        self.assertIsNotNone(bom_item)
        self.assertEqual(bom_item.relative_path, "system/bom.json")


if __name__ == "__main__":
    unittest.main()
