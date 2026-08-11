"""Tests for the Weather tool (F023)."""

import json
from unittest.mock import patch

import pytest

from collie_core.tools.weather import WeatherTool


def _make_response(current: dict | None = None, daily: dict | None = None) -> dict:
    return {
        "current": current or {},
        "daily": daily or {},
    }


def _make_geo_response(name: str = "Berlin", country: str = "Germany") -> dict:
    return {
        "results": [
            {
                "name": name,
                "country": country,
                "latitude": 52.52,
                "longitude": 13.41,
                "timezone": "Europe/Berlin",
            }
        ],
    }


_CURRENT = {
    "temperature_2m": 18.5,
    "relative_humidity_2m": 55,
    "apparent_temperature": 20.1,
    "weather_code": 2,
    "wind_speed_10m": 12.3,
    "wind_direction_10m": 270,
    "is_day": 1,
}

_DAILY = {
    "time": ["2026-07-18", "2026-07-19"],
    "weather_code": [2, 61],
    "temperature_2m_max": [24.0, 19.0],
    "temperature_2m_min": [15.0, 13.0],
    "precipitation_probability_max": [10, 80],
    "wind_speed_10m_max": [15.0, 25.0],
}


@pytest.mark.asyncio
async def test_weather_current() -> None:
    tool = WeatherTool()

    with (
        patch("collie_core.tools.weather._api_get") as mock_get,
    ):
        mock_get.side_effect = [
            _make_geo_response(),
            _make_response(current=_CURRENT),
        ]

        result = await tool.execute(location="Berlin")
        data = json.loads(str(result))
        current = data["current"]

        assert data["location"] == "Berlin, Germany"
        assert data["card_type"] == "weather"
        assert current["temp"] == 18.5
        assert current["feels_like"] == 20.1
        assert current["humidity"] == 55
        assert current["wind_speed"] == 12.3
        assert "cloudy" in current["condition"].lower()
        assert current["icon"] == "⛅"
        assert "forecast" not in data


@pytest.mark.asyncio
async def test_weather_forecast() -> None:
    tool = WeatherTool()

    with (
        patch("collie_core.tools.weather._api_get") as mock_get,
    ):
        mock_get.side_effect = [
            _make_geo_response(),
            _make_response(current=_CURRENT, daily=_DAILY),
        ]

        result = await tool.execute(location="Berlin", days=2)
        data = json.loads(str(result))
        forecast = data["forecast"]

        assert len(forecast) == 2
        assert forecast[0]["date"] == "2026-07-18"
        assert forecast[0]["high"] == 24.0
        assert forecast[0]["low"] == 15.0
        assert forecast[0]["rain_chance"] == 10
        assert forecast[0]["icon"] == "⛅"

        assert forecast[1]["date"] == "2026-07-19"
        assert forecast[1]["condition"] == "Slight rain"
        assert forecast[1]["icon"] == "🌧️"
        assert forecast[1]["rain_chance"] == 80


@pytest.mark.asyncio
async def test_weather_unknown_location() -> None:
    tool = WeatherTool()

    with (
        patch("collie_core.tools.weather._api_get") as mock_get,
    ):
        mock_get.return_value = {"results": []}
        result = await tool.execute(location="XyzzyNotARealPlace")
        assert "couldn't find" in str(result).lower()


@pytest.mark.asyncio
async def test_weather_empty_location() -> None:
    tool = WeatherTool()
    result = await tool.execute(location="")
    assert "need a location" in str(result).lower()


@pytest.mark.asyncio
async def test_weather_api_error() -> None:
    tool = WeatherTool()

    with (
        patch("collie_core.tools.weather._api_get") as mock_get,
    ):
        mock_get.return_value = _make_geo_response()
        # Second call (forecast) fails

        with (
            patch("collie_core.tools.weather._api_get") as mock_get2,
        ):
            mock_get2.side_effect = [
                _make_geo_response(),
                Exception("Connection refused"),
            ]

            result = await tool.execute(location="Berlin")
            assert "weather station" in str(result).lower()


@pytest.mark.asyncio
async def test_weather_icon_variants() -> None:
    """Smoke test all WMO code icons produce valid strings."""
    from collie_core.tools.weather import _WMO_CODES, _icon_from_code

    for code in _WMO_CODES:
        icon = _icon_from_code(code, is_day=False)
        assert icon and len(icon) > 0
