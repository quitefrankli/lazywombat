from collections import Counter
from unittest.mock import PropertyMock, patch

from web_app.app import app
from web_app.config import ConfigManager
from web_app.dev.logs import _read_log_lines, get_logs
from web_app.dev.map import (
    _build_hit_series,
    _collect_client_ip_counts,
    _extract_client_ip,
    _extract_request_path,
    _extract_timestamp,
    _matching_log_events,
    _parse_ip_filters,
    _path_matches_filter,
    map_data,
)


def test_extract_client_ip_accepts_ipv4_and_ipv6():
    assert _extract_client_ip("INFO Processing request: client=1.145.63.44, path=/") == "1.145.63.44"
    assert _extract_client_ip("INFO Processing request: client=2001:4860:4860::8888, path=/") == "2001:4860:4860::8888"
    assert _extract_client_ip("INFO Processing request: client=not-an-ip, path=/") is None


def test_extract_request_path_and_glob_filter():
    line = "INFO Processing request: client=1.1.1.1, path=/loft/cats, method=GET"

    assert _extract_request_path(line) == "/loft/cats"
    assert _path_matches_filter("/loft/cats", "/loft/*")
    assert _path_matches_filter("/loft/cats", "/loft/cats")
    assert not _path_matches_filter("/metrics/", "/loft/*")


def test_extract_request_fields_from_structured_start_event_only():
    started = (
        '2026-07-30 10:00:00,000 INFO worker=1 thread=2 '
        '{"app":"loft","event":"request.started","ip":"8.8.8.8",'
        '"path":"/loft/cats","user":null}\n'
    )
    completed = (
        '2026-07-30 10:00:00,010 INFO worker=1 thread=2 '
        '{"app":"loft","event":"request.completed","ip":"8.8.8.8",'
        '"path":"/loft/cats","status":200,"user":null}\n'
    )

    assert _extract_client_ip(started) == "8.8.8.8"
    assert _extract_request_path(started) == "/loft/cats"
    assert _extract_client_ip(completed) is None
    assert _extract_request_path(completed) is None


def test_build_hit_series_buckets_events_by_hour():
    events = [
        (_extract_timestamp("2026-05-12 10:15:00,000 INFO Processing request: client=1.1.1.1, path=/"), "1.1.1.1"),
        (_extract_timestamp("2026-05-12 10:45:00,000 INFO Processing request: client=1.1.1.1, path=/"), "1.1.1.1"),
        (_extract_timestamp("2026-05-12 12:05:00,000 INFO Processing request: client=8.8.8.8, path=/"), "8.8.8.8"),
    ]

    series = _build_hit_series(events)

    assert series["bucket"] == "hour"
    assert [point["count"] for point in series["points"]] == [2, 0, 1]
    assert series["points"][0]["ips"] == [{"ip": "1.1.1.1", "count": 2}]


def test_collect_client_ip_counts_reads_all_rotated_logs(tmp_path):
    (tmp_path / "web_app.log").write_text(
        "INFO Processing request: client=1.1.1.1, path=/\n"
        "INFO Processing request: client=1.1.1.1, path=/dev\n"
    )
    (tmp_path / "web_app.log.1").write_text(
        "INFO Processing request: client=8.8.8.8, path=/\n"
        "INFO Processing request: client=bad-ip, path=/\n"
    )
    (tmp_path / "other.log").write_text("INFO Processing request: client=9.9.9.9, path=/\n")

    assert _collect_client_ip_counts(tmp_path) == Counter({"1.1.1.1": 2, "8.8.8.8": 1})


def test_read_log_lines_reads_two_most_recent_rotated_logs_oldest_first(tmp_path):
    (tmp_path / "web_app.log").write_text("current\n")
    (tmp_path / "web_app.log.1").write_text("previous\n")
    (tmp_path / "web_app.log.2").write_text("two\n")
    (tmp_path / "web_app.log.3").write_text("three\n")
    (tmp_path / "web_app.log.4").write_text("four\n")
    (tmp_path / "web_app.log.5").write_text("too old\n")
    (tmp_path / "web_app.log.10").write_text("oldest\n")
    (tmp_path / "web_app.log.bak").write_text("ignored\n")

    assert _read_log_lines(tmp_path) == ["previous", "current"]


def test_read_log_lines_hides_suppressed_request_paths(tmp_path):
    (tmp_path / "web_app.log").write_text(
        "2026-05-25 17:41:09,281 INFO Processing request: client=1.1.1.1, path=/dev/terminal/input, method=POST\n"
        "2026-05-25 17:41:10,281 INFO Processing request: client=1.1.1.1, path=/example, method=GET\n"
    )

    assert _read_log_lines(tmp_path) == [
        "2026-05-25 17:41:10,281 INFO Processing request: client=1.1.1.1, path=/example, method=GET"
    ]


