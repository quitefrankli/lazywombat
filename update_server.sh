#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR=$(pwd)
export PATH="${PROJECT_DIR}/.venv/bin:${HOME}/.local/bin:${HOME}/.deno/bin:/usr/local/bin:/usr/bin:/bin"
PYTHON_BIN="${PROJECT_DIR}/.venv/bin/python"
GUNICORN_BIN="${PROJECT_DIR}/.venv/bin/gunicorn"
LOG_FILE="${PROJECT_DIR}/logs/web_app.log"
mkdir -p "$(dirname "$LOG_FILE")"

write_deploy_log() {
    local level="$1"
    local message="$2"
    printf '%s %s deployment: %s\n' "$(date '+%Y-%m-%d %H:%M:%S,%3N')" "$level" "$message" | tee -a "$LOG_FILE"
}

deploy_log() {
    local message="$1"
    write_deploy_log INFO "$message"
}

deploy_error() {
    local message="$1"
    write_deploy_log ERROR "$message"
}

install_runtime_requirements() {
    uv sync --locked --no-dev --managed-python || return $?
    if "$PYTHON_BIN" -c 'import importlib.util; raise SystemExit(importlib.util.find_spec("nabicat_jswipe") is None)'; then
        "$PYTHON_BIN" -m nabicat_jswipe.install_career_ops
    fi
}

LOCK_PATH=$("$PYTHON_BIN" -c 'from web_app.config import ConfigManager; print(ConfigManager().deployment_lock_path)')
mkdir -p "$(dirname "$LOCK_PATH")"
exec 9>"$LOCK_PATH"
flock 9

git checkout main
git fetch origin main
git fetch --tags
PREVIOUS_COMMIT=$(git rev-parse HEAD)

mapfile -t RECOVERY_CONFIG < <("$PYTHON_BIN" - <<'PY'
from web_app.config import ConfigManager

config = ConfigManager()
print(config.deployment_health_attempts)
print(config.deployment_health_interval_s)
print(config.scheduled_job_service_unit_name)
for timer_name, job_name, on_calendar in config.scheduled_job_timers:
    print("\t".join((timer_name, job_name, on_calendar)))
PY
)
if [[ "${#RECOVERY_CONFIG[@]}" -lt 3 ]]; then
    deploy_error "could not load rollback health-check configuration"
    exit 1
fi
HEALTH_ATTEMPTS="${RECOVERY_CONFIG[0]}"
HEALTH_INTERVAL="${RECOVERY_CONFIG[1]}"
SCHEDULED_JOB_SERVICE_UNIT="${RECOVERY_CONFIG[2]}"
SCHEDULED_JOB_TIMEOUT=""
SCHEDULED_JOB_SPECS=("${RECOVERY_CONFIG[@]:3}")
SCHEDULED_JOB_TIMER_NAMES=()
for timer_spec in "${SCHEDULED_JOB_SPECS[@]}"; do
    IFS=$'\t' read -r timer_name _ _ <<< "$timer_spec"
    SCHEDULED_JOB_TIMER_NAMES+=("$timer_name")
done
PREVIOUS_JOB_SPECS=("${SCHEDULED_JOB_SPECS[@]}")
PREVIOUS_JOB_SERVICE_UNIT="$SCHEDULED_JOB_SERVICE_UNIT"
FLOCK_BIN=$(which flock)

CANDIDATE_COMMIT=$(git rev-parse origin/main)

BACKUP_DIR=$(mktemp -d)
CANARY_PID=""
ROLLBACK_ARMED=0

cleanup() {
    if [[ -n "$CANARY_PID" ]] && kill -0 "$CANARY_PID" 2>/dev/null; then
        kill "$CANARY_PID" 2>/dev/null || true
        wait "$CANARY_PID" 2>/dev/null || true
    fi
    rm -rf -- "$BACKUP_DIR"
}

backup_system_file() {
    local source="$1"
    local name="$2"
    [[ ! -e "${BACKUP_DIR}/${name}" && ! -e "${BACKUP_DIR}/${name}.missing" ]] || return 0
    if sudo test -f "$source"; then
        sudo cp "$source" "${BACKUP_DIR}/${name}"
    else
        touch "${BACKUP_DIR}/${name}.missing"
    fi
}

restore_system_file() {
    local target="$1"
    local name="$2"
    if [[ -f "${BACKUP_DIR}/${name}.missing" ]]; then
        sudo rm -f -- "$target"
    else
        sudo cp "${BACKUP_DIR}/${name}" "$target"
    fi
}

