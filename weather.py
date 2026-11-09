import argparse
import json
import requests
from pathlib import Path #TODO
from datetime import date
import math
import logging
from collections import Counter
import sys
import os
import tempfile

URL = "https://archive-api.open-meteo.com/v1/archive"
#https://open-meteo.com/en/docs/historical-weather-api

PARAMS = {
    "latitude": 51.5072,
    "longitude": -0.1276,
    "start_date": "2022-01-01",
    "end_date": "2022-12-31",
    "daily": "temperature_2m_max,precipitation_sum,weather_code",
    "timezone": "UTC",
}

# {
#     "daily": {
#         "time": ["2022-01-01", "2022-01-02"],
#         "temperature_2m_max": [12.3, 11.8],
#         "precipitation_sum": [0.0, 1.4],
#         "weather_code": [3, 61]
#     }
# }

def fetch_data():
    """Fetch weather data, retrying once after a timeout."""
    for attempt in range(2):
        try:
            response = requests.get(URL, params=PARAMS, timeout=15)
        except requests.exceptions.Timeout:
            if attempt == 0:
                logging.debug("Request timed out. Retrying once.")
                continue

            raise RuntimeError(
                "The API request timed out after two attempts."
            ) from None
        except requests.exceptions.RequestException as error:
            raise RuntimeError(
                f"Could not connect to the API: {error}"
            ) from None

        if response.status_code != 200:
            raise RuntimeError(
                f"The API returned HTTP {response.status_code}."
            )

        try:
            return response.json()
        except ValueError:
            raise RuntimeError(
                "The API returned invalid JSON."
            ) from None

def load_data(cache_path, refresh=False):
    """Load cached data unless a fresh response is requested."""
    logging.debug("Cache path: %s", cache_path.resolve())

    if cache_path.exists() and not refresh:
        logging.debug("Loading data from cache.")

        try:
            with cache_path.open("r", encoding="utf-8") as file:
                return json.load(file)
        except (ValueError, UnicodeError):
            raise RuntimeError(
                f"Cache file contains invalid JSON: {cache_path}"
            ) from None
        except OSError as error:
            raise RuntimeError(
                f"Could not read cache file {cache_path}: {error}"
            ) from None

    logging.debug("Fetching data from the API. Refresh: %s", refresh)
    data = fetch_data()

    temporary_path = None

    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=cache_path.parent,
                prefix=f".{cache_path.name}.",
                suffix=".tmp",
                delete=False,
        ) as file:
            temporary_path = Path(file.name)
            json.dump(data, file, ensure_ascii=False, indent=2)

        os.replace(temporary_path, cache_path)

    except OSError as error:
        raise RuntimeError(
            f"Could not write cache file {cache_path}: {error}"
        ) from None

    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError as error:
                logging.warning(
                    "Could not remove temporary cache file %s: %s",
                    temporary_path,
                    error,
                )

    logging.debug("Response saved to cache.")
    return data

def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Historical daily weather reports for London in 2022."
    )

    parser.add_argument(
        "--action",
        required=True,
        choices=["report", "rainfall", "weather-codes"],
        help="Report to display.",
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable diagnostic logging.",
    )

    parser.add_argument(
        "--cache",
        type=Path,
        default=Path("weather.json"),
        help="Cache file path (default: weather.json).",
    )

    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Ignore the cache and fetch fresh data.",
    )

    return parser.parse_args()

def get_array(daily, name):
    """Return a daily array, or an empty list if it is missing or invalid."""
    values = daily.get(name)
    return values if isinstance(values, list) else []


def get_value(values, index):
    """Return a value, or None if the array is too short."""
    return values[index] if index < len(values) else None


def parse_records(data):
    """Build daily records with valid dates in 2022."""
    if not isinstance(data, dict):
        return []

    daily = data.get("daily")
    if not isinstance(daily, dict):
        return []

    dates = get_array(daily, "time")
    precipitation = get_array(daily, "precipitation_sum")
    weather_codes = get_array(daily, "weather_code")

    records = []

    for index, raw_date in enumerate(dates):
        if not isinstance(raw_date, str):
            logging.debug(
                "Skipping record %d: missing or invalid date %r.",
                index,
                raw_date,
            )
            continue

        try:
            parsed_date = date.fromisoformat(raw_date)
        except ValueError:
            logging.debug(
                "Skipping record %d: invalid date %r.",
                index,
                raw_date,
            )
            continue

        if parsed_date.year != 2022:
            logging.debug(
                "Skipping record %d: date outside 2022: %s.",
                index,
                raw_date,
            )
            continue

        records.append({
            "date": parsed_date,
            "precipitation": get_value(precipitation, index),
            "weather_code": get_value(weather_codes, index),
        })

    return records

def valid_precipitation(value):
    """Return valid precipitation, or None for missing or invalid values."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None

    if not math.isfinite(value) or value < 0:
        return None

    return value


def report(records):
    """Summarize dry and rainy days using valid precipitation values."""
    dry = 0
    rainy = 0

    for record in records:
        precipitation = valid_precipitation(record["precipitation"])

        if precipitation is None:
            continue

        if precipitation == 0:
            dry += 1
        else:
            rainy += 1

    valid_days = dry + rainy
    rainy_ratio = rainy / valid_days * 100 if valid_days else 0

    return (
        f"Total: {len(records)} | Dry: {dry} | Rainy: {rainy} | "
        f"Rainy-day ratio: {rainy_ratio:.0f}%" #zaokraglenie round
    )

def rainfall_report(records):
    """Calculate average precipitation, treating invalid values as zero."""
    total = 0.0

    for record in records:
        precipitation = valid_precipitation(record["precipitation"])

        if precipitation is not None:
            total += precipitation

    average = total / len(records) if records else 0

    return f"Average rainfall per day: {average:.2f} mm"


def weather_codes_report(records):
    """Count weather codes, sorting by count descending and code alphabetically."""
    counts = Counter()

    for record in records:
        code = record["weather_code"]
        label = "unknown" if code is None else str(code)
        counts[label] += 1

    sorted_codes = sorted(
        counts.items(),
        key=lambda item: (-item[1], item[0]),
    )

    return "\n".join(
        f"{code} — {count}"
        for code, count in sorted_codes
    )

def main():
    args = parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    data = load_data(args.cache, refresh=args.refresh)
    records = parse_records(data)

    logging.debug("Processed %d valid records.", len(records))

    if args.action == "report":
        result = report(records)
    elif args.action == "rainfall":
        result = rainfall_report(records)
    else:
        result = weather_codes_report(records)

    print(result)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)