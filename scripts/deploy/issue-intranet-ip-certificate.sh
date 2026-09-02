#!/usr/bin/env bash
#
# Issue a TLS server certificate whose SAN contains one private IPv4 address.
# The same certificate is used by the platform (443) and authentik (8443);
# certificate validation does not include the TCP port.

set -euo pipefail

CA_DIR=""
OUTPUT_DIR=""
SERVER_IP=""
CERT_DAYS=365
FORCE=0

usage() {
  printf '%s\n' \
    'Issue a server certificate with one private IPv4 SAN.' \
    '' \
    'Usage: bash scripts/deploy/issue-intranet-ip-certificate.sh --ca-dir ABSOLUTE_PATH --ip PRIVATE_IPV4 --output-dir ABSOLUTE_PATH [--days DAYS] [--force]'
}

fail() { printf 'issue-intranet-ip-certificate: %s\n' "$1" >&2; exit 1; }

is_private_ipv4() {
  [[ "$1" != *$'\n'* && "$1" != *$'\r'* ]] || return 1
  awk -F. '
    NF != 4 { exit 1 }
    {
      for (i = 1; i <= 4; i++) {
        if ($i !~ /^[0-9]+$/ || $i < 0 || $i > 255) exit 1
      }
      if ($1 == 10) exit 0
      if ($1 == 172 && $2 >= 16 && $2 <= 31) exit 0
      if ($1 == 192 && $2 == 168) exit 0
      exit 1
    }
  ' <<<"$1"
}

while (($# > 0)); do
  case "$1" in
    --ca-dir) CA_DIR="${2:?}"; shift 2 ;;
    --ip) SERVER_IP="${2:?}"; shift 2 ;;
    --output-dir) OUTPUT_DIR="${2:?}"; shift 2 ;;
    --days) CERT_DAYS="${2:?}"; shift 2 ;;
    --force) FORCE=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; fail "unknown argument: $1" ;;
  esac
done

[[ -n "${CA_DIR}" ]] || fail "--ca-dir is required"
[[ -n "${OUTPUT_DIR}" ]] || fail "--output-dir is required"
[[ "${CA_DIR}" == /* && "${OUTPUT_DIR}" == /* ]] \
  || fail "--ca-dir and --output-dir must be absolute paths"
[[ "${CA_DIR}" != "/" && "${OUTPUT_DIR}" != "/" ]] \
  || fail "--ca-dir and --output-dir cannot be the filesystem root"
is_private_ipv4 "${SERVER_IP}" || fail "--ip must be an RFC1918 private IPv4 address"
[[ "${CERT_DAYS}" =~ ^[1-9][0-9]*$ ]] || fail "--days must be a positive integer"
((CERT_DAYS >= 1 && CERT_DAYS <= 825)) || fail "--days must be between 1 and 825"
command -v openssl >/dev/null 2>&1 || fail "openssl is required"

ROOT_KEY="${CA_DIR}/root-ca.key"
ROOT_CERT="${CA_DIR}/root-ca.crt"
[[ -f "${ROOT_KEY}" && -f "${ROOT_CERT}" ]] \
  || fail "root-ca.key/root-ca.crt not found in ${CA_DIR}"

mkdir -p "${OUTPUT_DIR}"
chmod 700 "${OUTPUT_DIR}"
for target in server.key server.crt root-ca.crt; do
  if [[ -e "${OUTPUT_DIR}/${target}" && "${FORCE}" -ne 1 ]]; then
    fail "${OUTPUT_DIR}/${target} already exists (use --force to replace the server material)"
  fi
done

umask 077
STAGE_DIR="$(mktemp -d "${OUTPUT_DIR}/.issue.XXXXXX")"
cleanup() { rm -rf "${STAGE_DIR}"; }
trap cleanup EXIT

cat >"${STAGE_DIR}/server-extensions.cnf" <<EOF
[server_certificate]
basicConstraints = critical, CA:false
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
subjectAltName = @subject_alt_names

[subject_alt_names]
IP.1 = ${SERVER_IP}
EOF

openssl genpkey \
  -algorithm RSA \
  -pkeyopt rsa_keygen_bits:3072 \
  -out "${STAGE_DIR}/server.key"
openssl req \
  -new \
  -sha256 \
  -key "${STAGE_DIR}/server.key" \
  -subj "/O=AI Hub/CN=${SERVER_IP}" \
  -out "${STAGE_DIR}/server.csr"
openssl x509 \
  -req \
  -sha256 \
  -days "${CERT_DAYS}" \
  -in "${STAGE_DIR}/server.csr" \
  -CA "${ROOT_CERT}" \
  -CAkey "${ROOT_KEY}" \
  -CAcreateserial \
  -extfile "${STAGE_DIR}/server-extensions.cnf" \
  -extensions server_certificate \
  -out "${STAGE_DIR}/server.crt"

openssl verify -CAfile "${ROOT_CERT}" "${STAGE_DIR}/server.crt" >/dev/null
if openssl x509 -help 2>&1 | grep -q -- '-checkip'; then
  openssl x509 -in "${STAGE_DIR}/server.crt" -noout -checkip "${SERVER_IP}" >/dev/null \
    || fail "issued certificate does not contain IP SAN ${SERVER_IP}"
else
  openssl x509 -in "${STAGE_DIR}/server.crt" -noout -text \
    | grep -F "IP Address:${SERVER_IP}" >/dev/null \
    || fail "issued certificate does not contain IP SAN ${SERVER_IP}"
fi

install -m 0600 "${STAGE_DIR}/server.key" "${OUTPUT_DIR}/server.key"
install -m 0644 "${STAGE_DIR}/server.crt" "${OUTPUT_DIR}/server.crt"
install -m 0644 "${ROOT_CERT}" "${OUTPUT_DIR}/root-ca.crt"

printf 'Issued intranet certificate for %s:\n' "${SERVER_IP}"
printf '  server key:  %s\n' "${OUTPUT_DIR}/server.key"
printf '  server cert: %s\n' "${OUTPUT_DIR}/server.crt"
printf '  client CA:   %s\n' "${OUTPUT_DIR}/root-ca.crt"
printf 'Copy only these three files to the Mac mini; keep %s offline.\n' "${ROOT_KEY}"
