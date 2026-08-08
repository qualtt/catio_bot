#!/bin/bash
set -e

echo "Removing iptables redirect..."
iptables -t nat -D PREROUTING -d 217.114.11.194 -p tcp --dport 443 -j REDIRECT --to-port 8443 || true
iptables -t nat -D PREROUTING -p tcp --dport 443 -j REDIRECT --to-port 8443 || true
if command -v iptables-save >/dev/null; then iptables-save > /etc/iptables/rules.v4 || true; fi

echo "Opening port 80..."
if command -v ufw >/dev/null; then ufw allow 80/tcp || true; fi
iptables -I INPUT -p tcp --dport 80 -j ACCEPT || true

echo "Installing Nginx and Certbot..."
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y nginx certbot python3-certbot-nginx

echo "Configuring Nginx basic..."
cat <<'EOF' > /etc/nginx/sites-available/dash.qualtt.ru
server {
    listen 80;
    server_name dash.qualtt.ru;

    location / {
        proxy_pass https://127.0.0.1:8443;
        proxy_ssl_verify off;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # WebSockets support for terminal
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
EOF

ln -sf /etc/nginx/sites-available/dash.qualtt.ru /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
systemctl restart nginx

echo "Running Certbot..."
certbot --nginx -d dash.qualtt.ru --non-interactive --agree-tos --register-unsafely-without-email --redirect

systemctl restart nginx
echo "SSL configured successfully!"
