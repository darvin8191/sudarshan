#!/usr/bin/env bash
# sudarshan/ops/signals/bootstrap.sh - the TRINETRA letterbox's one-time set-up on the server (M13.3,
# design 3.3.2, 3.3.3 and 3.8). The owner runs it once, as root, after the release that ships it is
# installed (sudarshan/README.md says how). Running it again is safe: a second run changes nothing and
# says so. It reads the system's account list for one number and touches nothing of TRINETRA's: not its
# account, groups, unit, files or folders. It runs no Python and restarts nothing.
#
#   1. /var/spool/sudarshan-signals (root:root 755) and the letterbox in it, in/ (sudarshan:sudarshan,
#      mode 1733: anyone may add a file, only its writer or sudarshan may remove it, nobody but
#      sudarshan may list it).
#   2. /etc/sudarshan/signals.env (root:sudarshan 640): SUDARSHAN_SIGNALS_PUBLISHER_UID=<number>, the
#      number of the one account whose files the TRINETRA view reads (looked up from the name given).
#   3. Both checked as they now are; then PASS, or what failed, and the check to run as sudarshan.
#
# Usage: bootstrap.sh --publisher ACCOUNT [--dry-run]
#   --publisher  the account TRINETRA runs as (its operators name it); never root, never sudarshan
#   --dry-run    say what it would do, and change nothing
# Exit status: 0 PASS, 1 FAIL, 2 nothing was done (a wrong option, or what it needs is not there).
set -uo pipefail

RING="sudarshan"
SPOOL_PATH="/var/spool/sudarshan-signals"
# A test runs the whole script on a folder of its own: BOOTSTRAP_ROOT is put before every path it reads
# or writes, and the owners there are the test's own account. On the server it is empty.
ROOT="${BOOTSTRAP_ROOT:-}"
SPOOL="${ROOT}${SPOOL_PATH}"
BOX="${SPOOL}/in"
ETC="${ROOT}/etc/sudarshan"
ENV_FILE="${ETC}/signals.env"
DEPLOY_ENV="${ETC}/deploy.env"

DRY=0; PUBLISHER=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY=1; shift ;;
        --publisher) PUBLISHER="${2:-}"; shift 2 || { echo "--publisher needs an account name" >&2; exit 2; } ;;
        -h|--help) awk 'NR == 1 { next } /^set -uo/ { exit } { sub(/^# ?/, ""); print }' "$0"; exit 0 ;;
        *) echo "usage: bootstrap.sh --publisher ACCOUNT [--dry-run]" >&2; exit 2 ;;
    esac
done

CHANGED=()   # what this run changed, for the summary
PROBLEMS=()  # what failed, for the summary
say() { printf '%s\n' "$*"; }
changed() { CHANGED+=("$1"); if [[ ${DRY} -eq 1 ]]; then say "would change: $1"; else say "changed: $1"; fi; }
ok() { say "in place: $1"; }
problem() { PROBLEMS+=("$1"); say "FAIL: $1"; }
stop() { say "$1"; say "Nothing was changed."; exit 2; }   # before anything was changed
act() { if [[ ${DRY} -eq 1 ]]; then return 0; fi; "$@"; }   # a change, made unless this is a dry run

# ---- 0. What it needs, checked before anything is changed --------------------------------------------
if [[ -z "${ROOT}" && ${EUID} -ne 0 ]]; then stop "Run this as root (sudo)."; fi
for cmd in getent stat mktemp; do
    command -v "${cmd}" > /dev/null 2>&1 || stop "This server has no ${cmd} command, which the set-up needs."
