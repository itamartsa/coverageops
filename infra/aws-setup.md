# 🚀 מדריך פריסת CoverageOps ל-AWS EC2

## ארכיטקטורה

```
Internet
   │
   ▼
EC2 Instance (Ubuntu 22.04)
   └── Docker Compose
       ├── nginx:443/80  ← כניסה ראשית (Reverse Proxy + SSL)
       ├── frontend:3000 ← React App
       ├── backend:8000  ← FastAPI
       └── db:5432       ← PostgreSQL + PostGIS
```

---

## שלב 1 – יצירת EC2 Instance ב-AWS

### 1.1 כנס ל-AWS Console
1. עבור ל-**EC2** → **Launch Instance**

### 1.2 הגדרות ה-Instance
| הגדרה | ערך מומלץ |
|---|---|
| **Name** | `coverageops-prod` |
| **AMI** | Ubuntu Server 22.04 LTS (64-bit) |
| **Instance Type** | `t3.medium` (2 vCPU, 4GB RAM) – מינימום. `t3.large` עדיף |
| **Key pair** | צור חדש או בחר קיים → שמור את `.pem` |
| **Storage** | 30 GB gp3 (לפחות – יותר אם יש SRTM data גדול) |

### 1.3 Security Group – פתח פורטים
| Port | Protocol | Source | Purpose |
|---|---|---|---|
| 22 | TCP | My IP | SSH |
| 80 | TCP | 0.0.0.0/0 | HTTP → HTTPS redirect |
| 443 | TCP | 0.0.0.0/0 | HTTPS |

> ⚠️ **אל תפתח פורט 8000 ו-5432** – הם יהיו פנימיים בתוך Docker network בלבד.

### 1.4 Elastic IP (חשוב!)
- לאחר שהשרת עולה: **EC2** → **Elastic IPs** → **Allocate** → **Associate** ל-instance שלך
- זה מונע שינוי IP בכל restart

---

## שלב 2 – הגדרת הדומיין

### אם יש לך דומיין:
1. כנס לספק הדומיין שלך (GoDaddy, Cloudflare, Route53...)
2. הוסף **A Record**: `coverageops.yourdomain.com` → כתובת ה-Elastic IP שלך

### אם אין דומיין (עבודה עם IP):
> ניתן לעבוד עם IP ישיר ללא SSL, אבל לא מומלץ בסביבת ייצור.
> בשלב כזה, דלג על שלב ה-Certbot (SSL).

---

## שלב 3 – הכנת השרת

### 3.1 התחבר ל-EC2 דרך SSH
```bash
# מ-Windows PowerShell / Terminal
ssh -i ~/.ssh/coverageops-key.pem ubuntu@<ELASTIC_IP>
```

### 3.2 התקן Docker + Docker Compose
```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker ubuntu

# Install Docker Compose plugin
sudo apt install docker-compose-plugin -y

# Verify
docker --version
docker compose version
```

> **חשוב:** צא מה-SSH ותתחבר מחדש (`exit` → `ssh ...`) כדי שה-group יחול.

### 3.3 התקן Git
```bash
sudo apt install git -y
```

---

## שלב 4 – העלאת קוד האפליקציה

### אפשרות A – דרך Git (מומלץ)
```bash
git clone https://github.com/your-username/coverageops.git
cd coverageops/coverageops
```

### אפשרות B – דרך SCP (העתקה ידנית)
```bash
# מהמחשב שלך (Windows PowerShell):
scp -i ~/.ssh/coverageops-key.pem -r C:\Users\user\Desktop\CoverageOps\coverageops ubuntu@<ELASTIC_IP>:~/coverageops
```

---

## שלב 5 – הגדרת קובץ הסביבה

```bash
cd ~/coverageops
cp .env.prod.example .env.prod
nano .env.prod   # ערוך את הערכים
```

### מלא את הערכים:
```env
DOMAIN=coverageops.yourdomain.com       # הדומיין שלך
DB_PASSWORD=SomeVeryStrongPassword123!  # סיסמת DB
SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
```

---

## שלב 6 – הוצאת SSL Certificate (Let's Encrypt)

### 6.1 הפעל nginx עם HTTP בלבד לפני SSL
```bash
# הפעל nginx ב-HTTP בלבד (כדי שCertbot יוכל לאמת את הדומיין)
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d nginx db backend frontend
```

### 6.2 בקש Certificate
```bash
docker run --rm \
  -v certbot_www:/var/www/certbot \
  -v certbot_certs:/etc/letsencrypt \
  certbot/certbot certonly \
  --webroot -w /var/www/certbot \
  -d coverageops.yourdomain.com \
  --email your@email.com \
  --agree-tos \
  --no-eff-email
```

> ✅ אם הצליח – Certificate תקף ל-90 יום, יתחדש אוטומטית על ידי קונטיינר ה-certbot

---

## שלב 7 – הפעלה מלאה

```bash
chmod +x deploy.sh
./deploy.sh
```

### בדיקה שהכל רץ:
```bash
docker compose -f docker-compose.prod.yml ps
```

צריך לראות כל הקונטיינרים ב-`Up` או `running`:
```
NAME                   STATUS
coverageops_db         Up (healthy)
coverageops_api        Up
coverageops_ui         Up
coverageops_nginx      Up
coverageops_certbot    Up
```

---

## שלב 8 – גישה לאפליקציה

- 🌐 **Main App**: `https://coverageops.yourdomain.com`
- 📚 **API Docs**: `https://coverageops.yourdomain.com/api/docs`
- 👤 **Admin Login**: שם הגישה הראשוני מוגדר בקובץ `app/scripts/seed.py`

---

## פקודות שימושיות

```bash
# צפייה בלוגים בזמן אמת
docker compose -f docker-compose.prod.yml logs -f

# לוגים של שירות ספציפי
docker compose -f docker-compose.prod.yml logs -f backend

# כניסה ל-shell של הבאקנד
docker exec -it coverageops_api bash

# כניסה ל-PostgreSQL
docker exec -it coverageops_db psql -U coverageops coverageops

# עצור הכל
docker compose -f docker-compose.prod.yml down

# עדכון לגרסה חדשה
./deploy.sh
```

---

## טיפים לאחר הפריסה

### 1. גיבוי Database
```bash
# הרץ כ-cron job יומי
docker exec coverageops_db pg_dump -U coverageops coverageops | gzip > /home/ubuntu/backups/db-$(date +%Y%m%d).sql.gz
```

### 2. ניטור (אופציונלי)
- הפעל **CloudWatch** ב-AWS לניטור CPU/Memory
- שקול **AWS CloudFront** לפני ה-nginx לשיפור מהירות

### 3. SRTM Data
אם יש לך SRTM data מקומי:
```bash
# העתק מהמחשב לשרת
scp -i ~/.ssh/coverageops-key.pem -r /path/to/srtm/*.hgt ubuntu@<IP>:~/coverageops/data/srtm/
```

### 4. עדכון אוטומטי של SSL (כבר מוגדר)
ה-certbot container כבר מריץ `certbot renew` כל 12 שעות אוטומטית.

---

## עלויות AWS מוערכות

| שירות | עלות חודשית |
|---|---|
| EC2 t3.medium | ~$30 |
| EBS Storage 30GB | ~$3 |
| Elastic IP (בשימוש) | חינם |
| Data Transfer (10GB) | ~$1 |
| **סה"כ** | **~$34/חודש** |

> 💡 לחיסכון: השתמש ב-**Reserved Instance** (1 שנה) לחיסכון של ~40%.
