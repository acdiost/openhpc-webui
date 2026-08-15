#!/usr/bin/env bash

set -euo pipefail

# ============================================================
# OpenHPC WebUI Nginx HTTPS 部署脚本
# ============================================================

# 当前脚本所在目录
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="${BASE_DIR}/deploy/nginx"
CERT_DIR="${DEPLOY_DIR}/certs"

CONTAINER_NAME="openhpc_webui_nginx"
IMAGE="nginx:latest"

# HTTPS 服务地址
SERVER_NAME="slurm.acdiost.com"
SERVER_IP="10.80.4.30"

# WebUI 后端
BACKEND="http://127.0.0.1:6827"

echo "============================================================"
echo " OpenHPC WebUI Nginx Deployment"
echo "============================================================"
echo
echo "BASE_DIR   : ${BASE_DIR}"
echo "DEPLOY_DIR : ${DEPLOY_DIR}"
echo "SERVER_NAME: ${SERVER_NAME}"
echo "SERVER_IP  : ${SERVER_IP}"
echo "BACKEND    : ${BACKEND}"
echo

# ------------------------------------------------------------
# 1. 创建目录
# ------------------------------------------------------------

echo "[1/6] Creating directories..."

mkdir -p "${CERT_DIR}"

# ------------------------------------------------------------
# 2. 创建 nginx.conf
# ------------------------------------------------------------

echo "[2/6] Creating nginx.conf..."

cat > "${DEPLOY_DIR}/nginx.conf" <<'EOF'
user nginx;
worker_processes 4;

error_log /var/log/nginx/error.log notice;
pid /var/run/nginx.pid;

events {
    worker_connections 1024;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    log_format main
        "$remote_addr - $remote_user [$time_local] "
        "\"$request\" $status $body_bytes_sent "
        "\"$http_referer\" \"$http_user_agent\"";

    access_log /var/log/nginx/access.log main;

    sendfile on;
    keepalive_timeout 65;

    include /etc/nginx/conf.d/*.conf;
}
EOF

# ------------------------------------------------------------
# 3. 创建 default.conf
# ------------------------------------------------------------

echo "[3/6] Creating default.conf..."

cat > "${DEPLOY_DIR}/default.conf" <<EOF
server {
    listen 443 ssl;
    server_name ${SERVER_IP} ${SERVER_NAME};

    ssl_certificate /etc/nginx/certs/openhpc_webui.crt;
    ssl_certificate_key /etc/nginx/certs/openhpc_webui.key;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;

    location / {
        proxy_pass ${BACKEND};

        proxy_http_version 1.1;

        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;

        proxy_connect_timeout 10s;
        proxy_read_timeout 120s;
    }
}
EOF

# ------------------------------------------------------------
# 4. 创建 OpenSSL SAN 配置
# ------------------------------------------------------------

echo "[4/6] Creating openssl-san.cnf..."

cat > "${DEPLOY_DIR}/openssl-san.cnf" <<EOF
[req]
prompt = no
distinguished_name = dn
x509_extensions = v3_req

[dn]
CN = ${SERVER_NAME}
O = openhpc_webui

[v3_req]
subjectAltName = @alt_names
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
basicConstraints = critical, CA:false

[alt_names]
DNS.1 = ${SERVER_NAME}
IP.1 = ${SERVER_IP}
EOF

# ------------------------------------------------------------
# 5. 生成 HTTPS 证书
# ------------------------------------------------------------

echo "[5/6] Generating TLS certificate..."

openssl req \
    -x509 \
    -nodes \
    -newkey rsa:4096 \
    -sha256 \
    -days 3650 \
    -config "${DEPLOY_DIR}/openssl-san.cnf" \
    -keyout "${CERT_DIR}/openhpc_webui.key" \
    -out "${CERT_DIR}/openhpc_webui.crt"

chmod 600 "${CERT_DIR}/openhpc_webui.key"
chmod 644 "${CERT_DIR}/openhpc_webui.crt"

echo
echo "Certificate information:"
openssl x509 \
    -in "${CERT_DIR}/openhpc_webui.crt" \
    -noout \
    -subject \
    -issuer \
    -dates \
    -ext subjectAltName

# ------------------------------------------------------------
# 6. Docker Nginx
# ------------------------------------------------------------

echo
echo "[6/6] Starting Nginx container..."

# 如果已经存在旧容器，先删除
if docker ps -a --format '{{.Names}}' | grep -qx "${CONTAINER_NAME}"; then
    echo "Removing existing container: ${CONTAINER_NAME}"
    docker rm -f "${CONTAINER_NAME}"
fi

docker run -d \
    --name "${CONTAINER_NAME}" \
    --restart unless-stopped \
    --network host \
    -v "${DEPLOY_DIR}/nginx.conf:/etc/nginx/nginx.conf:ro" \
    -v "${DEPLOY_DIR}/default.conf:/etc/nginx/conf.d/default.conf:ro" \
    -v "${CERT_DIR}:/etc/nginx/certs:ro" \
    "${IMAGE}"

echo
echo "============================================================"
echo " Deployment completed successfully"
echo "============================================================"
echo
echo "Container:"
docker ps --filter "name=${CONTAINER_NAME}"

echo
echo "Access:"
echo "  https://${SERVER_IP}"
echo "  https://${SERVER_NAME}"
echo
echo "Backend:"
echo "  ${BACKEND}"
echo
echo "Certificate:"
echo "  ${CERT_DIR}/openhpc_webui.crt"
echo
echo "Private key:"
echo "  ${CERT_DIR}/openhpc_webui.key"
