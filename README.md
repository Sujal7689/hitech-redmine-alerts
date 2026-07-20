# HiTech Redmine Discrepancy Alert System

Monitors new Installation tickets (Project 3, Tracker 11) every 5 minutes.
- **Critical issues** (payment/amount/lock) → instant email
- **Minor issues** (license, contact data) → 9:00 AM daily digest

---

## One-Time Setup (15 minutes)

### Step 1 — Create GitHub repo

1. Go to [github.com](https://github.com) and log in
2. Click **New repository** (top right, + icon)
3. Name it `hitech-redmine-alerts`
4. Set it to **Private**
5. Click **Create repository**

### Step 2 — Upload files

1. In your new repo, click **Add file → Upload files**
2. Upload `checker.py`, `requirements.txt`, and the `.github/workflows/redmine-alerts.yml` file
   - For the workflow file: create folders manually — click **Add file → Create new file**, type `.github/workflows/redmine-alerts.yml` as the filename, paste the content
3. Click **Commit changes**

### Step 3 — Add secrets

1. In your repo, go to **Settings → Secrets and variables → Actions**
2. Click **New repository secret** and add each of these:

| Secret Name | Value |
|---|---|
| `REDMINE_URL` | `http://3.7.179.127:82/redmine` |
| `REDMINE_KEY` | `fff4a2a98f942c806109e44d54710c57bf949617` |
| `GMAIL_USER` | `singhaniasujal7689@gmail.com` |
| `GMAIL_APP_PASS` | *(your 16-character Gmail App Password)* |
| `ALERT_TO` | `sujal@hitechnepal.com.np,subodh@hitechnepal.com.np` |

### Step 4 — Gmail App Password

1. Go to [myaccount.google.com](https://myaccount.google.com) logged into `singhaniasujal7689@gmail.com`
2. Search **"App Passwords"** in the search bar
3. App name: `HiTech Redmine Alerts` → click **Create**
4. Copy the 16-character password → paste as `GMAIL_APP_PASS` secret above

### Step 5 — Test it

1. In your repo, go to **Actions** tab
2. Click **Redmine Discrepancy Alerts** in the left panel
3. Click **Run workflow → Run workflow**
4. Watch the logs — you should see it connect to Redmine and process tickets

---

## What Gets Flagged

### 🔴 Critical (instant email)
- Payment Status blank or invalid
- Total Amount blank, zero, or has text (e.g. `40000+vat`)
- Received Amount has leading dot (`.162720`)
- Payment Status = Fully Paid but Received Amount is blank/zero
- Lock Issued = YES but Lock No empty
- Lock No filled but Lock Issued checkbox = NO
- VAT field has placeholder dots (`...`)

### 🟡 Minor (daily 9am digest)
- License Code empty
- License Issue Date empty
- Software Expiry Date blank
- Contact Person blank
- Email address in Phone field
- PAN number not 9 digits
- Bill Type = NA on a closed ticket
- Sales Executive blank

---

## Maintenance

- **To add a new rule**: edit `checker.py` in the `check_ticket()` function
- **To change recipients**: update the `ALERT_TO` secret
- **To change digest time**: edit the cron in the workflow file (`15 3 * * *` = 3:15 UTC = 9:00 AM Nepal)
- **Logs**: visible in the Actions tab of your GitHub repo