done
[[ -n "${PUBLISHER}" ]] || stop "Name the account TRINETRA runs as: bootstrap.sh --publisher ACCOUNT"
[[ "${PUBLISHER}" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || stop "'${PUBLISHER}' is not an account name."
entry="$(getent passwd "${PUBLISHER}" 2>/dev/null | head -n 1)"
[[ -n "${entry}" ]] || stop "There is no account named ${PUBLISHER} on this server. Ask TRINETRA's operators which account TRINETRA runs as."
pub_uid="$(cut -d: -f3 <<< "${entry}")"
[[ "${pub_uid}" =~ ^[0-9]+$ ]] || stop "The account list gives no number for ${PUBLISHER}."
[[ "${pub_uid}" != 0 ]] || stop "${PUBLISHER} is root (number 0). The letterbox reads one account's files, and it is never root's."
if [[ -n "${ROOT}" ]]; then   # a test folder: its "root" and "sudarshan" are the test's own account
    ROOT_OWNER="$(id -un):$(id -gn)"; RING_OWNER="$(id -un):$(id -gn)"; ring_uid="$(id -u)"
else
    ROOT_OWNER="root:root"; RING_OWNER="${RING}:${RING}"
    ring_uid="$(getent passwd "${RING}" | cut -d: -f3)"
    [[ -n "${ring_uid}" ]] || stop "There is no ${RING} account: SUDARSHAN is not installed on this server."
fi
if [[ "${PUBLISHER}" == "${RING}" || "${pub_uid}" == "${ring_uid}" ]]; then
    stop "${PUBLISHER} is SUDARSHAN's own account. The publisher must be TRINETRA's account, so that only TRINETRA's files are read."
fi
[[ -d "${ETC}" && ! -L "${ETC}" ]] || stop "There is no ${ETC}: the deploy tool is not set up on this server."
say "TRINETRA letterbox set-up$([[ ${DRY} -eq 1 ]] && echo ' (dry run: nothing is changed)')"
say "The publisher is ${PUBLISHER} (number ${pub_uid}); only its files will be read."

# ---- 1. The two folders -----------------------------------------------------------------------------
have_of() { stat -c '%U:%G %a' -- "$1" 2>/dev/null; }
folder() {   # $1 = folder, $2 = owner:group, $3 = mode: made, or set to that, and said either way
    local have
    if [[ -L "$1" ]] || [[ -e "$1" && ! -d "$1" ]]; then
        problem "$1 is there but is not a folder; move it away and run this again"; return 1
    fi
    if [[ ! -d "$1" ]]; then
        if ! { act mkdir -- "$1" && act chown -- "$2" "$1" && act chmod -- "$3" "$1"; }; then
            problem "$1 could not be made"; return 1
        fi
        changed "new folder $1 ($2, mode $3)"; return 0
    fi
    have="$(have_of "$1")"
    if [[ "${have}" == "$2 $3" ]]; then ok "$1 ($2, mode $3)"; return 0; fi
    if ! { act chown -- "$2" "$1" && act chmod -- "$3" "$1"; }; then
        problem "$1 could not be set to $2, mode $3"; return 1
    fi
    changed "$1 set to $2, mode $3 (it was ${have:-unreadable})"
}
if [[ -n "${ROOT}" ]]; then mkdir -p -- "${ROOT}/var/spool"; fi
folder "${SPOOL}" "${ROOT_OWNER}" 755 && folder "${BOX}" "${RING_OWNER}" 1733

# ---- 2. The publisher's number ------------------------------------------------------------------------
want="SUDARSHAN_SIGNALS_PUBLISHER_UID=${pub_uid}"
text="$(printf '%s\n%s\n%s' "# Written by sudarshan/ops/signals/bootstrap.sh: the one account whose files the" \
    "# TRINETRA view reads (${PUBLISHER}). Run the set-up again to change it; never edit by hand." "${want}")"
if [[ -L "${ENV_FILE}" ]]; then
    problem "${ENV_FILE} is a link; move it away and run this again"
elif [[ -f "${ENV_FILE}" ]] && [[ "$(cat -- "${ENV_FILE}")" == "${text}" ]] \
        && [[ "$(have_of "${ENV_FILE}")" == "${ROOT_OWNER%%:*}:${RING_OWNER##*:} 640" ]]; then
    ok "${ENV_FILE} (${want})"
elif [[ ${DRY} -eq 1 ]]; then
    changed "${ENV_FILE} written (${want})"
else
    tmpf="$(mktemp -p "${ETC}" ".signals.env.XXXXXX")" || { problem "${ENV_FILE} could not be written"; tmpf=""; }
    if [[ -n "${tmpf}" ]]; then
        if printf '%s\n' "${text}" > "${tmpf}" && chown -- "${ROOT_OWNER%%:*}:${RING_OWNER##*:}" "${tmpf}" \
                && chmod 640 -- "${tmpf}" && mv -fT -- "${tmpf}" "${ENV_FILE}"; then
            changed "${ENV_FILE} written (${want})"
        else
            rm -f -- "${tmpf}"; problem "${ENV_FILE} could not be written"
        fi
    fi
fi

# ---- 3. The result ------------------------------------------------------------------------------------
if [[ ${DRY} -eq 1 ]]; then say "Dry run: nothing was changed."; exit 0; fi
if [[ "$(have_of "${BOX}")" != "${RING_OWNER} 1733" ]]; then
    [[ ${#PROBLEMS[@]} -gt 0 ]] || problem "${BOX} is not ${RING_OWNER} with mode 1733 after the set-up"
fi
if ! grep -qx -- "${want}" "${ENV_FILE}" 2>/dev/null; then
    [[ ${#PROBLEMS[@]} -gt 0 ]] || problem "${ENV_FILE} does not hold ${want} after the set-up"
fi
if [[ ${#PROBLEMS[@]} -gt 0 ]]; then
    say "FAIL: fix what is named above and run this again."
    exit 1
fi
app_dir="$(sed -n 's/^[[:space:]]*APP_DIR=//p' "${DEPLOY_ENV}" 2>/dev/null | tail -n 1)"
app_dir="${app_dir%\"}"; app_dir="${app_dir#\"}"; app_dir="${app_dir%\'}"; app_dir="${app_dir#\'}"
if [[ ${#CHANGED[@]} -eq 0 ]]; then
    say "PASS. Nothing was changed: the letterbox and the publisher's number were already in place."
else
    say "PASS. The letterbox is ready. TRINETRA's operators write into ${SPOOL_PATH}/in (section 1 of"
    say "sudarshan/signals/README.md); nothing of TRINETRA's was touched."
fi
say "Check it as SUDARSHAN's account (it writes one file as a stranger and sees it refused):"
say "    cd ${app_dir:-<the app folder>} && sudo -u ${RING} .venv/bin/python -m sudarshan.signals.check"
exit 0
