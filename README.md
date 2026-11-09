# London Weather CLI

Daily weather reports for London in 2022 using the Open-Meteo Archive API.
No API key required.

## Setup

Requires Python 3.10+.

```bash
python -m pip install -r requirements.txt
```

## Usage

```bash
python weather.py --action report
python weather.py --action rainfall -v
python weather.py --action weather-codes --refresh --cache .cache/weather.json
```

The default cache is `weather.json`. Use `--refresh` to fetch fresh data
and `-v` for diagnostic logging.

Only valid dates in 2022 are included. The rainy-day ratio excludes
invalid precipitation; average rainfall treats it as zero.

Requests time out after 15 seconds and are retried once on timeout.

## Tests

```bash
python -m unittest -v
```

Tests use mocked HTTP responses and temporary files.

## API documentation

https://open-meteo.com/en/docs/historical-weather-api