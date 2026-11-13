#!/usr/bin/env bash
# sudarshan/ops/family/bootstrap.sh - the family view's one-time set-up on the server (P6; cross-design
# Y1, Y2, Y3.1-3.2, Y25). The owner runs it once, as root, after the release that ships it is installed
# (sudarshan/README.md says how). Running it again is safe: a second run changes nothing and says so.
# It never runs the app's Python as root, never flushes the firewall, and restarts nothing but its own
# firewall unit.
#
#   1. The account sudarshan-family: a system account with no home and no login shell, and not in the
#      group sudarshan, so it cannot pass into /etc/sudarshan or read OpenAlgo's files.
#   2. /etc/sudarshan-family (root:sudarshan-family 750, files 640), where the deploy tool puts what the
#      account needs, and /var/lib/sudarshan-family-export (sudarshan-family:sudarshan 2750), where the
#      family view leaves what the ring may read (notices, the copy of its store for the nightly backup).
#   3. The account's allow-list of outgoing connections, in an nftables table of its own, loaded at boot
#      by nft-sudarshan-family.service (a name the deploy tool never manages). The account may connect
#      to 127.0.0.1 on ports 5000, 5580, 5581, 5583 and 5586 and to the local resolver (127.0.0.53, port
#      53). Every other port on this server is refused at once ("administratively prohibited", which
#      the check can prove; a packet only dropped makes the connection hang until it times out), and
#      elsewhere only TCP port 443 is open. No other table, service or rule on the server is touched.
#   4. OpenAlgo's folders that other accounts can read are named, with the chmod line to run. They are
#      not changed.
#   5. The deploy tool's provision step (sudarshan-deploy --family-provision): the keys and copies in
#      /etc/sudarshan-family, then the check, run as the account itself. Then PASS, or what failed.
#
# Usage: bootstrap.sh [--dry-run] [--deploy-tool PATH]
#   --dry-run       say what it would do, and change nothing
#   --deploy-tool   the deploy tool, when it cannot be found by itself (it is the one the
#                   sudarshan-deploy unit runs, else sudarshan-deploy on PATH)
# Exit status: 0 PASS, 1 FAIL, 2 nothing was done (a wrong option, or what it needs is not there).
set -uo pipefail

ACCOUNT="sudarshan-family"
RING="sudarshan"
TABLE="sudarshan_family"
FW_UNIT="nft-sudarshan-family.service"
NFT_PATH="/etc/nftables-sudarshan-family.nft"
PORTS="5000, 5580, 5581, 5583, 5586"
# A test runs the whole script on a folder of its own: BOOTSTRAP_ROOT is put before every path it reads
# or writes, and the files there are the test's own. On the server it is empty.
ROOT="${BOOTSTRAP_ROOT:-}"
FAM_ETC="${ROOT}/etc/sudarshan-family"
EXPORT="${ROOT}/var/lib/sudarshan-family-export"
NFT_FILE="${ROOT}${NFT_PATH}"
UNIT_FILE="${ROOT}/etc/systemd/system/${FW_UNIT}"
DEPLOY_ENV="${ROOT}/etc/sudarshan/deploy.env"
RESOLV="${ROOT}/etc/resolv.conf"

DRY=0; TOOL=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY=1; shift ;;
        --deploy-tool) TOOL="${2:-}"; shift 2 || { echo "--deploy-tool needs a path" >&2; exit 2; } ;;
        -h|--help) awk 'NR == 1 { next } /^set -uo/ { exit } { sub(/^# ?/, ""); print }' "$0"; exit 0 ;;
        *) echo "usage: bootstrap.sh [--dry-run] [--deploy-tool PATH]" >&2; exit 2 ;;
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
for cmd in systemctl nft useradd stat; do
    if command -v "${cmd}" > /dev/null 2>&1; then continue; fi
    if [[ "${cmd}" == nft ]]; then
        stop "This server has no nft command, which the set-up needs. Install it with: apt install nftables, then run this again."
    fi
    stop "This server has no ${cmd} command, which the set-up needs."
