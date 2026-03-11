# Cloudways Deployment Guide — Voter ID Card Generator

## Architecture on Cloudways

```
Internet → Cloudways Nginx → Gunicorn (port 8000) → Flask App
                                                   ↕
                                           Redis (sessions/cache)
                                           Celery (background jobs)
                                           MySQL (remote: 174.138.49.116)
                                           Cloudinary (file storage)
```

---

## Step 1: Create Server & Application on Cloudways

1. **Login** to [Cloudways Dashboard](https://platform.cloudways.com)
2. **Launch Server:**
   - Click **"+ Add Server"**
   - Choose provider: **DigitalOcean** (recommended) or Vultr/Linode
   - Server size: **2 GB RAM / 1 vCPU** minimum (for Redis + Celery + Gunicorn)
   - Data center: Choose closest to your users (e.g., **Bangalore** for India)
3. **Create Application:**
   - Application type: **Custom PHP** (we'll replace PHP with Python)
   - Name: `idcard` (or whatever you prefer)
   - Click **Launch Now** and wait for provisioning

---

## Step 2: Point Your Domain

1. In Cloudways Panel → **Application** → **Domain Management**
2. Add your domain: `www.puratchithaai.org`
3. Set it as **Primary Domain**
4. Update DNS at your domain registrar:
   - **A Record**: `@` → `YOUR_CLOUDWAYS_SERVER_IP`
   - **A Record**: `www` → `YOUR_CLOUDWAYS_SERVER_IP`
5. Wait for DNS propagation (5-30 minutes)

---

## Step 3: Enable SSL (Free Let's Encrypt)

1. Cloudways Panel → **Application** → **SSL Certificate**
2. Select **Let's Encrypt**
3. Enter email & domain: `www.puratchithaai.org`
4. Check **Auto-Renewal**
5. Click **Install Certificate**

---

## Step 4: SSH into Server & Upload Code

### Option A: Git Clone (Recommended)

```bash
# SSH into server (get credentials from Cloudways Panel → Server → Master Credentials)
ssh master@YOUR_SERVER_IP

# Navigate to application directory
cd /home/master/applications/YOUR_APP_FOLDER/public_html

# Remove default PHP files
rm -rf *

# Clone your repo
git clone https://github.com/YOUR_USERNAME/IDcard.git .
```

### Option B: SFTP Upload

1. Use **FileZilla** or **WinSCP**
2. Connect with Master Credentials from Cloudways Panel
3. Upload project files to `/home/master/applications/YOUR_APP_FOLDER/public_html/`

---

## Step 5: Run Setup Script

```bash
# SSH into server
ssh master@YOUR_SERVER_IP

# Navigate to project
cd /home/master/applications/YOUR_APP_FOLDER/public_html

# Make setup script executable
chmod +x deploy/setup_cloudways.sh

# Run the setup
bash deploy/setup_cloudways.sh
```

This script will:
- Install Python 3.11, Redis, Supervisor
- Create virtual environment
- Install all Python dependencies
- Configure Supervisor for Gunicorn + Celery
- Start all services

---

## Step 6: Configure Environment Variables

```bash
# Copy the production template
cp deploy/.env.production .env

# Edit with your actual values
nano .env
```

Fill in all your actual credentials. **Important:** Generate a strong Flask secret:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

---

## Step 7: Configure Nginx (Custom Vhost)

### Via Cloudways Panel (Easiest):

1. Go to **Application** → **Vhost Conf** (under Application Settings)
2. Scroll to the **custom Nginx config** section
3. Paste the contents of `deploy/nginx_cloudways.conf`
4. Update `YOUR_APP_FOLDER` with your actual folder name
5. Save

### Via SSH:

```bash
# Find your app's Nginx include directory
ls /etc/nginx/conf.d/

# Copy the config (update the path to match your app)
sudo cp deploy/nginx_cloudways.conf /etc/nginx/conf.d/server/custom_server.conf

# Edit and replace YOUR_APP_FOLDER with actual folder name
sudo nano /etc/nginx/conf.d/server/custom_server.conf

# Test and reload Nginx
sudo nginx -t
sudo systemctl reload nginx
```

---

## Step 8: Restart Everything

```bash
# Restart Gunicorn + Celery
sudo supervisorctl restart all

# Restart Nginx
sudo systemctl reload nginx

# Check status
sudo supervisorctl status
```

Expected output:
```
idcard_celery                    RUNNING   pid 12345, uptime 0:00:05
idcard_gunicorn                  RUNNING   pid 12346, uptime 0:00:05
```

---

## Step 9: Verify Deployment

```bash
# Health check
curl http://127.0.0.1:8000/health

# Test from outside
curl https://www.puratchithaai.org/health
```

---

## Common Commands (Post-Deployment)

| Task | Command |
|------|---------|
| **Restart app** | `sudo supervisorctl restart idcard_gunicorn` |
| **Restart Celery** | `sudo supervisorctl restart idcard_celery` |
| **Restart all** | `sudo supervisorctl restart all` |
| **View app logs** | `tail -f /home/master/applications/*/public_html/logs/gunicorn_out.log` |
| **View error logs** | `tail -f /home/master/applications/*/public_html/logs/gunicorn_err.log` |
| **View Celery logs** | `tail -f /home/master/applications/*/public_html/logs/celery_out.log` |
| **Check Redis** | `redis-cli ping` |
| **Check processes** | `sudo supervisorctl status` |
| **Quick redeploy** | `bash deploy/redeploy.sh` |

---

## Updating Code (Future Deployments)

```bash
# SSH into server
ssh master@YOUR_SERVER_IP
cd /home/master/applications/YOUR_APP_FOLDER/public_html

# Quick redeploy
bash deploy/redeploy.sh
```

Or manually:
```bash
git pull origin main
source venv/bin/activate
pip install -r requirements.txt
sudo supervisorctl restart all
```

---

## Troubleshooting

### App not running?
```bash
sudo supervisorctl status                     # Check process status
cat logs/gunicorn_err.log                      # Check error logs
source venv/bin/activate && python app.py      # Test manually
```

### 502 Bad Gateway?
```bash
# Gunicorn not running or wrong port
sudo supervisorctl restart idcard_gunicorn
# Check Nginx config
sudo nginx -t
```

### Redis connection errors?
```bash
redis-cli ping                                # Should return PONG
sudo systemctl status redis-server
sudo systemctl restart redis-server
```

### MySQL connection errors?
```bash
# Test MySQL connectivity from server
source venv/bin/activate
python3 -c "import pymysql; conn = pymysql.connect(host='174.138.49.116', port=3306, user='YOUR_USER', password='YOUR_PASS'); print('Connected!')"
```

### Celery tasks not processing?
```bash
sudo supervisorctl restart idcard_celery
cat logs/celery_err.log
```

---

## Cloudways Server Sizing Recommendations

| Traffic Level | Server Size | Workers | Celery Concurrency |
|---------------|-------------|---------|-------------------|
| Low (<1K/day) | 2 GB RAM | 3 workers | 2 |
| Medium (1K-10K/day) | 4 GB RAM | 5 workers | 4 |
| High (10K+/day) | 8 GB RAM | 8 workers | 6 |

---

## Security Checklist

- [x] SSL enabled via Let's Encrypt
- [x] `.env` file blocked from web access (Nginx config)
- [x] Python/log files blocked from web access
- [x] Redis bound to localhost only
- [x] Strong FLASK_SECRET generated
- [x] FLASK_ENV set to `production`
- [x] Admin credentials are strong and unique
