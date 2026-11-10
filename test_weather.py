import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch
import requests
import weather

class WeatherTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.cache = Path(self.temp_dir.name) / "weather.json"

        self.data = {
            "daily": {
                "time": ["2022-01-01", "2022-01-02"],
                "precipitation_sum": [0.0, 2.0],
                "weather_code": [3, 61],
            }
        }

    def make_response(self, data=None, status=200):
        response = Mock()
        response.status_code = status
        response.json.return_value = (
            self.data if data is None else data
        )
        return response

    @patch("weather.requests.get")
    def test_successful_fetch_and_cache_write(self, mock_get):
        mock_get.return_value = self.make_response()

        result = weather.load_data(self.cache)

        self.assertEqual(result, self.data)
        self.assertEqual(
            json.loads(self.cache.read_text(encoding="utf-8")),
            self.data,
        )
        mock_get.assert_called_once_with(
            "https://archive-api.open-meteo.com/v1/archive",
            params={
                "latitude": 51.5072,
                "longitude": -0.1276,
                "start_date": "2022-01-01",
                "end_date": "2022-12-31",
                "daily": "temperature_2m_max,precipitation_sum,weather_code",
                "timezone": "UTC",
            },
            timeout=15,
        )

    @patch("weather.requests.get")
    def test_existing_cache_is_reused(self, mock_get):
        self.cache.write_text(
            json.dumps(self.data),
            encoding="utf-8",
        )

        result = weather.load_data(self.cache)

        self.assertEqual(result, self.data)
        mock_get.assert_not_called()

    @patch("weather.requests.get")
    def test_refresh_replaces_existing_cache(self, mock_get):
        self.cache.write_text('{"old": true}', encoding="utf-8")
        mock_get.return_value = self.make_response()

        result = weather.load_data(self.cache, refresh=True)

        self.assertEqual(result, self.data)
        self.assertEqual(
            json.loads(self.cache.read_text(encoding="utf-8")),
            self.data,
        )
        mock_get.assert_called_once()

    @patch("weather.requests.get")
    def test_timeout_then_success(self, mock_get):
        mock_get.side_effect = [
            requests.exceptions.Timeout(),
            self.make_response(),
        ]

        result = weather.fetch_data()

        self.assertEqual(result, self.data)
        self.assertEqual(mock_get.call_count, 2)

    @patch("weather.requests.get")
    def test_two_timeouts_raise_error(self, mock_get):
        mock_get.side_effect = requests.exceptions.Timeout()

        with self.assertRaisesRegex(RuntimeError, "two attempts"):
            weather.fetch_data()

        self.assertEqual(mock_get.call_count, 2)

    @patch("weather.requests.get")
    def test_non_200_response_is_not_retried(self, mock_get):
        mock_get.return_value = self.make_response(status=503)

        with self.assertRaisesRegex(RuntimeError, "HTTP 503"):
            weather.load_data(self.cache)

        mock_get.assert_called_once()
        self.assertFalse(self.cache.exists())

    @patch("weather.requests.get")
    def test_invalid_cached_json_raises_error(self, mock_get):
        self.cache.write_text("{invalid json", encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "invalid JSON"):
            weather.load_data(self.cache)

        mock_get.assert_not_called()

    def test_missing_daily_or_arrays(self):
        for data in ({}, {"daily": None}, {"daily": {}}):
            with self.subTest(data=data):
                self.assertEqual(weather.parse_records(data), [])

        records = weather.parse_records({
            "daily": {"time": ["2022-01-01"]}
        })

        self.assertEqual(len(records), 1)
        self.assertIsNone(records[0]["precipitation"])
        self.assertIsNone(records[0]["weather_code"])

    def test_mismatched_arrays_keep_all_valid_dates(self):
        records = weather.parse_records({
            "daily": {
                "time": ["2022-01-01", "2022-01-02"],
                "precipitation_sum": [1.5],
                "weather_code": [],
            }
        })

        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["precipitation"], 1.5)
        self.assertIsNone(records[1]["precipitation"])
        self.assertIsNone(records[1]["weather_code"])

    def test_invalid_and_out_of_year_dates_are_skipped(self):
        records = weather.parse_records({
            "daily": {
                "time": [
                    "invalid",
                    None,
                    "2022-02-30",
                    "2021-12-31",
                    "2022-01-01",
                ],
                "precipitation_sum": [9, 9, 9, 9, 2],
            }
        })

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["date"], date(2022, 1, 1))
        self.assertEqual(records[0]["precipitation"], 2)

    def test_report_excludes_invalid_precipitation_from_ratio(self):
        records = [
            {"precipitation": value}
            for value in [
                0, 2, None, "invalid", -1,
                float("nan"), float("inf"), True,
            ]
        ]

        self.assertEqual(
            weather.report(records),
            "Total: 8 | Dry: 1 | Rainy: 1 | Rainy-day ratio: 50%",
        )

    def test_rainfall_uses_all_valid_date_records(self):
        records = [
            {"precipitation": 0},
            {"precipitation": 6},
            {"precipitation": None},
            {"precipitation": "invalid"},
        ]

        self.assertEqual(
            weather.rainfall_report(records),
            "Average rainfall per day: 1.50 mm",
        )

    def test_weather_codes_sort_by_count_then_alphabetically(self):
        records = [
            {"weather_code": code}
            for code in [3, 3, 2, 10, None]
        ]

        self.assertEqual(
            weather.weather_codes_report(records),
            "3 — 2\n10 — 1\n2 — 1\nunknown — 1",
        )

    def test_empty_reports(self):
        self.assertEqual(
            weather.report([]),
            "Total: 0 | Dry: 0 | Rainy: 0 | Rainy-day ratio: 0%",
        )
        self.assertEqual(
            weather.rainfall_report([]),
            "Average rainfall per day: 0.00 mm",
        )
        self.assertEqual(weather.weather_codes_report([]), "")

    @patch("weather.fetch_data")
    def test_failed_refresh_preserves_existing_cache(self, mock_fetch):
        original = json.dumps(self.data)
        self.cache.write_text(original, encoding="utf-8")
        mock_fetch.return_value = {"daily": {}}

        with patch("weather.json.dump", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(RuntimeError, "Could not write"):
                weather.load_data(self.cache, refresh=True)

        self.assertEqual(
            self.cache.read_text(encoding="utf-8"),
            original,
        )
        self.assertEqual(
            set(self.cache.parent.iterdir()),
            {self.cache},
        )

if __name__ == "__main__":
    unittest.main()