done
NFT_BIN="$(command -v nft)"
want_uid=0   # what root's own files are owned by; in a test folder they are the test's own
if [[ -n "${ROOT}" ]]; then want_uid="$(id -u)"; fi
[[ -f "${DEPLOY_ENV}" ]] || stop "There is no ${DEPLOY_ENV}: the deploy tool is not set up on this server."
APP_DIR="$(sed -n 's/^[[:space:]]*APP_DIR=//p' "${DEPLOY_ENV}" | tail -n 1)"
APP_DIR="${APP_DIR%\"}"; APP_DIR="${APP_DIR#\"}"; APP_DIR="${APP_DIR%\'}"; APP_DIR="${APP_DIR#\'}"
[[ "${APP_DIR}" == /* ]] || stop "${DEPLOY_ENV} names no APP_DIR, so the app's folder is not known."
APP="${ROOT}${APP_DIR}"
if [[ -z "${TOOL}" ]]; then
    TOOL="$(systemctl show -p ExecStart --value sudarshan-deploy.service 2>/dev/null \
        | sed -n 's/.*path=\([^ ;]*\).*/\1/p' | head -n 1)"
    if [[ -z "${TOOL}" ]]; then TOOL="$(command -v sudarshan-deploy 2>/dev/null || true)"; fi
fi
[[ -n "${TOOL}" ]] || stop "The deploy tool was not found. Give its path with --deploy-tool."
tool_info="$(stat -L -c '%u %a' -- "${TOOL}" 2>/dev/null)" \
    || stop "The deploy tool ${TOOL} is not there. Give its path with --deploy-tool."
if [[ "${tool_info% *}" != "${want_uid}" ]] || (( (8#${tool_info#* } & 8#022) != 0 )); then
    stop "The deploy tool ${TOOL} is not root's alone (others can change it), so it is not run as root."
fi
if ! grep -q -- '--family-provision)' "${TOOL}"; then
    stop "The deploy tool on this server is older than the release that brings the family set-up. It updates
itself at the next install (outside market hours); run this again after that."
fi
say "Family view set-up$([[ ${DRY} -eq 1 ]] && echo ' (dry run: nothing is changed)')"
say "The app is in ${APP_DIR}; the deploy tool is ${TOOL}."

# ---- 1. The account --------------------------------------------------------------------------------
if id -u "${ACCOUNT}" > /dev/null 2>&1; then
    if [[ "$(id -u "${ACCOUNT}")" == 0 ]]; then stop "The account ${ACCOUNT} has the user id 0 (root). Have it looked at."; fi
    if [[ " $(id -nG "${ACCOUNT}") " == *" ${RING} "* ]]; then
        stop "The account ${ACCOUNT} is in the group ${RING}, so it could read the owner's settings and keys.
Take it out first (gpasswd -d ${ACCOUNT} ${RING}), then run this again."
    fi
    ok "the account ${ACCOUNT}"
else
    act useradd --system --user-group --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin \
        --comment "SUDARSHAN family view" "${ACCOUNT}" || stop "The account ${ACCOUNT} could not be made."
    changed "new account ${ACCOUNT} (no home, no login shell, not in the group ${RING})"
fi

# ---- 2. Its two folders ----------------------------------------------------------------------------
owner_of() { stat -c '%U:%G %a' -- "$1" 2>/dev/null; }
folder() {   # $1 = folder, $2 = owner:group, $3 = mode: made, or set to that, and said either way
    local have
    if [[ -L "$1" ]] || [[ -e "$1" && ! -d "$1" ]]; then
        problem "$1 is there but is not a folder; move it away and run this again"; return 1
    fi
    if [[ ! -d "$1" ]]; then
        if ! { act mkdir -p -- "$1" && act chown -- "$2" "$1" && act chmod -- "$3" "$1"; }; then
            problem "$1 could not be made"; return 1
        fi
        changed "new folder $1 ($2, mode $3)"; return 0
    fi
    have="$(owner_of "$1")"
    if [[ "${have}" == "$2 $3" ]]; then ok "$1 ($2, mode $3)"; return 0; fi
    if ! { act chown -- "$2" "$1" && act chmod -- "$3" "$1"; }; then
        problem "$1 could not be set to $2, mode $3"; return 1
    fi
    changed "$1 set to $2, mode $3 (it was ${have:-unreadable})"
}
folder "${FAM_ETC}" "root:${ACCOUNT}" 750
if [[ -d "${FAM_ETC}" && ! -L "${FAM_ETC}" ]]; then   # the files the deploy tool wrote there: 640
    while IFS= read -r -d '' f; do
        [[ "$(owner_of "${f}")" == "root:${ACCOUNT} 640" ]] && continue
        act chown -- "root:${ACCOUNT}" "${f}" && act chmod -- 640 "${f}" \
            && changed "${f} set to root:${ACCOUNT}, mode 640"
    done < <(find "${FAM_ETC}" -mindepth 1 -maxdepth 1 -type f -print0 2>/dev/null)
fi
folder "${EXPORT}" "${ACCOUNT}:${RING}" 2750

# ---- 3. The allow-list of outgoing connections ------------------------------------------------------
ruleset() {
    cat <<EOF
# Written by sudarshan/ops/family/bootstrap.sh: where the ${ACCOUNT} account may connect.
# A table of its own, so nothing else in this server's firewall is touched; loading this file again
# replaces this table only. A refusal is "administratively prohibited", so it is told at once.
# The "ct state" rule turns connection tracking on for the whole server if it was off.
table inet ${TABLE}
delete table inet ${TABLE}
table inet ${TABLE} {
	chain output {
		type filter hook output priority 0; policy accept;
		meta skuid "${ACCOUNT}" jump family
	}
	chain family {
		# replies, on a connection already let through and on one made to the account's own listener
		ct state established,related accept
		# this server: the ports the family view uses, and the local resolver
		ip daddr 127.0.0.1 tcp dport { ${PORTS} } accept
		ip daddr 127.0.0.53 udp dport 53 accept
		ip daddr 127.0.0.53 tcp dport 53 accept
		# every other port here: OpenAlgo's 5555 and 8765, the other ring services, other systems
		oifname "lo" reject with icmpx type admin-prohibited
		# elsewhere: HTTPS only (Telegram, the push services)
		tcp dport 443 accept
		reject with icmpx type admin-prohibited
	}
}
EOF
}
unit_text() {
    cat <<EOF
# Written by sudarshan/ops/family/bootstrap.sh. Loads the ${ACCOUNT} account's allow-list of outgoing
# connections (${NFT_PATH}) at boot. Its name does not start with sudarshan-, so the deploy tool never
# manages it. It has no stop command: stopping the unit leaves the rules in place. The server's own
# /etc/nftables.conf usually starts with "flush ruleset", which takes this table away too, so a
# restart or a reload of nftables.service loads the list again after it (PartOf, ReloadPropagatedFrom).
[Unit]
Description=SUDARSHAN family account: allow-list of outgoing connections (nftables)
After=nftables.service netfilter-persistent.service ufw.service
Before=sudarshan-family.service
PartOf=nftables.service
ReloadPropagatedFrom=nftables.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=${NFT_BIN} -f ${NFT_PATH}
ExecReload=${NFT_BIN} -f ${NFT_PATH}

[Install]
WantedBy=multi-user.target
EOF
}
put_file() {   # $1 = path, $2 = mode, $3 = the words for it; the content on stdin (a redirection, never a
               # pipe: a function at the end of a pipe runs in a subshell, and what it records would be
               # lost): written through a file beside it, renamed into place, only when it differs.
               # 0 = in place, 1 = not written
    local tmpf new
    new="$(cat)"
    if [[ -f "$1" && ! -L "$1" ]] && [[ "$(cat -- "$1")" == "${new}" ]]; then ok "$3 ($1)"; return 0; fi
    if [[ ${DRY} -eq 1 ]]; then changed "$3 written ($1)"; return 0; fi
    tmpf="$(mktemp -p "$(dirname -- "$1")" ".$(basename -- "$1").XXXXXX")" || { problem "$1 could not be written"; return 1; }
    if printf '%s\n' "${new}" > "${tmpf}" && chmod "$2" -- "${tmpf}" && { [[ "$1" != "${NFT_FILE}" ]] || nft -c -f "${tmpf}"; } \
            && mv -fT -- "${tmpf}" "$1"; then
        changed "$3 written ($1)"; return 0
    fi
    rm -f -- "${tmpf}"; problem "$1 could not be written"; return 1
}
fw_before="${#CHANGED[@]}"
put_file "${NFT_FILE}" 644 "the allow-list" < <(ruleset)
unit_before="${#CHANGED[@]}"
put_file "${UNIT_FILE}" 644 "the unit that loads it at boot" < <(unit_text)
if [[ "${#CHANGED[@]}" -gt "${unit_before}" ]]; then act systemctl daemon-reload || problem "systemd did not reload its units"; fi
if [[ "$(systemctl is-enabled "${FW_UNIT}" 2>/dev/null)" != "enabled" ]]; then
    if act systemctl enable "${FW_UNIT}" > /dev/null 2>&1; then
        changed "${FW_UNIT} enabled (it loads the allow-list at every boot)"
    else
        problem "${FW_UNIT} could not be enabled"
    fi
else
    ok "${FW_UNIT} is enabled"
fi
if [[ "${#CHANGED[@]}" -gt "${fw_before}" ]] || ! nft list table inet "${TABLE}" > /dev/null 2>&1 \
        || ! systemctl is-active --quiet "${FW_UNIT}" 2>/dev/null; then
    if act systemctl restart "${FW_UNIT}" && { [[ ${DRY} -eq 1 ]] || nft list table inet "${TABLE}" > /dev/null 2>&1; }; then
        changed "the allow-list loaded (table inet ${TABLE})"
    else
        problem "the allow-list did not load; see: journalctl -u ${FW_UNIT}"
    fi
else
    ok "the allow-list is loaded (table inet ${TABLE})"
fi
if [[ -f "${RESOLV}" ]] && ! grep -Eq '^[[:space:]]*nameserver[[:space:]]+127\.0\.0\.53([[:space:]]|$)' "${RESOLV}"; then
    say "NOTE: ${RESOLV} names a resolver other than 127.0.0.53. The family account may look names up only
through 127.0.0.53, so the family view could not reach Telegram or the push services by name."
fi

# ---- 4. OpenAlgo's folders other accounts can read: named, never changed ---------------------------
hints=()
for f in "${APP}/db" "${APP}/log" "${APP}/.env"; do
    mode="$(stat -c %a -- "${f}" 2>/dev/null)" || continue
    if (( (8#${mode} & 8#007) != 0 )); then hints+=("chmod o-rwx '${APP_DIR}${f#"${APP}"}'"); fi
done
if [[ ${#hints[@]} -gt 0 ]]; then
    say "Other accounts can read some of OpenAlgo's files, and the family check fails until they cannot."
    say "This set-up changes nothing there. Run these lines yourself (nothing that runs needs them open):"
    for h in "${hints[@]}"; do say "    ${h}"; done
fi

# ---- 5. The deploy tool's provision step, and the result ------------------------------------------
if [[ ${DRY} -eq 1 ]]; then
    say "would run: ${TOOL} --family-provision (the keys, the copies, then the check as ${ACCOUNT})"
    say "Dry run: nothing was changed."
    exit 0
fi
if [[ ${#PROBLEMS[@]} -gt 0 ]]; then
    say "FAIL: the provision step was not run, because of what failed above. Fix it and run this again."
    exit 1
fi
say "Running the provision step (${TOOL} --family-provision):"
out_file="$(mktemp)" || { say "FAIL: no temporary file for the provision step's output"; exit 1; }
"${TOOL}" --family-provision 2>&1 | tee "${out_file}"
rc="${PIPESTATUS[0]}"
if grep -Eq '^family: (written in |the check passed: .* written|.*provisioned removed)' "${out_file}"; then
    CHANGED+=("the provision step wrote what is listed above")
fi
rm -f -- "${out_file}"
if [[ "${rc}" -eq 0 ]]; then
    if [[ ${#CHANGED[@]} -eq 0 ]]; then
        say "PASS. Nothing was changed: everything was already in place, and the check passes."
    else
        say "PASS. The family account is set up and closed off from the owner's secrets."
    fi
    say "The family view itself comes in a later release, switched off; nothing more is needed until then."
    exit 0
fi
case "${rc}" in
    75) say "FAIL: an install was running, so the provision step did not run. Run this again in a few minutes." ;;
    2) say "FAIL: the provision step found no ${FAM_ETC}." ;;
    *) say "FAIL: the provision step did not pass. The lines starting with 'family: FAIL' or 'family: check: FAIL' above say what; fix that and run this again." ;;
esac
exit 1