stop_application() {
    local timer_name job_name timer_spec
    for timer_name in "${SCHEDULED_JOB_TIMER_NAMES[@]}"; do
        if sudo test -f "/etc/systemd/system/${timer_name}"; then
            sudo systemctl stop "$timer_name" || return $?
        fi
    done
    for timer_spec in "${SCHEDULED_JOB_SPECS[@]}"; do
        IFS=$'\t' read -r _ job_name _ <<< "$timer_spec"
        if sudo test -f "/etc/systemd/system/${SCHEDULED_JOB_SERVICE_UNIT}"; then
            sudo systemctl stop "${SCHEDULED_JOB_SERVICE_UNIT%@.service}@${job_name}.service" || return $?
        fi
    done
    if sudo test -f /etc/systemd/system/nabicat.service; then
        sudo systemctl stop nabicat.service || return $?
    fi
}

restore_scheduled_job_units() {
    local job_name
    local timer_spec
    local timer_name
    [[ -n "$SCHEDULED_JOB_SERVICE_UNIT" ]] || return 0
    for timer_name in "${SCHEDULED_JOB_TIMER_NAMES[@]}"; do
        sudo systemctl stop "$timer_name" || true
    done
    for timer_spec in "${SCHEDULED_JOB_SPECS[@]}"; do
        IFS=$'\t' read -r _ job_name _ <<< "$timer_spec"
        sudo systemctl stop "${SCHEDULED_JOB_SERVICE_UNIT%@.service}@${job_name}.service" || true
    done
    for timer_name in "${SCHEDULED_JOB_TIMER_NAMES[@]}"; do
        if [[ -f "${BACKUP_DIR}/${timer_name}.missing" ]]; then
            sudo systemctl disable --now "$timer_name" || true
        fi
        restore_system_file "/etc/systemd/system/${timer_name}" "$timer_name"
    done
    restore_system_file "/etc/systemd/system/${SCHEDULED_JOB_SERVICE_UNIT}" "$SCHEDULED_JOB_SERVICE_UNIT"
    SCHEDULED_JOB_SERVICE_UNIT="$PREVIOUS_JOB_SERVICE_UNIT"
    SCHEDULED_JOB_SPECS=("${PREVIOUS_JOB_SPECS[@]}")
    SCHEDULED_JOB_TIMER_NAMES=()
    for timer_spec in "${SCHEDULED_JOB_SPECS[@]}"; do
        IFS=$'\t' read -r timer_name _ _ <<< "$timer_spec"
        restore_system_file "/etc/systemd/system/${timer_name}" "$timer_name"
        SCHEDULED_JOB_TIMER_NAMES+=("$timer_name")
    done
    restore_system_file "/etc/systemd/system/${SCHEDULED_JOB_SERVICE_UNIT}" "$SCHEDULED_JOB_SERVICE_UNIT"
}

enable_restored_scheduled_job_timers() {
    local timer_name
    [[ -n "$SCHEDULED_JOB_SERVICE_UNIT" ]] || return 0
    for timer_name in "${SCHEDULED_JOB_TIMER_NAMES[@]}"; do
        if [[ ! -f "${BACKUP_DIR}/${timer_name}.missing" ]]; then
            sudo systemctl enable --now "$timer_name"
        fi
    done
}

wait_for_commit() {
    local url="$1"
    local expected="$2"
    local response
    for ((attempt = 1; attempt <= HEALTH_ATTEMPTS; attempt++)); do
        response=$(curl --fail --silent --show-error --max-time 2 "$url" 2>/dev/null || true)
        if [[ "$response" == *"\"commit\":\"${expected}\""* ]]; then
            return 0
        fi
        sleep "$HEALTH_INTERVAL"
    done
    return 1
}

rollback() {
    local exit_code=$?
    trap - ERR
    set +e
    if [[ "$ROLLBACK_ARMED" -eq 1 ]]; then
        deploy_error "candidate ${CANDIDATE_COMMIT} failed (exit ${exit_code}); rolling back to ${PREVIOUS_COMMIT}"
        if [[ -n "$CANARY_PID" ]]; then
            kill "$CANARY_PID" 2>/dev/null
            wait "$CANARY_PID" 2>/dev/null
            CANARY_PID=""
        fi
        stop_application
        git am --abort >/dev/null 2>&1 || true
        git reset --hard "$PREVIOUS_COMMIT"
        if ! install_runtime_requirements; then
            deploy_error "rollback dependency installation failed; services remain stopped"
            exit "$exit_code"
        fi
        restore_system_file /etc/nginx/conf.d/nabicat.conf nginx.conf
        restore_system_file /etc/systemd/system/nabicat.service nabicat.service
        restore_system_file /etc/systemd/system/meridian.service meridian.service
        restore_scheduled_job_units
        sudo nginx -t
        sudo systemctl daemon-reload
        sudo systemctl reload nginx
        sudo systemctl restart meridian.service nabicat.service
        if wait_for_commit "http://127.0.0.1:5000/api/health" "$PREVIOUS_COMMIT"; then
            enable_restored_scheduled_job_timers
            deploy_log "rollback recovered ${PREVIOUS_COMMIT}"
        else
            deploy_error "rollback to ${PREVIOUS_COMMIT} did not pass its health check"
        fi
    else
        deploy_error "deployment failed before the checkout changed (exit ${exit_code})"
    fi
    cleanup
    exit "$exit_code"
}

