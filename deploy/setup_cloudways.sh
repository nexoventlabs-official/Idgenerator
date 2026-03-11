#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# Cloudways Deployment Setup Script
# Voter ID Card Generator - Flask App
# ═══════════════════════════════════════════════════════════════════
# Run this script after SSHing into your Cloudways server.
# Usage: bash setup_cloudways.sh
# ═══════════════════════════════════════════════════════════════════

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Cloudways Deployment Setup - Voter ID Card App   ${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"

# ── Variables (UPDATE THESE) ─────────────────────────────────────
APP_NAME="idcard"
APP_DIR="/home/master/applications/$(ls /home/master/applications/ | head -1)/public_html"
PYTHON_VERSION="3.11"
VENV_DIR="$APP_DIR/venv"

echo -e "${YELLOW}App directory: $APP_DIR${NC}"

# ── Step 1: Install System Dependencies ──────────────────────────
echo -e "\n${GREEN}[1/8] Installing system dependencies...${NC}"
sudo apt-get update
sudo apt-get install -y \
    python${PYTHON_VERSION} \
    python${PYTHON_VERSION}-venv \
    python${PYTHON_VERSION}-dev \
    python3-pip \
    gcc \
    g++ \
    libffi-dev \
    libssl-dev \
    libjpeg-dev \
    zlib1g-dev \
    libpng-dev \
    redis-server \
    supervisor \
    curl \
    git

# ── Step 2: Install Python 3.11 if not available ────────────────
echo -e "\n${GREEN}[2/8] Checking Python ${PYTHON_VERSION}...${NC}"
if ! command -v python${PYTHON_VERSION} &> /dev/null; then
    echo "Installing Python ${PYTHON_VERSION} from deadsnakes PPA..."
    sudo add-apt-repository ppa:deadsnakes/ppa -y
    sudo apt-get update
    sudo apt-get install -y python${PYTHON_VERSION} python${PYTHON_VERSION}-venv python${PYTHON_VERSION}-dev
fi
python${PYTHON_VERSION} --version

# ── Step 3: Setup Virtual Environment ────────────────────────────
echo -e "\n${GREEN}[3/8] Setting up virtual environment...${NC}"
cd "$APP_DIR"
python${PYTHON_VERSION} -m venv venv
source venv/bin/activate
pip install --upgrade pip setuptools wheel

# ── Step 4: Install Python Requirements ──────────────────────────
echo -e "\n${GREEN}[4/8] Installing Python requirements...${NC}"
pip install -r requirements.txt

# ── Step 5: Create Required Directories ──────────────────────────
echo -e "\n${GREEN}[5/8] Creating required directories...${NC}"
mkdir -p member_photos data uploads static templates logs

# ── Step 6: Start & Enable Redis ─────────────────────────────────
echo -e "\n${GREEN}[6/8] Configuring Redis...${NC}"
sudo systemctl start redis-server
sudo systemctl enable redis-server
redis-cli ping

# ── Step 7: Setup Supervisor for Gunicorn + Celery ───────────────
echo -e "\n${GREEN}[7/8] Configuring Supervisor...${NC}"

# Gunicorn config
sudo tee /etc/supervisor/conf.d/idcard_gunicorn.conf > /dev/null <<GUNICORN_EOF
[program:idcard_gunicorn]
command=${VENV_DIR}/bin/gunicorn app:app --bind 127.0.0.1:8000 --timeout 30 --workers 3 --threads 2 --access-logfile ${APP_DIR}/logs/gunicorn_access.log --error-logfile ${APP_DIR}/logs/gunicorn_error.log
directory=${APP_DIR}
user=master
autostart=true
autorestart=true
stopasgroup=true
killasgroup=true
stderr_logfile=${APP_DIR}/logs/gunicorn_err.log
stdout_logfile=${APP_DIR}/logs/gunicorn_out.log
environment=PATH="${VENV_DIR}/bin:%(ENV_PATH)s"
GUNICORN_EOF

# Celery config
sudo tee /etc/supervisor/conf.d/idcard_celery.conf > /dev/null <<CELERY_EOF
[program:idcard_celery]
command=${VENV_DIR}/bin/celery -A tasks worker --loglevel=info --concurrency=2
directory=${APP_DIR}
user=master
autostart=true
autorestart=true
stopasgroup=true
killasgroup=true
stderr_logfile=${APP_DIR}/logs/celery_err.log
stdout_logfile=${APP_DIR}/logs/celery_out.log
environment=PATH="${VENV_DIR}/bin:%(ENV_PATH)s"
CELERY_EOF

sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl start idcard_gunicorn
sudo supervisorctl start idcard_celery

# ── Step 8: Verify ───────────────────────────────────────────────
echo -e "\n${GREEN}[8/8] Verifying deployment...${NC}"
sleep 3

# Check Gunicorn
if curl -s http://127.0.0.1:8000/health > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Gunicorn is running${NC}"
else
    echo -e "${RED}✗ Gunicorn may not be running yet. Check logs.${NC}"
fi

# Check Redis
if redis-cli ping | grep -q "PONG"; then
    echo -e "${GREEN}✓ Redis is running${NC}"
else
    echo -e "${RED}✗ Redis is not running${NC}"
fi

# Check Celery
if sudo supervisorctl status idcard_celery | grep -q "RUNNING"; then
    echo -e "${GREEN}✓ Celery worker is running${NC}"
else
    echo -e "${YELLOW}⚠ Celery worker may still be starting...${NC}"
fi

echo -e "\n${GREEN}═══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Setup Complete!${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo -e ""
echo -e "Next steps:"
echo -e "  1. Copy your .env.production to ${APP_DIR}/.env"
echo -e "  2. Update Nginx vhost (see deploy/nginx_cloudways.conf)"
echo -e "  3. Restart: sudo supervisorctl restart all"
echo -e "  4. Check health: curl http://127.0.0.1:8000/health"
echo -e ""
