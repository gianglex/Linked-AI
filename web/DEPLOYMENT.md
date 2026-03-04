# Deployment Guide - LinkedIn Post Generator Web App

Step-by-step guide to deploy this app on an existing Linux web server.

## Architecture

The app is split into two parts:

```
/var/www/html/linked-ai.html          Static HTML served by Apache/Nginx
/var/www/linkedin-posts/              Project root
    linked-ai.html                    Source HTML (copy to web root)
    linked-ai/                        Flask API backend
        linked-ai.py
        wsgi.py
        requirements.txt
        sources.md
        sample.md
```

- `linked-ai.html` -- standalone dark-mode frontend, placed in your web root
- `linked-ai/` -- Flask API backend, handles /defaults, /session, /models, /generate
- Apache/Nginx serves the HTML directly and proxies API routes to the Flask backend

---

## Prerequisites

- A Linux server (Ubuntu/Debian) with SSH access
- Python 3.10+ installed
- An existing web server: Apache or Nginx (guides for both below)
- A domain name pointing to your server (e.g. posts.yourdomain.com)
- SSL certificate (Let's Encrypt is free)

---

## Step 1: Upload the files

Copy the `web/` folder to your server:

```bash
# From your local machine
scp -r web/ user@yourserver:/var/www/linkedin-posts/
```

Or clone your Git repo directly on the server:

```bash
cd /var/www
git clone https://your-repo-url.git linkedin-posts
```

Copy the frontend HTML to your web server root:

```bash
sudo cp /var/www/linkedin-posts/linked-ai.html /var/www/html/linked-ai.html
```

---

## Step 2: Create a Python virtual environment

```bash
cd /var/www/linkedin-posts
python3 -m venv venv
source venv/bin/activate
pip install -r linked-ai/requirements.txt
```

---

## Step 3: Install and configure Redis (for session storage)

```bash
sudo apt update
sudo apt install redis-server -y
sudo systemctl enable redis-server
sudo systemctl start redis-server

# Test it works
redis-cli ping
# Should print: PONG
```

---

## Step 4: Create a systemd service

This keeps the API running in the background and restarts it on crashes or reboots.

Create `/etc/systemd/system/linkedin-posts.service`:

```ini
[Unit]
Description=LinkedIn Post Generator API
After=network.target redis-server.service

[Service]
User=www-data
Group=www-data
WorkingDirectory=/var/www/linkedin-posts/linked-ai
Environment="REDIS_URL=redis://localhost:6379"
Environment="PORT=8000"
Environment="WORKERS=2"
ExecStart=/var/www/linkedin-posts/venv/bin/gunicorn wsgi:application --bind 127.0.0.1:8000 --workers 2 --timeout 300 --access-logfile /var/log/linkedin-posts/access.log --error-logfile /var/log/linkedin-posts/error.log
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Then enable it:

```bash
# Create log directory
sudo mkdir -p /var/log/linkedin-posts
sudo chown www-data:www-data /var/log/linkedin-posts

# Set file ownership
sudo chown -R www-data:www-data /var/www/linkedin-posts

# Enable and start the service
sudo systemctl daemon-reload
sudo systemctl enable linkedin-posts
sudo systemctl start linkedin-posts

# Check it's running
sudo systemctl status linkedin-posts
```

---

## Step 5a: Configure Nginx (option A)

If you use Nginx as your web server.

Create `/etc/nginx/sites-available/linkedin-posts`:

```nginx
server {
    listen 443 ssl;
    server_name posts.yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/posts.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/posts.yourdomain.com/privkey.pem;

    root /var/www/html;

    location = /linked-ai.html {
        try_files $uri =404;
    }

    location /defaults {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /session {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /models {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /generate/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE requires these settings
        proxy_buffering off;
        proxy_cache off;
        proxy_http_version 1.1;
        proxy_set_header Connection '';
        proxy_read_timeout 300s;
    }
}

server {
    listen 80;
    server_name posts.yourdomain.com;
    return 301 https://$host$request_uri;
}
```

Enable and test:

```bash
sudo ln -s /etc/nginx/sites-available/linkedin-posts /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

---

## Step 5b: Configure Apache (option B)

If you use Apache as your web server.

### Option B1: Apache as reverse proxy to Gunicorn (recommended)

Enable the required modules:

```bash
sudo a2enmod proxy proxy_http ssl headers
```

Create `/etc/apache2/sites-available/linkedin-posts.conf`:

```apache
<VirtualHost *:443>
    ServerName posts.yourdomain.com

    SSLEngine on
    SSLCertificateFile    /etc/letsencrypt/live/posts.yourdomain.com/fullchain.pem
    SSLCertificateKeyFile /etc/letsencrypt/live/posts.yourdomain.com/privkey.pem

    DocumentRoot /var/www/html

    ProxyPreserveHost On

    ProxyPass /defaults http://127.0.0.1:8000/defaults
    ProxyPassReverse /defaults http://127.0.0.1:8000/defaults

    ProxyPass /session http://127.0.0.1:8000/session
    ProxyPassReverse /session http://127.0.0.1:8000/session

    ProxyPass /models http://127.0.0.1:8000/models
    ProxyPassReverse /models http://127.0.0.1:8000/models

    ProxyPassMatch ^/generate/(.*) http://127.0.0.1:8000/generate/$1
    ProxyPassReverse /generate/ http://127.0.0.1:8000/generate/

    SetEnv proxy-sendchunked 1
    SetEnv proxy-sendcl 0
    ProxyTimeout 300

    RequestHeader set X-Forwarded-Proto "https"
    RequestHeader set X-Forwarded-For "%{REMOTE_ADDR}s"

    ErrorLog  ${APACHE_LOG_DIR}/linkedin-posts-error.log
    CustomLog ${APACHE_LOG_DIR}/linkedin-posts-access.log combined
</VirtualHost>

<VirtualHost *:80>
    ServerName posts.yourdomain.com
    Redirect permanent / https://posts.yourdomain.com/
</VirtualHost>
```

Enable and test:

```bash
sudo a2ensite linkedin-posts
sudo apache2ctl configtest
sudo systemctl reload apache2
```

### Option B2: Apache with mod_wsgi (alternative, no Gunicorn needed)

```bash
sudo apt install libapache2-mod-wsgi-py3
sudo a2enmod wsgi ssl
```

Create `/etc/apache2/sites-available/linkedin-posts.conf`:

```apache
<VirtualHost *:443>
    ServerName posts.yourdomain.com

    SSLEngine on
    SSLCertificateFile    /etc/letsencrypt/live/posts.yourdomain.com/fullchain.pem
    SSLCertificateKeyFile /etc/letsencrypt/live/posts.yourdomain.com/privkey.pem

    DocumentRoot /var/www/html

    WSGIDaemonProcess linkedinposts python-home=/var/www/linkedin-posts/venv python-path=/var/www/linkedin-posts/linked-ai threads=5 request-timeout=300
    WSGIProcessGroup linkedinposts

    WSGIScriptAlias /defaults /var/www/linkedin-posts/linked-ai/wsgi.py/defaults
    WSGIScriptAlias /session /var/www/linkedin-posts/linked-ai/wsgi.py/session
    WSGIScriptAlias /models /var/www/linkedin-posts/linked-ai/wsgi.py/models
    WSGIScriptAlias /generate /var/www/linkedin-posts/linked-ai/wsgi.py/generate

    SetEnv REDIS_URL redis://localhost:6379

    <Directory /var/www/linkedin-posts/linked-ai>
        Require all granted
    </Directory>

    ErrorLog  ${APACHE_LOG_DIR}/linkedin-posts-error.log
    CustomLog ${APACHE_LOG_DIR}/linkedin-posts-access.log combined
</VirtualHost>

<VirtualHost *:80>
    ServerName posts.yourdomain.com
    Redirect permanent / https://posts.yourdomain.com/
</VirtualHost>
```

Enable and test:

```bash
sudo a2ensite linkedin-posts
sudo apache2ctl configtest
sudo systemctl reload apache2
```

> Note: mod_wsgi may have issues with SSE streaming. The reverse proxy approach (Option B1) is more reliable for real-time features.

---

## Step 6: Set up SSL with Let's Encrypt

If you don't have SSL certificates yet:

```bash
sudo apt install certbot

# For Nginx
sudo certbot --nginx -d posts.yourdomain.com

# For Apache
sudo certbot --apache -d posts.yourdomain.com
```

Certbot auto-renews certificates. Verify with:

```bash
sudo certbot renew --dry-run
```

---

## Step 7: Verify the deployment

1. Open https://posts.yourdomain.com/linked-ai.html in your browser
2. Enter a Gemini API key and click Generate
3. Watch the progress panel for real-time updates
4. Check logs if anything goes wrong:

```bash
# App logs
sudo journalctl -u linkedin-posts -f

# Or log files
tail -f /var/log/linkedin-posts/error.log

# Nginx logs
tail -f /var/log/nginx/error.log

# Apache logs
tail -f /var/log/apache2/linkedin-posts-error.log
```

---

## Configuring the API base URL

By default, `linked-ai.html` calls the API on the same origin using relative paths (/session, /models, etc.). This works when Apache/Nginx proxies those paths to the Flask backend.

If you need the API under a prefix (e.g. /li-api/), edit the `API_BASE` variable at the top of the JavaScript in linked-ai.html:

```javascript
const API_BASE = '/li-api';
```

And update your proxy config accordingly:

```apache
ProxyPass /li-api/ http://127.0.0.1:8000/
ProxyPassReverse /li-api/ http://127.0.0.1:8000/
```

---

## Environment variables reference

| Variable | Default | Description |
|----------|---------|-------------|
| PORT | 5000 | Port Gunicorn listens on |
| WORKERS | 2 | Number of Gunicorn workers |
| REDIS_URL | redis://localhost:6379 | Redis connection URL for session storage |
| FLASK_ENV | (empty) | Set to development for dev mode |

---

## Updating the app

```bash
cd /var/www/linkedin-posts

# Pull latest code
git pull

# Update the frontend HTML in the web root
sudo cp linked-ai.html /var/www/html/linked-ai.html

# Update dependencies (if requirements.txt changed)
source venv/bin/activate
pip install -r linked-ai/requirements.txt

# Restart the service
sudo systemctl restart linkedin-posts
```

---

## Security checklist

Before going live, verify:

- [ ] HTTPS is working (no plain HTTP access)
- [ ] Redis is running (redis-cli ping → PONG, needed for session tokens)
- [ ] App is bound to 127.0.0.1 (not 0.0.0.0)
- [ ] Files owned by www-data (not root)
- [ ] --dev flag is NOT used in production
- [ ] Firewall blocks direct access to port 8000 from outside
- [ ] Let's Encrypt auto-renewal is working
- [ ] Logs are being written and rotated

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| 502 Bad Gateway | Gunicorn not running. Check: systemctl status linkedin-posts |
| SSE not streaming | Nginx: add proxy_buffering off; / Apache: use reverse proxy (B1) |
| Sessions failing | Check Redis is running: redis-cli ping → PONG |
| Permission denied | Run: sudo chown -R www-data:www-data /var/www/linkedin-posts |
| SSL errors to Gemini | Make sure --dev is NOT used in production |
| 504 Gateway Timeout | Increase proxy_read_timeout (Nginx) or ProxyTimeout (Apache) to 300+ |
| Loading defaults stuck | Check API routes are proxied: curl https://yourdomain.com/defaults |
