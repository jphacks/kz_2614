"""Settings validation and runtime-observation UI checks without live services."""

import os
from pathlib import Path
import shutil
import subprocess
import unittest


class DashboardSettingsUITests(unittest.TestCase):
    def test_vad_settings_and_runtime_observations(self):
        node = os.getenv("NODE") or shutil.which("node")
        if not node:
            self.skipTest("Set NODE or put Node.js on PATH to run dashboard checks")
        result = subprocess.run(
            [node, str(Path(__file__).with_name("dashboard_settings_test.js"))],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
