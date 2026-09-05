"""Exercise deployment and rollback without touching host services or the network."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("failure", ["", "sync", "canary", "production"])
def test_deployment_stops_before_sync_and_recovers_failures(tmp_path, failure):
    checkout = tmp_path / "checkout"
    binaries = checkout / ".venv/bin"
    binaries.mkdir(parents=True)
    (checkout / "update_server.sh").write_text(Path("update_server.sh").read_text())
    (checkout / "nabicat.conf").write_text("nginx configuration")
    units = tmp_path / "etc/systemd/system"
    units.mkdir(parents=True)
    for name in ("nabicat.service", "meridian.service", "nabicat-scheduled-job@.service", "nabicat-backup.timer"):
        (units / name).write_text("original " + name)
    nginx = tmp_path / "etc/nginx/conf.d"
    nginx.mkdir(parents=True)
    (nginx / "nabicat.conf").write_text("original nginx")
    (tmp_path / "revision").write_text("previous")
    driver = binaries / "driver"
    driver.write_text(f"#!{sys.executable}\n" + r'''
import json, os, pathlib, shutil, signal, sys
root = pathlib.Path(os.environ["DEPLOY_TEST_ROOT"])
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with (root / "calls").open("a") as log:
    log.write(json.dumps([name, *args]) + "\n")
revision = root / "revision"
failure = os.environ["DEPLOY_TEST_FAILURE"]
def mapped(value):
    return root / value.lstrip("/") if value.startswith("/etc/") else pathlib.Path(value)
if name == "python":
    if args[:1] == ["-c"]:
        if "deployment_lock_path" in args[1]:
            print(root / "deployment.lock")
    elif args == ["-"]:
        source = sys.stdin.read()
        if "config.gunicorn_workers" in source:
            print("1\n720\n720\n5001\n1\n0\nnabicat-scheduled-job@.service\n3600\nnabicat-backup.timer\tbackup\tSun *-*-* 00:00:00")
        else:
            print("1\n0\nnabicat-scheduled-job@.service\nnabicat-backup.timer\tbackup\tSun *-*-* 00:00:00")
elif name == "git":
    if args[:2] == ["rev-parse", "HEAD"]: print(revision.read_text())
    elif args[:2] == ["rev-parse", "origin/main"]: print("candidate")
    elif args[:2] == ["reset", "--hard"]: revision.write_text(args[2])
elif name == "uv":
    if failure == "sync" and revision.read_text() == "candidate": sys.exit(1)
elif name == "curl":
    url = args[-1]
    if revision.read_text() == "candidate" and (
        failure == "canary" and ":5001/" in url
        or failure == "production" and ":5000/" in url
    ): sys.exit(1)
    print(json.dumps({"commit": revision.read_text()}, separators=(",", ":")))
elif name == "gunicorn":
    signal.pause()
elif name == "sudo":
    if args[0] == "test": sys.exit(0 if mapped(args[2]).is_file() else 1)
    if args[0] == "cp":
        destination = mapped(args[2])
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(mapped(args[1]), destination)
    elif args[0] == "rm": mapped(args[-1]).unlink(missing_ok=True)
''')
    driver.chmod(0o755)
    for command in ("python", "gunicorn", "git", "uv", "sudo", "curl", "sleep", "meridian"):
        (binaries / command).symlink_to(driver)
    result = subprocess.run(
        ["bash", "update_server.sh"], cwd=checkout,
        env={**os.environ, "DEPLOY_TEST_ROOT": str(tmp_path), "DEPLOY_TEST_FAILURE": failure},
        capture_output=True, text=True, timeout=15,
    )
    calls = [json.loads(line) for line in (tmp_path / "calls").read_text().splitlines()]
    stop = calls.index(["sudo", "systemctl", "stop", "nabicat.service"])
    sync = calls.index(["uv", "sync", "--locked", "--no-dev", "--managed-python"])
    reset = calls.index(["git", "reset", "--hard", "candidate"])
    assert stop < reset < sync
    assert (result.returncode != 0) == bool(failure), result.stdout + result.stderr
    if failure:
        assert (tmp_path / "revision").read_text() == "previous"
        assert (units / "nabicat.service").read_text() == "original nabicat.service"
        assert calls.count(["uv", "sync", "--locked", "--no-dev", "--managed-python"]) == 2
        assert "rollback recovered previous" in result.stdout
    else:
        assert str(binaries / "gunicorn") in (units / "nabicat.service").read_text()
        assert str(binaries / "python") in (units / "nabicat-scheduled-job@.service").read_text()
    assert ["sudo", "systemctl", "enable", "--now", "nabicat-backup.timer"] in calls
