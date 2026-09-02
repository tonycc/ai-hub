#!/usr/bin/env bash
#
# Create the offline root CA used by an IP-only AI Hub intranet deployment.
# Run this on an operator workstation, not on the Mac mini. The root private
# key must never be copied to the server or committed to the repository.

set -euo pipefail

CA_DIR=""
CA_NAME="AI Hub Intranet Root CA"
CA_DAYS=3650

usage() {
  printf '%s\n' \
    'Create the offline root CA for an AI Hub IP-only deployment.' \
    '' \
    'Usage: bash scripts/deploy/init-intranet-ca.sh --ca-dir ABSOLUTE_PATH [--name NAME] [--days DAYS]'
}

fail() { printf 'init-intranet-ca: %s\n' "$1" >&2; exit 1; }

while (($# > 0)); do
  case "$1" in
    --ca-dir) CA_DIR="${2:?}"; shift 2 ;;
    --name) CA_NAME="${2:?}"; shift 2 ;;
    --days) CA_DAYS="${2:?}"; shift 2 ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; fail "unknown argument: $1" ;;
  esac
done

[[ -n "${CA_DIR}" ]] || fail "--ca-dir is required"
[[ "${CA_DIR}" == /* ]] || fail "--ca-dir must be an absolute path"
[[ "${CA_DIR}" != "/" ]] || fail "--ca-dir cannot be the filesystem root"
[[ "${CA_DAYS}" =~ ^[1-9][0-9]*$ ]] || fail "--days must be a positive integer"
((CA_DAYS >= 365)) || fail "--days must be at least 365"
command -v openssl >/dev/null 2>&1 || fail "openssl is required"

mkdir -p "${CA_DIR}"
chmod 700 "${CA_DIR}"

ROOT_KEY="${CA_DIR}/root-ca.key"
ROOT_CERT="${CA_DIR}/root-ca.crt"
[[ ! -e "${ROOT_KEY}" && ! -e "${ROOT_CERT}" ]] \
  || fail "root CA already exists in ${CA_DIR}; refusing to overwrite it"

umask 077
CONFIG="$(mktemp "${CA_DIR}/.root-ca-openssl.XXXXXX")"
cleanup() { rm -f "${CONFIG}"; }
trap cleanup EXIT

cat >"${CONFIG}" <<EOF
[req]
prompt = no
distinguished_name = distinguished_name
x509_extensions = root_ca

[distinguished_name]
O = AI Hub
CN = ${CA_NAME}

[root_ca]
basicConstraints = critical, CA:true, pathlen:0
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always
EOF

openssl genpkey \
  -algorithm RSA \
  -pkeyopt rsa_keygen_bits:3072 \
  -out "${ROOT_KEY}"
openssl req \
  -x509 \
  -new \
  -sha256 \
  -days "${CA_DAYS}" \
  -key "${ROOT_KEY}" \
  -config "${CONFIG}" \
  -out "${ROOT_CERT}"

chmod 600 "${ROOT_KEY}"
chmod 644 "${ROOT_CERT}"
openssl verify -CAfile "${ROOT_CERT}" "${ROOT_CERT}" >/dev/null

printf 'Created offline root CA:\n'
printf '  private key: %s (keep offline; never copy to the server)\n' "${ROOT_KEY}"
printf '  public cert: %s (install on clients and copy with the server certificate)\n' "${ROOT_CERT}"
openssl x509 -in "${ROOT_CERT}" -noout -fingerprint -sha256
