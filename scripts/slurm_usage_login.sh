#!/usr/bin/env bash
# Display the current user's Slurm usage when an interactive login shell starts.
#
# Deployment on Bash-based login nodes (Rocky Linux/RHEL):
#   sudo install -m 0755 scripts/slurm_usage_login.sh \
#     /etc/profile.d/openhpc-slurm-usage.sh
#
# Test without starting a new login session:
#   bash scripts/slurm_usage_login.sh
#
# Requirements:
#   - sreport, sacctmgr, and scontrol must be installed on the login node.
#   - The login user must be allowed to read Slurm accounting/association data.
#   - GNU timeout is optional. Without it, Slurm commands have no client timeout.
#
# Configuration (set before sourcing this file):
#   SLURM_USAGE_TIMEOUT=3             Per-command timeout in seconds.
#   OPENHPC_SLURM_USAGE_BANNER=0      Disable the banner system-wide/session-wide.
#   NO_COLOR=1                        Disable ANSI colors.
#
# A user can hide the banner with:
#   touch "$HOME/.hush_slurm_usage"
#
# For zsh, source this script from /etc/zshrc or another system-wide zsh startup
# file. To uninstall the Bash login hook, remove the installed file from
# /etc/profile.d/.

# Do not display the banner when this file is sourced by a non-interactive shell.
if [[ "${BASH_SOURCE[0]}" != "$0" && $- != *i* ]]; then
    return 0
fi

_slurm_usage_run() {
    local timeout_seconds="${SLURM_USAGE_TIMEOUT:-3}"
    if command -v timeout >/dev/null 2>&1; then
        timeout "${timeout_seconds}s" "$@"
    else
        "$@"
    fi
}

_slurm_usage_hours() {
    awk -v seconds="${1:-0}" 'BEGIN { printf "%.2f", (seconds + 0) / 3600 }'
}

_slurm_usage_minutes_to_hours() {
    awk -v minutes="${1:-0}" 'BEGIN { printf "%.2f", (minutes + 0) / 60 }'
}

_slurm_usage_month_seconds() {
    local tres="$1"
    local username="$2"
    local start_time="$3"
    local end_time="$4"
    local output

    output="$(_slurm_usage_run sreport \
        -T "$tres" -t Seconds \
        cluster UserUtilizationByAccount \
        "Users=$username" "Start=$start_time" "End=$end_time" \
        -n -P format=Login,Used 2>/dev/null)" || return 1

    awk -F'|' -v username="$username" \
        '$1 == username { total += $2 } END { printf "%.0f", total + 0 }' \
        <<<"$output"
}