trap cleanup EXIT
trap rollback ERR

backup_system_file /etc/nginx/conf.d/nabicat.conf nginx.conf
backup_system_file /etc/systemd/system/nabicat.service nabicat.service
backup_system_file /etc/systemd/system/meridian.service meridian.service

backup_system_file "/etc/systemd/system/${SCHEDULED_JOB_SERVICE_UNIT}" "$SCHEDULED_JOB_SERVICE_UNIT"
for timer_name in "${SCHEDULED_JOB_TIMER_NAMES[@]}"; do
    backup_system_file "/etc/systemd/system/${timer_name}" "$timer_name"
done

deploy_log "deploying ${CANDIDATE_COMMIT} over ${PREVIOUS_COMMIT}"
ROLLBACK_ARMED=1
stop_application
while getopts ":p" opt
do
    case "$opt" in
        p)
            git reset --hard origin/main
            deploy_log "applying API patch"
            git am
            git push
            git fetch origin main
            ;;
        \?)
            deploy_error "invalid option: -${OPTARG}"
            false
            ;;
    esac
done

CANDIDATE_COMMIT=$(git rev-parse origin/main)
git reset --hard "$CANDIDATE_COMMIT"

mapfile -t DEPLOY_CONFIG < <("$PYTHON_BIN" - <<'PY'
from web_app.config import ConfigManager

config = ConfigManager()
print(config.gunicorn_workers)
print(config.gunicorn_request_timeout_s)
print(config.gunicorn_graceful_timeout_s)
print(config.deployment_canary_port)
print(config.deployment_health_attempts)
print(config.deployment_health_interval_s)
print(config.scheduled_job_service_unit_name)
print(config.scheduled_job_timeout_s)
for timer_name, job_name, on_calendar in config.scheduled_job_timers:
    print("\t".join((timer_name, job_name, on_calendar)))
PY
)
if [[ "${#DEPLOY_CONFIG[@]}" -lt 9 ]]; then
    deploy_error "candidate scheduled-job configuration is incomplete"
    false
fi
WORKERS="${DEPLOY_CONFIG[0]}"
REQUEST_TIMEOUT="${DEPLOY_CONFIG[1]}"
GRACEFUL_TIMEOUT="${DEPLOY_CONFIG[2]}"
CANARY_PORT="${DEPLOY_CONFIG[3]}"
HEALTH_ATTEMPTS="${DEPLOY_CONFIG[4]}"
HEALTH_INTERVAL="${DEPLOY_CONFIG[5]}"
SCHEDULED_JOB_SERVICE_UNIT="${DEPLOY_CONFIG[6]}"
SCHEDULED_JOB_TIMEOUT="${DEPLOY_CONFIG[7]}"
SCHEDULED_JOB_SPECS=("${DEPLOY_CONFIG[@]:8}")
SCHEDULED_JOB_TIMER_NAMES=()
for timer_spec in "${SCHEDULED_JOB_SPECS[@]}"; do
    IFS=$'\t' read -r timer_name _ _ <<< "$timer_spec"
    SCHEDULED_JOB_TIMER_NAMES+=("$timer_name")
done

backup_system_file "/etc/systemd/system/${SCHEDULED_JOB_SERVICE_UNIT}" "$SCHEDULED_JOB_SERVICE_UNIT"
for timer_name in "${SCHEDULED_JOB_TIMER_NAMES[@]}"; do
    backup_system_file "/etc/systemd/system/${timer_name}" "$timer_name"
done

install_runtime_requirements
sudo "$PYTHON_BIN" -m playwright install-deps chromium
"$PYTHON_BIN" -m playwright install chromium
sudo apt-get install -y redis-server
sudo systemctl enable --now redis-server

