import fnmatch
import ipaddress
import json
import re
import time

from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import requests
from flask import jsonify, request

from web_app.config import ConfigManager
from web_app.helpers import limiter

_CLIENT_RE = re.compile(r"\bclient=([^,\s]+)")
_PATH_RE = re.compile(r"\bpath=([^,\s]+)")
_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d{3}")
_geo_cache: dict[str, tuple[float, dict | None]] = {}


def _extract_structured_event(line: str) -> dict | None:
    json_start = line.find("{")
    if json_start == -1:
        return None
    try:
        payload = json.loads(line[json_start:])
    except (json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _normalize_client_ip(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    try:
        return str(ipaddress.ip_address(raw.strip("[]")))
    except ValueError:
        return None


def _iter_log_files(logs_dir: Path) -> list[Path]:
    return sorted(p for p in logs_dir.glob("web_app.log*") if p.is_file())


def _extract_client_ip(line: str) -> str | None:
    payload = _extract_structured_event(line)
    if payload is not None:
        if payload.get("event") != "request.started":
            return None
        return _normalize_client_ip(payload.get("ip"))

    match = _CLIENT_RE.search(line)
    if not match:
        return None
    return _normalize_client_ip(match.group(1))


def _extract_request_path(line: str) -> str | None:
    payload = _extract_structured_event(line)
    if payload is not None:
        if payload.get("event") != "request.started":
            return None
        path = payload.get("path")
        return path if isinstance(path, str) else None

    match = _PATH_RE.search(line)
    if not match:
        return None
    return match.group(1)


def _extract_timestamp(line: str) -> datetime | None:
    match = _TS_RE.match(line)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _path_matches_filter(path: str, path_filter: str | None) -> bool:
    if not path_filter:
        return True
    pattern = path_filter.strip()
    if not pattern:
        return True
    if any(char in pattern for char in "*?[]"):
        return fnmatch.fnmatchcase(path, pattern)
    return path == pattern


def _parse_datetime_param(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_interval(value: str | None) -> str | None:
    return value if value in {"hour", "day"} else None


def _parse_ip_filters(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _ip_matches_filters(ip: str, filters: list[str]) -> bool:
    for pattern in filters:
        if any(char in pattern for char in "*?[]"):
            if fnmatch.fnmatchcase(ip, pattern):
                return True
        elif ip == pattern:
            return True
    return False


def _event_in_range(timestamp: datetime | None, start: datetime | None, end: datetime | None) -> bool:
    if start is None and end is None:
        return True
    if timestamp is None:
        return False
    if start and timestamp < start:
        return False
    if end and timestamp > end:
        return False
    return True


def _matching_log_events(
    logs_dir: Path,
    path_filter: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    excluded_ips: list[str] | None = None,
) -> list[tuple[datetime | None, str]]:
    events = []
    excluded_ips = excluded_ips or []
    for path in _iter_log_files(logs_dir):
        try:
            with path.open(errors='replace') as handle:
                for line in handle:
                    request_path = _extract_request_path(line)
                    if request_path is None or not _path_matches_filter(request_path, path_filter):
                        continue
                    ip = _extract_client_ip(line)
                    if not ip or _ip_matches_filters(ip, excluded_ips):
                        continue
                    timestamp = _extract_timestamp(line)
                    if _event_in_range(timestamp, start, end):
                        events.append((timestamp, ip))
        except OSError:
            continue
    return events


def _collect_client_ip_counts(logs_dir: Path, path_filter: str | None = None) -> Counter:
    counts = Counter()
    for _, ip in _matching_log_events(logs_dir, path_filter):
        counts[ip] += 1
    return counts


def _floor_time(value: datetime, interval: str) -> datetime:
    if interval == "day":
        return value.replace(hour=0, minute=0, second=0, microsecond=0)
    return value.replace(minute=0, second=0, microsecond=0)


def _next_time(value: datetime, interval: str) -> datetime:
    if interval == "day":
        return value + timedelta(days=1)
    return value + timedelta(hours=1)


def _build_hit_series(events: list[tuple[datetime | None, str]], interval: str | None = None) -> dict:
    dated_events = sorted((ts, ip) for ts, ip in events if ts is not None)
    if not dated_events:
        return {"bucket": interval or "hour", "points": []}

    start = dated_events[0][0]
    end = dated_events[-1][0]
    bucket = interval or ("day" if (end - start) > timedelta(days=4) else "hour")
    counts = Counter(_floor_time(ts, bucket) for ts, _ in dated_events)
    ips_by_bucket: dict[datetime, Counter] = {}
    for ts, ip in dated_events:
        key = _floor_time(ts, bucket)
        ips_by_bucket.setdefault(key, Counter())[ip] += 1

    points = []
    cursor = _floor_time(start, bucket)
    stop = _floor_time(end, bucket)
    while cursor <= stop:
        points.append({
            "time": cursor.isoformat(),
            "count": counts[cursor],
            "ips": [
                {"ip": ip, "count": count}
                for ip, count in ips_by_bucket.get(cursor, Counter()).most_common(20)
            ],
        })
        cursor = _next_time(cursor, bucket)
    return {"bucket": bucket, "points": points}


def _is_public_ip(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_global
    except ValueError:
        return False


def _cached_geo(ip: str, ttl_s: int) -> dict | None:
    cached = _geo_cache.get(ip)
    if not cached:
        return None
    cached_at, location = cached
    if time.time() - cached_at > ttl_s:
        _geo_cache.pop(ip, None)
        return None
    return location


def _geolocate_ips(ips: list[str]) -> dict[str, dict]:
    config = ConfigManager()
    results: dict[str, dict] = {}
    missing = []
    for ip in ips:
        cached = _cached_geo(ip, config.dev.map_geo_cache_ttl_s)
        if cached is not None:
            results[ip] = cached
        elif ip in _geo_cache:
            continue
        else:
            missing.append(ip)

    for idx in range(0, len(missing), config.dev.map_geo_batch_size):
        batch = missing[idx:idx + config.dev.map_geo_batch_size]
        try:
            response = requests.post(
                config.dev.map_geo_url,
                params={"fields": "status,message,query,country,countryCode,regionName,city,lat,lon,isp,org,as,proxy,hosting"},
                json=batch,
                timeout=config.dev.map_geo_timeout_s,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError):
            for ip in batch:
                _geo_cache[ip] = (time.time(), None)
            continue

        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            ip = item.get("query")
            if not ip:
                continue
            if item.get("status") == "success" and item.get("lat") is not None and item.get("lon") is not None:
                location = {
                    "country": item.get("country") or "",
                    "country_code": item.get("countryCode") or "",
                    "region": item.get("regionName") or "",
                    "city": item.get("city") or "",
                    "lat": item.get("lat"),
                    "lon": item.get("lon"),
                    "isp": item.get("isp") or item.get("org") or item.get("as") or "",
                    "proxy": bool(item.get("proxy")),
                    "hosting": bool(item.get("hosting")),
                }
                results[ip] = location
                _geo_cache[ip] = (time.time(), location)
            else:
                _geo_cache[ip] = (time.time(), None)
    return results


def map_data():
    config = ConfigManager()
    path_filter = request.args.get('path', '').strip() or None
    start = _parse_datetime_param(request.args.get('from'))
    end = _parse_datetime_param(request.args.get('to'))
    interval = _parse_interval(request.args.get('interval'))
    excluded_ips = _parse_ip_filters(request.args.get('exclude_ips'))
    events = _matching_log_events(
        config.log_file_path.parent,
        path_filter,
        start,
        end,
        excluded_ips,
    )
    counts = Counter(ip for _, ip in events)
    ranked_ips = [ip for ip, _ in counts.most_common(config.dev.map_max_ips)]
    public_ips = [ip for ip in ranked_ips if _is_public_ip(ip)]
    locations = _geolocate_ips(public_ips)

    points = []
    for ip in ranked_ips:
        location = locations.get(ip)
        if not location:
            continue
        points.append({
            "ip": ip,
            "count": counts[ip],
            **location,
        })

    return jsonify({
        "points": points,
        "series": _build_hit_series(events, interval),
        "summary": {
            "unique_ips": len(counts),
            "shown_ips": len(ranked_ips),
            "public_ips": len(public_ips),
            "located_ips": len(points),
            "request_count": sum(counts.values()),
            "limited": len(counts) > len(ranked_ips),
            "path_filter": path_filter,
            "from": start.isoformat() if start else None,
            "to": end.isoformat() if end else None,
            "interval": interval or "auto",
            "excluded_ips": excluded_ips,
        },
    })


def register_map_routes(dev_api):
    dev_api.add_url_rule('/map-data', view_func=limiter.exempt(map_data), methods=['GET'])
