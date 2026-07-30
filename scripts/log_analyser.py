import ipaddress
import json

from collections import Counter
from pathlib import Path


LOGS_DIR = Path("logs")


def _request_client_ip(line: str) -> str | None:
    json_start = line.find("{")
    if json_start != -1:
        try:
            payload = json.loads(line[json_start:])
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            if payload.get("event") != "request.started":
                return None
            raw_ip = payload.get("ip")
            if not isinstance(raw_ip, str):
                return None
            try:
                return str(ipaddress.ip_address(raw_ip))
            except ValueError:
                return None

    marker = "Processing request: client="
    marker_start = line.find(marker)
    if marker_start == -1:
        return None
    raw_ip = line[marker_start + len(marker):].split(",", 1)[0].strip("[]")
    try:
        return str(ipaddress.ip_address(raw_ip))
    except ValueError:
        return None


def main() -> None:
    client_ips = Counter()
    for log_file in LOGS_DIR.glob("*"):
        if not log_file.is_file():
            continue
        try:
            with log_file.open(errors="replace") as handle:
                for line in handle:
                    client_ip = _request_client_ip(line)
                    if client_ip:
                        client_ips[client_ip] += 1
        except OSError:
            continue

    for ip, count in client_ips.most_common(10):
        print(f"{ip}: {count} requests")
    print(f"Total unique IPs: {len(client_ips)}")
    print(f"Total requests: {sum(client_ips.values())}")


if __name__ == "__main__":
    main()
