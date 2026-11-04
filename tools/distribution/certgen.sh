#!/bin/bash
# Local-only CA; no private keys are downloaded or published.
set -euo pipefail
umask 077
cd "${HOA_CERTS_DIR:-/certs}"
if [[ -f forever.cert.pem && -f forever.key.pem && -f ca/forever_root_ca.crt ]]; then
    openssl verify -CAfile ca/forever_root_ca.crt forever.cert.pem
    exit
fi
if [[ -n "$(ls -A .)" ]]; then
    echo "Certificate folder is incomplete; refusing to replace existing keys" >&2
    exit 1
fi
mkdir ca
openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 3650 \
    -keyout ca/forever_root_ca.key -out ca/forever_root_ca.crt \
    -subj '/CN=Hordes Offline Local CA' \
    -addext 'basicConstraints=critical,CA:TRUE,pathlen:0' \
    -addext 'keyUsage=critical,keyCertSign,cRLSign' \
    -addext 'nameConstraints=critical,permitted;DNS:actual.battle.net'
openssl req -newkey rsa:2048 -sha256 -nodes -keyout forever.key.pem \
    -out request.csr -subj '/CN=trinity.actual.battle.net'
cat > extension.cnf <<'EOF'
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:trinity.actual.battle.net
EOF
openssl x509 -req -in request.csr -CA ca/forever_root_ca.crt -CAkey ca/forever_root_ca.key \
    -CAcreateserial -days 825 -sha256 -extfile extension.cnf -out server.crt
cat server.crt ca/forever_root_ca.crt > forever.cert.pem
openssl verify -CAfile ca/forever_root_ca.crt forever.cert.pem
rm request.csr extension.cnf server.crt