def test_read_log_lines_hides_structured_lifecycle_logs_for_suppressed_paths(tmp_path):
    lifecycle_started = (
        '2026-07-30 10:00:00,000 INFO {"app":"dev","event":"request.started",'
        '"path":"/dev/terminal/input"}'
    )
    lifecycle_completed = (
        '2026-07-30 10:00:00,010 INFO {"app":"dev","event":"request.completed",'
        '"path":"/dev/terminal/input","status":200}'
    )
    audit_event = (
        '2026-07-30 10:00:00,005 INFO {"app":"dev","event":"terminal.input_written",'
        '"bytes":4}'
    )
    regular_request = (
        '2026-07-30 10:00:01,000 INFO {"app":"dev","event":"request.started",'
        '"path":"/example"}'
    )
    (tmp_path / "web_app.log").write_text(
        "\n".join((lifecycle_started, audit_event, lifecycle_completed, regular_request))
    )

    assert _read_log_lines(tmp_path) == [audit_event, regular_request]


def test_get_logs_returns_all_selected_files_without_default_line_limit(tmp_path):
    (tmp_path / "web_app.log").write_text("".join(f"line {idx}\n" for idx in range(2100)))

    with (
        app.test_request_context("/dev/logs"),
        patch.object(
            ConfigManager,
            "log_file_path",
            new_callable=PropertyMock,
            return_value=tmp_path / "web_app.log",
        ),
    ):
        payload = get_logs().get_json()

    assert payload["start"] == 0
    assert payload["total"] == 2100
    assert len(payload["lines"]) == 2100


def test_collect_client_ip_counts_can_filter_by_path_glob(tmp_path):
    (tmp_path / "web_app.log").write_text(
        "INFO Processing request: client=1.1.1.1, path=/loft/cats, method=GET\n"
        "INFO Processing request: client=1.1.1.1, path=/metrics/, method=GET\n"
        "INFO Processing request: client=8.8.8.8, path=/loft/dogs, method=GET\n"
    )

    assert _collect_client_ip_counts(tmp_path, "/loft/*") == Counter({"1.1.1.1": 1, "8.8.8.8": 1})


def test_matching_log_events_can_filter_by_range_and_ip_glob(tmp_path):
    (tmp_path / "web_app.log").write_text(
        "2026-05-12 09:00:00,000 INFO Processing request: client=1.1.1.1, path=/loft/cats, method=GET\n"
        "2026-05-12 10:00:00,000 INFO Processing request: client=8.8.8.8, path=/loft/cats, method=GET\n"
        "2026-05-12 11:00:00,000 INFO Processing request: client=1.145.63.44, path=/loft/cats, method=GET\n"
    )

    events = _matching_log_events(
        tmp_path,
        "/loft/*",
        _extract_timestamp("2026-05-12 09:30:00,000 INFO x"),
        _extract_timestamp("2026-05-12 11:30:00,000 INFO x"),
        _parse_ip_filters("1.145.*"),
    )

    assert events == [(_extract_timestamp("2026-05-12 10:00:00,000 INFO x"), "8.8.8.8")]


def test_map_data_returns_located_public_ips_and_summary(tmp_path):
    (tmp_path / "web_app.log").write_text(
        "2026-05-12 10:00:00,000 INFO Processing request: client=8.8.8.8, path=/\n"
        "2026-05-12 10:30:00,000 INFO Processing request: client=8.8.8.8, path=/dev\n"
        "2026-05-12 11:00:00,000 INFO Processing request: client=127.0.0.1, path=/dev\n"
    )
    geo = {
        "8.8.8.8": {
            "country": "United States",
            "country_code": "US",
            "region": "California",
            "city": "Mountain View",
            "lat": 37.4056,
            "lon": -122.0775,
            "isp": "Google",
            "proxy": False,
            "hosting": True,
        }
    }

    with (
        app.test_request_context(),
        patch.object(
            ConfigManager,
            "log_file_path",
            new_callable=PropertyMock,
            return_value=tmp_path / "web_app.log",
        ),
        patch("web_app.dev.map._geolocate_ips", return_value=geo),
    ):
        payload = map_data().get_json()

    assert payload["summary"]["unique_ips"] == 2
    assert payload["summary"]["public_ips"] == 1
    assert payload["summary"]["located_ips"] == 1
    assert payload["summary"]["request_count"] == 3
    assert payload["series"]["points"]
    assert payload["points"][0]["ip"] == "8.8.8.8"
    assert payload["points"][0]["count"] == 2


def test_map_data_applies_path_filter(tmp_path):
    (tmp_path / "web_app.log").write_text(
        "INFO Processing request: client=8.8.8.8, path=/loft/cats, method=GET\n"
        "INFO Processing request: client=1.1.1.1, path=/metrics/, method=GET\n"
    )
    geo = {
        "8.8.8.8": {
            "country": "United States",
            "country_code": "US",
            "region": "California",
            "city": "Mountain View",
            "lat": 37.4056,
            "lon": -122.0775,
            "isp": "Google",
            "proxy": False,
            "hosting": True,
        }
    }

    with (
        app.test_request_context("/dev/map-data?path=/loft/*"),
        patch.object(
            ConfigManager,
            "log_file_path",
            new_callable=PropertyMock,
            return_value=tmp_path / "web_app.log",
        ),
        patch("web_app.dev.map._geolocate_ips", return_value=geo),
    ):
        payload = map_data().get_json()

    assert payload["summary"]["path_filter"] == "/loft/*"
    assert payload["summary"]["unique_ips"] == 1
    assert payload["points"][0]["ip"] == "8.8.8.8"
