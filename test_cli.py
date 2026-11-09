import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import weather


class WeatherCliTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.cache = Path(self.temp_dir.name) / "custom weather.json"
        self.cache.write_text(
            json.dumps({
                "daily": {
                    "time": ["2022-01-01", "2022-01-02"],
                    "precipitation_sum": [0, 2],
                    "weather_code": [3, 61],
                }
            }),
            encoding="utf-8",
        )

    def run_cli(self, *arguments):
        return subprocess.run(
            [
                sys.executable,
                str(Path(weather.__file__).resolve()),
                *arguments,
            ],
            cwd=self.temp_dir.name,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )

    def test_actions_use_selected_cache(self):
        expected_outputs = {
            "report": (
                "Total: 2 | Dry: 1 | Rainy: 1 | Rainy-day ratio: 50%\n"
            ),
            "rainfall": "Average rainfall per day: 1.00 mm\n",
            "weather-codes": "3 — 1\n61 — 1\n",
        }

        for action, expected in expected_outputs.items():
            with self.subTest(action=action):
                result = self.run_cli(
                    "--action", action,
                    "--cache", str(self.cache),
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, expected)
                self.assertEqual(result.stderr, "")

    def test_verbose_logs_go_to_stderr(self):
        for flag in ("-v", "--verbose"):
            with self.subTest(flag=flag):
                result = self.run_cli(
                    "--action", "rainfall",
                    "--cache", str(self.cache),
                    flag,
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    result.stdout,
                    "Average rainfall per day: 1.00 mm\n",
                )
                self.assertIn("Loading data from cache", result.stderr)
                self.assertIn(str(self.cache), result.stderr)
                self.assertIn("Processed 2 valid records", result.stderr)

    def test_invalid_cache_exits_without_traceback(self):
        self.cache.write_text("{invalid json", encoding="utf-8")

        result = self.run_cli(
            "--action", "report",
            "--cache", str(self.cache),
        )

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn(
            "Error: Cache file contains invalid JSON",
            result.stderr,
        )
        self.assertNotIn("Traceback", result.stderr)

    def test_unreadable_cache_exits_without_traceback(self):
        # A directory instead of a file triggers a cache read error.
        result = self.run_cli(
            "--action", "report",
            "--cache", self.temp_dir.name,
        )

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("Error: Could not read cache file", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_invalid_or_missing_action_exits_with_usage_error(self):
        for arguments in ([], ["--action", "invalid"]):
            with self.subTest(arguments=arguments):
                result = self.run_cli(*arguments)

                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("usage:", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_refresh_fetches_and_replaces_selected_cache(self):
        fresh_data = {
            "daily": {
                "time": ["2022-01-01"],
                "precipitation_sum": [6],
            }
        }
        arguments = [
            "weather.py",
            "--action", "rainfall",
            "--cache", str(self.cache),
            "--refresh",
        ]

        response = Mock(status_code=200)
        response.json.return_value = fresh_data

        with patch.object(sys, "argv", arguments):
            with patch(
                    "weather.requests.get",
                    return_value=response,
            ) as mock_get:
                with patch(
                        "sys.stdout",
                        new_callable=io.StringIO,
                ) as stdout:
                    weather.main()

        mock_get.assert_called_once_with(
            weather.URL,
            params=weather.PARAMS,
            timeout=15,
        )
        self.assertEqual(
            stdout.getvalue(),
            "Average rainfall per day: 6.00 mm\n",
        )
        self.assertEqual(
            json.loads(self.cache.read_text(encoding="utf-8")),
            fresh_data,
        )


if __name__ == "__main__":
    unittest.main()