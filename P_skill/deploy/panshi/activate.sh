#!/bin/sh
set -eu
cd /opt/industrial-agents
test -s /tmp/htpasswd.admin
test -s /tmp/htpasswd.ssh
install -o root -g www-data -m 640 /tmp/htpasswd.admin /etc/nginx/industrial-htpasswd.admin
install -o root -g www-data -m 640 /tmp/htpasswd.ssh /etc/nginx/industrial-htpasswd.ssh
cp /etc/nginx/nginx.conf /etc/nginx/nginx.conf.before-industrial-$(date +%Y%m%d%H%M%S)
cp P_skill/deploy/panshi/nginx.conf /etc/nginx/nginx.conf
nginx -t
docker compose up -d --no-build
systemctl enable docker nginx
systemctl reload nginx