"$GUNICORN_BIN" -b "127.0.0.1:${CANARY_PORT}" \
    -w 1 \
    --timeout "$REQUEST_TIMEOUT" \
    --graceful-timeout "$GRACEFUL_TIMEOUT" \
    'web_app.__main__:prod_entry()' &
CANARY_PID=$!

if ! wait_for_commit "http://127.0.0.1:${CANARY_PORT}/api/health" "$CANDIDATE_COMMIT"; then
    deploy_error "canary health check failed for ${CANDIDATE_COMMIT}"
    false
fi
deploy_log "canary passed for ${CANDIDATE_COMMIT}"
kill "$CANARY_PID"
wait "$CANARY_PID" || true
CANARY_PID=""

USER_NAME=$(whoami)
MERIDIAN_BIN=$(which meridian)
NABICAT_UNIT="${BACKUP_DIR}/nabicat.service.new"
MERIDIAN_UNIT="${BACKUP_DIR}/meridian.service.new"
SCHEDULED_JOB_SERVICE_FILE="${BACKUP_DIR}/${SCHEDULED_JOB_SERVICE_UNIT}.new"

cat >"$NABICAT_UNIT" <<EOF
[Unit]
Description=Nabicat web app
After=network.target
StartLimitBurst=5
StartLimitIntervalSec=60

[Service]
Type=simple
User=${USER_NAME}
WorkingDirectory=${PROJECT_DIR}
Environment="PATH=${PATH}"
ExecStart=${GUNICORN_BIN} -b 127.0.0.1:5000 -w ${WORKERS} --timeout ${REQUEST_TIMEOUT} --graceful-timeout ${GRACEFUL_TIMEOUT} 'web_app.__main__:prod_entry()'
ExecReload=/bin/kill -s HUP \$MAINPID
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

cat >"$MERIDIAN_UNIT" <<EOF
[Unit]
Description=Meridian LLM proxy
After=network.target
StartLimitBurst=5
StartLimitIntervalSec=60

[Service]
Type=simple
User=${USER_NAME}
WorkingDirectory=/home/${USER_NAME}
Environment="PATH=${PATH}"
ExecStart=${MERIDIAN_BIN}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

cat >"$SCHEDULED_JOB_SERVICE_FILE" <<EOF
[Unit]
Description=Nabicat scheduled job (%i)
Requires=redis-server.service
After=network-online.target redis-server.service
Wants=network-online.target

[Service]
Type=oneshot
User=${USER_NAME}
WorkingDirectory=${PROJECT_DIR}
Environment="PATH=${PATH}"
TimeoutStartSec=${SCHEDULED_JOB_TIMEOUT}
ExecStart=${FLOCK_BIN} "${LOCK_PATH}" "${PYTHON_BIN}" -m web_app.scheduled_jobs %i
EOF

for timer_spec in "${SCHEDULED_JOB_SPECS[@]}"; do
    IFS=$'\t' read -r TIMER_NAME JOB_NAME ON_CALENDAR <<< "$timer_spec"
    TIMER_FILE="${BACKUP_DIR}/${TIMER_NAME}.new"
    cat >"$TIMER_FILE" <<EOF
[Unit]
Description=Nabicat scheduled job timer (${JOB_NAME})

[Timer]
OnCalendar=${ON_CALENDAR}
Persistent=true
Unit=${SCHEDULED_JOB_SERVICE_UNIT%@.service}@${JOB_NAME}.service

[Install]
WantedBy=timers.target
EOF
done

sudo cp nabicat.conf /etc/nginx/conf.d/
sudo nginx -t
sudo systemctl reload nginx

sudo cp "$NABICAT_UNIT" /etc/systemd/system/nabicat.service
sudo cp "$MERIDIAN_UNIT" /etc/systemd/system/meridian.service
sudo cp "$SCHEDULED_JOB_SERVICE_FILE" "/etc/systemd/system/${SCHEDULED_JOB_SERVICE_UNIT}"
for timer_name in "${SCHEDULED_JOB_TIMER_NAMES[@]}"; do
    sudo cp "${BACKUP_DIR}/${timer_name}.new" "/etc/systemd/system/${timer_name}"
done
sudo systemctl daemon-reload
sudo systemctl enable meridian.service nabicat.service

sudo systemctl restart meridian.service nabicat.service

if ! wait_for_commit "http://127.0.0.1:5000/api/health" "$CANDIDATE_COMMIT"; then
    deploy_error "production health check failed for ${CANDIDATE_COMMIT}"
    false
fi

sudo systemctl enable --now "${SCHEDULED_JOB_TIMER_NAMES[@]}"
ROLLBACK_ARMED=0
deploy_log "deployment ${CANDIDATE_COMMIT} is healthy"
