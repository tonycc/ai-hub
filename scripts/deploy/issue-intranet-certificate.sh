#!/usr/bin/env bash
# Issue an AI Hub certificate containing every enabled IP and DNS SAN.

set -eo pipefail

ca_dir=''
output_dir=''
cert_days=365
force=0
server_ips=()
dns_names=()

fail() { printf 'issue-intranet-certificate: %s\n' "$1" >&2; exit 1; }
usage() {
  printf '%s\n' \
    'Usage: bash scripts/deploy/issue-intranet-certificate.sh --ca-dir ABSOLUTE_PATH' \
    '  (--ip PRIVATE_IPV4 | --dns LOWERCASE_NAME)... --output-dir ABSOLUTE_PATH' \
    '  [--days DAYS] [--force]'
}
is_private_ipv4() {
  [[ "$1" != *$'\n'* && "$1" != *$'\r'* ]] || return 1
  awk -F. '
    NF != 4 { exit 1 }
    { for (i = 1; i <= 4; i++) if ($i !~ /^[0-9]+$/ || $i < 0 || $i > 255) exit 1 }
    $1 == 10 { exit 0 }
    $1 == 172 && $2 >= 16 && $2 <= 31 { exit 0 }
    $1 == 192 && $2 == 168 { exit 0 }
    { exit 1 }
  ' <<<"$1"
}
is_valid_dns_name() {
  local name=$1 label old_ifs
  [[ ${#name} -le 253 && "${name}" != *[A-Z]* && "${name}" != .* && "${name}" != *. ]] || return 1
  [[ "${name}" =~ ^[a-z0-9.-]+$ && "${name}" == *.* ]] || return 1
  old_ifs=${IFS}
  IFS='.' read -r -a labels <<<"${name}"
  IFS=${old_ifs}
  for label in "${labels[@]}"; do
    [[ ${#label} -ge 1 && ${#label} -le 63 ]] || return 1
    [[ "${label}" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]] || return 1
  done
}
contains_value() {
  local candidate=$1 value
  shift
  for value in "$@"; do
    [[ "${value}" != "${candidate}" ]] || return 0
  done
  return 1
}

while (($# > 0)); do
  case "$1" in
    --ca-dir) ca_dir=${2:?}; shift 2 ;;
    --ip) server_ips+=("${2:?}"); shift 2 ;;
    --dns) dns_names+=("${2:?}"); shift 2 ;;
    --output-dir) output_dir=${2:?}; shift 2 ;;
    --days) cert_days=${2:?}; shift 2 ;;
    --force) force=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; fail "unknown argument: $1" ;;
  esac
done

[[ -n "${ca_dir}" && -n "${output_dir}" ]] || fail '--ca-dir and --output-dir are required'
[[ "${ca_dir}" == /* && "${output_dir}" == /* && "${ca_dir}" != / && "${output_dir}" != / ]] \
  || fail '--ca-dir and --output-dir must be absolute non-root paths'
((${#server_ips[@]} + ${#dns_names[@]} > 0)) || fail 'at least one SAN is required'
[[ "${cert_days}" =~ ^[1-9][0-9]*$ ]] || fail '--days must be a positive integer'
((cert_days <= 825)) || fail '--days must be between 1 and 825'
command -v openssl >/dev/null 2>&1 || fail 'openssl is required'

validated=()
for server_ip in "${server_ips[@]}"; do
  is_private_ipv4 "${server_ip}" || fail "invalid RFC1918 address: ${server_ip}"
  contains_value "IP:${server_ip}" "${validated[@]}" && fail "duplicate SAN: IP:${server_ip}"
  validated+=("IP:${server_ip}")
done
for dns_name in "${dns_names[@]}"; do
  is_valid_dns_name "${dns_name}" || fail "invalid lowercase DNS name: ${dns_name}"
  contains_value "DNS:${dns_name}" "${validated[@]}" && fail "duplicate SAN: DNS:${dns_name}"
  validated+=("DNS:${dns_name}")
done

root_key="${ca_dir}/root-ca.key"
root_cert="${ca_dir}/root-ca.crt"
[[ -f "${root_key}" && -f "${root_cert}" ]] || fail "root CA files not found in ${ca_dir}"
mkdir -p "${output_dir}"
chmod 700 "${output_dir}"
for target in server.key server.crt root-ca.crt; do
  [[ ! -e "${output_dir}/${target}" || "${force}" -eq 1 ]] || fail "${target} already exists; use --force"
done

umask 077
stage_dir=$(mktemp -d "${output_dir}/.issue.XXXXXX")
cleanup() { rm -rf "${stage_dir}"; }
trap cleanup EXIT
extensions_file="${stage_dir}/server-extensions.cnf"
{
  printf '%s\n' '[server_certificate]' 'basicConstraints = critical, CA:false' \
    'keyUsage = critical, digitalSignature, keyEncipherment' 'extendedKeyUsage = serverAuth' \
    'subjectKeyIdentifier = hash' 'authorityKeyIdentifier = keyid,issuer' \
    'subjectAltName = @subject_alt_names' '' '[subject_alt_names]'
  san_index=1
  for server_ip in "${server_ips[@]}"; do
    printf 'IP.%d = %s\n' "${san_index}" "${server_ip}"
    san_index=$((san_index + 1))
  done
  san_index=1
  for dns_name in "${dns_names[@]}"; do
    printf 'DNS.%d = %s\n' "${san_index}" "${dns_name}"
    san_index=$((san_index + 1))
  done
} >"${extensions_file}"
if ((${#dns_names[@]} > 0)); then common_name=${dns_names[0]}; else common_name=${server_ips[0]}; fi
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out "${stage_dir}/server.key"
openssl req -new -sha256 -key "${stage_dir}/server.key" \
  -subj "/O=AI Hub/CN=${common_name}" -out "${stage_dir}/server.csr"
openssl x509 -req -sha256 -days "${cert_days}" -in "${stage_dir}/server.csr" \
  -CA "${root_cert}" -CAkey "${root_key}" -CAcreateserial -extfile "${extensions_file}" \
  -extensions server_certificate -out "${stage_dir}/server.crt"
openssl verify -CAfile "${root_cert}" "${stage_dir}/server.crt" >/dev/null
for server_ip in "${server_ips[@]}"; do
  if openssl x509 -help 2>&1 | grep -q -- '-checkip'; then
    openssl x509 -in "${stage_dir}/server.crt" -noout -checkip "${server_ip}" >/dev/null \
      || fail "missing IP SAN ${server_ip}"
  else
    openssl x509 -in "${stage_dir}/server.crt" -noout -text \
      | grep -F "IP Address:${server_ip}" >/dev/null || fail "missing IP SAN ${server_ip}"
  fi
done
for dns_name in "${dns_names[@]}"; do
  if openssl x509 -help 2>&1 | grep -q -- '-checkhost'; then
    openssl x509 -in "${stage_dir}/server.crt" -noout -checkhost "${dns_name}" >/dev/null \
      || fail "missing DNS SAN ${dns_name}"
  else
    openssl x509 -in "${stage_dir}/server.crt" -noout -text \
      | grep -F "DNS:${dns_name}" >/dev/null || fail "missing DNS SAN ${dns_name}"
  fi
done
install -m 0600 "${stage_dir}/server.key" "${output_dir}/server.key"
install -m 0644 "${stage_dir}/server.crt" "${output_dir}/server.crt"
install -m 0644 "${root_cert}" "${output_dir}/root-ca.crt"
printf 'Issued AI Hub certificate for %s. Keep %s offline.\n' "${validated[*]}" "${root_key}"