_slurm_usage_assoc_tres() {
    local username="$1"
    local account="$2"
    local output

    output="$(_slurm_usage_run scontrol show assoc_mgr flags=assoc \
        "users=$username" 2>/dev/null)" || return 1

    # Prefer the account's global association. Fall back to its first
    # partition-specific association when no global association exists.
    awk -v wanted_user="$username" -v wanted_account="$account" '
        function read_header(    i, value) {
            header_account = ""
            header_user = ""
            header_partition = ""
            for (i = 1; i <= NF; i++) {
                if ($i ~ /^Account=/) {
                    header_account = substr($i, 9)
                } else if ($i ~ /^UserName=/) {
                    value = substr($i, 10)
                    sub(/\(.*/, "", value)
                    header_user = value
                } else if ($i ~ /^Partition=/) {
                    header_partition = substr($i, 11)
                }
            }
            selected = (header_account == wanted_account && header_user == wanted_user)
        }
        /^ClusterName=/ { read_header(); next }
        selected && /^[[:space:]]*GrpTRESMins=/ {
            value = $0
            sub(/^[[:space:]]*GrpTRESMins=/, "", value)
            if (header_partition == "") {
                print value
                found = 1
                exit
            }
            if (fallback == "") {
                fallback = value
            }
        }
        END {
            if (!found && fallback != "") {
                print fallback
            }
        }
    ' <<<"$output"
}

_slurm_usage_account_tres() {
    local account="$1"
    local output

    output="$(_slurm_usage_run scontrol show assoc_mgr flags=assoc \
        "accounts=$account" 2>/dev/null)" || return 1

    # Account associations have no UserName. Prefer the global association so
    # the displayed balance matches the account-wide shared limit.
    awk -v wanted_account="$account" '
        function read_header(    i) {
            header_account = ""
            header_user = ""
            header_partition = ""
            for (i = 1; i <= NF; i++) {
                if ($i ~ /^Account=/) {
                    header_account = substr($i, 9)
                } else if ($i ~ /^UserName=/) {
                    header_user = substr($i, 10)
                } else if ($i ~ /^Partition=/) {
                    header_partition = substr($i, 11)
                }
            }
            selected = (header_account == wanted_account && header_user == "")
        }
        /^ClusterName=/ { read_header(); next }
        selected && /^[[:space:]]*GrpTRESMins=/ {
            value = $0
            sub(/^[[:space:]]*GrpTRESMins=/, "", value)
            if (header_partition == "") {
                print value
                found = 1
                exit
            }
            if (fallback == "") {
                fallback = value
            }
        }
        END {
            if (!found && fallback != "") {
                print fallback
            }
        }
    ' <<<"$output"
}

_slurm_usage_parse_tres() {
    local tres_value="$1"
    local wanted="$2"
    local item
    local limit
    local used

    IFS=',' read -r -a _slurm_usage_items <<<"$tres_value"
    for item in "${_slurm_usage_items[@]}"; do
        if [[ "$item" =~ ^${wanted}=([^\(]+)\(([0-9]+)\) ]]; then
            limit="${BASH_REMATCH[1]}"
            used="${BASH_REMATCH[2]}"
            printf '%s|%s\n' "$limit" "$used"
            return 0
        fi
    done
    printf 'N|0\n'
}

_slurm_usage_resource_line() {
    local label="$1"
    local month_seconds="$2"
    local parsed="$3"
    local limit="${parsed%%|*}"
    local used="${parsed#*|}"
    local month_hours
    local used_hours
    local limit_hours
    local remaining_minutes
    local remaining_hours
    local usage_percent=0
    local status_color="$_usage_green"

    month_hours="$(_slurm_usage_hours "$month_seconds")"

    if [[ "$limit" == "?" ]]; then
        printf '  %s%-4s%s  本月 %s%10s h%s  累计已用 %s  额度数据 %s不可用%s\n' \
            "$_usage_bold" "$label" "$_usage_reset" \
            "$_usage_cyan" "$month_hours" "$_usage_reset" \
            "—" "$_usage_yellow" "$_usage_reset"
        return
    fi

    used_hours="$(_slurm_usage_minutes_to_hours "$used")"

    if [[ "$limit" == "N" ]]; then
        printf '  %s%-4s%s  本月 %s%10s h%s  累计已用 %s%10s h%s  额度 %s无限%s\n' \
            "$_usage_bold" "$label" "$_usage_reset" \
            "$_usage_cyan" "$month_hours" "$_usage_reset" \
            "$_usage_blue" "$used_hours" "$_usage_reset" \
            "$_usage_green" "$_usage_reset"
        return
    fi

    remaining_minutes=$((limit > used ? limit - used : 0))
    limit_hours="$(_slurm_usage_minutes_to_hours "$limit")"
    remaining_hours="$(_slurm_usage_minutes_to_hours "$remaining_minutes")"
    if (( limit > 0 )); then
        usage_percent=$((used * 100 / limit))
    elif (( used > 0 )); then
        usage_percent=100
    fi
    if (( usage_percent >= 95 )); then
        status_color="$_usage_red"
    elif (( usage_percent >= 80 )); then
        status_color="$_usage_yellow"
    fi

    printf '  %s%-4s%s  本月 %s%10s h%s  累计已用 %s%10s h%s  额度 %10s h  剩余 %s%10s h%s\n' \
        "$_usage_bold" "$label" "$_usage_reset" \
        "$_usage_cyan" "$month_hours" "$_usage_reset" \
        "$_usage_blue" "$used_hours" "$_usage_reset" \
        "$limit_hours" "$status_color" "$remaining_hours" "$_usage_reset"
}

_slurm_usage_quota_line() {
    local label="$1"
    local parsed="$2"
    local limit="${parsed%%|*}"
    local used="${parsed#*|}"
    local used_hours
    local limit_hours
    local remaining_minutes
    local remaining_hours
    local usage_percent=0
    local status_color="$_usage_green"

    if [[ "$limit" == "?" ]]; then
        printf '  %s%-4s%s  累计已用 %s  额度数据 %s不可用%s\n' \
            "$_usage_bold" "$label" "$_usage_reset" \
            "—" "$_usage_yellow" "$_usage_reset"
        return
    fi

    used_hours="$(_slurm_usage_minutes_to_hours "$used")"
    if [[ "$limit" == "N" ]]; then
        printf '  %s%-4s%s  累计已用 %s%10s h%s  额度 %s无限%s\n' \
            "$_usage_bold" "$label" "$_usage_reset" \
            "$_usage_blue" "$used_hours" "$_usage_reset" \
            "$_usage_green" "$_usage_reset"
        return
    fi

    remaining_minutes=$((limit > used ? limit - used : 0))
    limit_hours="$(_slurm_usage_minutes_to_hours "$limit")"
    remaining_hours="$(_slurm_usage_minutes_to_hours "$remaining_minutes")"
    if (( limit > 0 )); then
        usage_percent=$((used * 100 / limit))
    elif (( used > 0 )); then
        usage_percent=100
    fi
    if (( usage_percent >= 95 )); then
        status_color="$_usage_red"
    elif (( usage_percent >= 80 )); then
        status_color="$_usage_yellow"
    fi

    printf '  %s%-4s%s  累计已用 %s%10s h%s  额度 %10s h  剩余 %s%10s h%s\n' \
        "$_usage_bold" "$label" "$_usage_reset" \
        "$_usage_blue" "$used_hours" "$_usage_reset" \
        "$limit_hours" "$status_color" "$remaining_hours" "$_usage_reset"
}

_slurm_usage_main() {
    local username
    local account
    local month_start
    local now
    local cpu_seconds=0
    local gpu_seconds=0
    local assoc_tres=""
    local account_tres=""
    local cpu_values="?|?"
    local gpu_values="?|?"
    local account_cpu_values="?|?"
    local account_gpu_values="?|?"

    [[ "${OPENHPC_SLURM_USAGE_BANNER:-1}" != "0" ]] || return 0
    [[ ! -e "${HOME:-/nonexistent}/.hush_slurm_usage" ]] || return 0
    [[ -z "${OPENHPC_SLURM_USAGE_SHOWN:-}" ]] || return 0
    OPENHPC_SLURM_USAGE_SHOWN=1

    for command_name in sreport sacctmgr scontrol; do
        command -v "$command_name" >/dev/null 2>&1 || return 0
    done

    username="$(id -un 2>/dev/null)" || return 0
    account="$(_slurm_usage_run sacctmgr show user "name=$username" \
        format=User,DefaultAccount -n -P 2>/dev/null \
        | awk -F'|' -v username="$username" \
            '$1 == username && $2 != "" { print $2; exit }')"
    [[ -n "$account" ]] || return 0

    month_start="$(date '+%Y-%m-01T00:00:00')"
    now="$(date '+%Y-%m-%dT%H:%M:%S')"
    cpu_seconds="$(_slurm_usage_month_seconds cpu "$username" "$month_start" "$now")" \
        || cpu_seconds=0
    gpu_seconds="$(_slurm_usage_month_seconds gres/gpu "$username" "$month_start" "$now")" \
        || gpu_seconds=0
    assoc_tres="$(_slurm_usage_assoc_tres "$username" "$account")" || assoc_tres=""
    if [[ -n "$assoc_tres" ]]; then
        cpu_values="$(_slurm_usage_parse_tres "$assoc_tres" cpu)"
        gpu_values="$(_slurm_usage_parse_tres "$assoc_tres" 'gres/gpu')"
    fi
    account_tres="$(_slurm_usage_account_tres "$account")" || account_tres=""
    if [[ -n "$account_tres" ]]; then
        account_cpu_values="$(_slurm_usage_parse_tres "$account_tres" cpu)"
        account_gpu_values="$(_slurm_usage_parse_tres "$account_tres" 'gres/gpu')"
    fi

    if [[ -t 1 && -z "${NO_COLOR:-}" && "${TERM:-}" != "dumb" ]]; then
        _usage_reset=$'\033[0m'
        _usage_bold=$'\033[1m'
        _usage_cyan=$'\033[36m'
        _usage_blue=$'\033[34m'
        _usage_green=$'\033[32m'
        _usage_yellow=$'\033[33m'
        _usage_red=$'\033[31m'
    else
        _usage_reset=""
        _usage_bold=""
        _usage_cyan=""
        _usage_blue=""
        _usage_green=""
        _usage_yellow=""
        _usage_red=""
    fi

    printf '\n%sSlurm 资源用量%s  用户 %s%s%s  账户 %s%s%s  统计月 %s\n' \
        "$_usage_bold" "$_usage_reset" \
        "$_usage_cyan" "$username" "$_usage_reset" \
        "$_usage_cyan" "$account" "$_usage_reset" \
        "$(date '+%Y-%m')"
    printf '%s\n' '--------------------------------------------------------------------------------'
    printf '  %s用户额度%s\n' "$_usage_bold" "$_usage_reset"
    _slurm_usage_resource_line "CPU" "$cpu_seconds" "$cpu_values"
    _slurm_usage_resource_line "GPU" "$gpu_seconds" "$gpu_values"
    printf '\n  %s账户共享额度%s（%s）\n' \
        "$_usage_bold" "$_usage_reset" "$account"
    _slurm_usage_quota_line "CPU" "$account_cpu_values"
    _slurm_usage_quota_line "GPU" "$account_gpu_values"
    printf '%s\n\n' '--------------------------------------------------------------------------------'
}

_slurm_usage_main

# Avoid leaking helper functions and color variables into the user's shell when
# this script is sourced from /etc/profile.d/.
unset -f _slurm_usage_run _slurm_usage_hours _slurm_usage_minutes_to_hours
unset -f _slurm_usage_month_seconds _slurm_usage_assoc_tres
unset -f _slurm_usage_account_tres _slurm_usage_parse_tres
unset -f _slurm_usage_resource_line _slurm_usage_quota_line _slurm_usage_main
unset _usage_reset _usage_bold _usage_cyan _usage_blue _usage_green
unset _usage_yellow _usage_red _slurm_usage_items
