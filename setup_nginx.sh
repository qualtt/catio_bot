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

echo "Configuring Nginx for catio.qualtt.ru..."
cat <<'EOF' > /etc/nginx/sites-available/catio.qualtt.ru
server {
    listen 80;
    server_name catio.qualtt.ru;

    # Static Web App React build
    location / {
        root /app/web/dist;
        index index.html;
        try_files $uri $uri/ /index.html;
    }

    # FastAPI REST API Backend
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF

ln -sf /etc/nginx/sites-available/catio.qualtt.ru /etc/nginx/sites-enabled/
systemctl restart nginx

echo "Running Certbot for catio.qualtt.ru..."
certbot --nginx -d catio.qualtt.ru --non-interactive --agree-tos --register-unsafely-without-email --redirect

systemctl restart nginx
echo "SSL configured successfully for catio.qualtt.ru!"
