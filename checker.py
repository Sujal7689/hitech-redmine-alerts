"""
HiTech Redmine Discrepancy Alert System
Checks new Installation tickets (Project 3, Tracker 11) for data quality issues.
Critical issues → instant email
Minor issues → queued for daily 9am digest
"""

import os
import json
import re
import smtplib
import requests
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ── Config ────────────────────────────────────────────────────────────────────
REDMINE_URL    = os.environ["http://3.7.179.127:82/redmine/"].rstrip("/")
REDMINE_KEY    = os.environ["fff4a2a98f942c806109e44d54710c57bf949617"]
PROJECT_ID     = 3
TRACKER_ID     = 11

GMAIL_USER     = os.environ["singhaniasujal7689@gmail.com"]
GMAIL_PASS     = os.environ["ekqb jmrq rrdh czwf"]
ALERT_TO       = [e.strip() for e in os.environ["sujal@hitechnepal.com.np, subodh@hitechnepal.com.np"].split(",")]

STATE_FILE     = "state.json"   # tracks last-seen ticket ID + minor queue
MODE           = os.environ.get("MODE", "check")   # "check" | "digest"

# ── Custom Field IDs ──────────────────────────────────────────────────────────
CF = {
    "payment_status":   81,
    "payment_received": 91,
    "total_amount":     106,
    "received_amount":  111,
    "lock_issued":      85,
    "lock_no":          86,
    "lock_batch":       95,
    "license_code":     93,
    "license_date":     92,
    "software_expiry":  141,
    "contact_person":   4,
    "phone":            5,
    "pan":              90,
    "bill_type":        94,
    "vat":              105,
    "company_name":     2,
    "sales_executive":  51,
}

VALID_PAYMENT_STATUSES = {
    "Fully Paid", "Credit", "Advance Paid", "Partially Paid"
}

# ── Helpers ───────────────────────────────────────────────────────────────────
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"last_ticket_id": 0, "minor_queue": []}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def cf_val(issue, cf_id):
    """Extract a custom field value by ID."""
    for cf in issue.get("custom_fields", []):
        if cf["id"] == cf_id:
            return (cf.get("value") or "").strip()
    return ""

def is_blank(val):
    return not val or val in [".", "..", "...", "....", ".....", "N/A", "n.a", "n,a", "na", "NA", "-", "null"]

def is_numeric(val):
    """Returns True if val is a clean number (no commas, dots as separator, text)."""
    try:
        float(val.replace(",", ""))
        # But reject if it has comma thousands separators or formula text
        if "," in val or "+" in val or "%" in val or "/" in val or any(c.isalpha() for c in val):
            return False
        return True
    except (ValueError, AttributeError):
        return False

def has_leading_dot(val):
    return bool(val and val.startswith(".") and len(val) > 1 and val[1:].replace(".", "").isdigit())

def fetch_new_tickets(since_id):
    """Fetch all Installation tickets newer than since_id."""
    headers = {"X-Redmine-API-Key": REDMINE_KEY}
    tickets = []
    offset = 0
    while True:
        resp = requests.get(
            f"{REDMINE_URL}/issues.json",
            headers=headers,
            params={
                "project_id": PROJECT_ID,
                "tracker_id": TRACKER_ID,
                "status_id": "*",
                "include": "custom_fields",
                "limit": 100,
                "offset": offset,
                "sort": "id:desc",
            },
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("issues", [])
        if not batch:
            break
        for t in batch:
            if t["id"] <= since_id:
                return tickets   # reached already-seen territory
            tickets.append(t)
        offset += len(batch)
        if offset >= data.get("total_count", 0):
            break
    return tickets

# ── Rules Engine ──────────────────────────────────────────────────────────────
def check_ticket(issue):
    """
    Returns (critical_issues, minor_issues) — both lists of strings.
    """
    critical = []
    minor = []

    tid     = issue["id"]
    subject = issue.get("subject", "")
    company = cf_val(issue, CF["company_name"]) or subject

    pay_status  = cf_val(issue, CF["payment_status"])
    pay_flag    = cf_val(issue, CF["payment_received"])
    total       = cf_val(issue, CF["total_amount"])
    received    = cf_val(issue, CF["received_amount"])
    lock_issued = cf_val(issue, CF["lock_issued"])
    lock_no     = cf_val(issue, CF["lock_no"])
    lock_batch  = cf_val(issue, CF["lock_batch"])
    lic_code    = cf_val(issue, CF["license_code"])
    lic_date    = cf_val(issue, CF["license_date"])
    expiry      = cf_val(issue, CF["software_expiry"])
    contact     = cf_val(issue, CF["contact_person"])
    phone       = cf_val(issue, CF["phone"])
    pan         = cf_val(issue, CF["pan"])
    bill_type   = cf_val(issue, CF["bill_type"])
    vat         = cf_val(issue, CF["vat"])
    sales_exec  = cf_val(issue, CF["sales_executive"])

    # ── CRITICAL: Payment Status ──────────────────────────────────────────────
    if is_blank(pay_status):
        critical.append("Payment Status is blank")
    elif pay_status not in VALID_PAYMENT_STATUSES:
        critical.append(f"Payment Status has invalid value: '{pay_status}'")

    # ── CRITICAL: Total Amount ────────────────────────────────────────────────
    if is_blank(total):
        critical.append("Total Amount is blank")
    elif not is_numeric(total):
        critical.append(f"Total Amount is not a clean number: '{total}'")
    elif float(total) == 0:
        critical.append("Total Amount is zero")

    # ── CRITICAL: Received Amount format ─────────────────────────────────────
    if not is_blank(received):
        if has_leading_dot(received):
            critical.append(f"Received Amount has leading dot (system reads as 0): '{received}'")
        elif not is_numeric(received):
            critical.append(f"Received Amount is not a clean number: '{received}'")

    # ── CRITICAL: Fully Paid but Received = blank/zero ────────────────────────
    if pay_status == "Fully Paid":
        if is_blank(received) or received == "0":
            critical.append("Payment Status = Fully Paid but Received Amount is blank or zero")
        if pay_flag != "1":
            critical.append("Payment Status = Fully Paid but Payment Received flag is not set")

    # ── CRITICAL: Received > Total (more than 5% gap, not TDS) ───────────────
    if is_numeric(total) and is_numeric(received):
        t = float(total)
        r = float(received)
        if t > 0 and r > t * 1.05:
            critical.append(f"Received Amount ({r:,.0f}) exceeds Total Amount ({t:,.0f}) by more than 5%")

    # ── CRITICAL: Lock checkbox mismatch ─────────────────────────────────────
    if lock_issued == "1" and is_blank(lock_no):
        critical.append("Lock Issued = YES but Lock No is empty")
    if lock_issued == "0" and not is_blank(lock_no) and not is_blank(lock_batch):
        critical.append("Lock No is filled but Lock Issued checkbox = NO")

    # ── CRITICAL: VAT amount format ───────────────────────────────────────────
    if not is_blank(vat):
        if has_leading_dot(vat):
            critical.append(f"VAT Amount has leading dot: '{vat}'")
        elif vat in ["...", "....", ".."] :
            critical.append(f"VAT Amount is placeholder dots: '{vat}'")

    # ── MINOR: License fields ─────────────────────────────────────────────────
    if is_blank(lic_code):
        minor.append("License Code is empty")
    if is_blank(lic_date):
        minor.append("License Issue Date is empty")
    if is_blank(expiry):
        minor.append("Software Expiry Date is blank")

    # ── MINOR: Contact data ───────────────────────────────────────────────────
    if is_blank(contact):
        minor.append("Contact Person is blank")
    if phone and "@" in phone:
        minor.append(f"Email address entered in Phone field: '{phone}'")
    if pan and not is_blank(pan) and pan not in ["N/A", "n.a", "n/a"]:
        digits = re.sub(r"\D", "", pan)
        if len(digits) > 0 and len(digits) != 9:
            minor.append(f"PAN number has {len(digits)} digits (Nepal PAN should be 9): '{pan}'")

    # ── MINOR: Bill Type ──────────────────────────────────────────────────────
    if bill_type in ["NA", "N/A", "na", ""] and issue.get("status", {}).get("name") == "Closed":
        minor.append("Bill Type is NA/blank on a closed ticket")

    # ── MINOR: Sales Executive ────────────────────────────────────────────────
    if is_blank(sales_exec):
        minor.append("Sales Executive is blank")

    return critical, minor

# ── Email ─────────────────────────────────────────────────────────────────────
def ticket_url(tid):
    return f"{REDMINE_URL}/issues/{tid}"

def send_email(subject, html_body):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = ", ".join(ALERT_TO)
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(GMAIL_USER, GMAIL_PASS)
        smtp.sendmail(GMAIL_USER, ALERT_TO, msg.as_string())

def critical_email_html(issue, issues_list):
    tid     = issue["id"]
    company = cf_val(issue, CF["company_name"]) or issue.get("subject", f"Ticket #{tid}")
    author  = issue.get("author", {}).get("name", "Unknown")
    created = issue.get("created_on", "")[:10]
    url     = ticket_url(tid)
    rows    = "".join(f"<tr><td style='padding:8px 12px;border-bottom:1px solid #fee2e2;color:#7f1d1d'>⚠ {i}</td></tr>" for i in issues_list)
    return f"""
<div style="font-family:Segoe UI,sans-serif;max-width:600px;margin:0 auto">
  <div style="background:#dc2626;padding:16px 24px;border-radius:8px 8px 0 0">
    <h2 style="color:#fff;margin:0;font-size:16px">🚨 Critical Data Issue — New Ticket</h2>
  </div>
  <div style="border:1px solid #fca5a5;border-top:none;padding:20px 24px;background:#fff;border-radius:0 0 8px 8px">
    <p style="margin:0 0 4px"><strong>Ticket:</strong> <a href="{url}" style="color:#2563eb">#{tid}</a></p>
    <p style="margin:0 0 4px"><strong>Company:</strong> {company}</p>
    <p style="margin:0 0 16px"><strong>Raised by:</strong> {author} on {created}</p>
    <table style="width:100%;border-collapse:collapse;background:#fff5f5;border-radius:6px;overflow:hidden">
      <tr><th style="padding:8px 12px;text-align:left;background:#fee2e2;color:#991b1b;font-size:12px;text-transform:uppercase">Issues Found</th></tr>
      {rows}
    </table>
    <p style="margin:16px 0 0;font-size:13px;color:#6b7280">Please correct these fields in Redmine immediately before the ticket progresses.</p>
    <a href="{url}" style="display:inline-block;margin-top:12px;background:#dc2626;color:#fff;padding:8px 18px;border-radius:6px;text-decoration:none;font-size:13px">Open Ticket →</a>
  </div>
  <p style="font-size:11px;color:#9ca3af;margin-top:8px;text-align:center">HiTech Redmine Alert System</p>
</div>"""

def digest_email_html(items):
    """items = list of (ticket_id, company, author, created, minor_issues_list)"""
    today = datetime.now().strftime("%d %b %Y")
    sections = ""
    for tid, company, author, created, issues_list in items:
        url  = ticket_url(tid)
        rows = "".join(f"<tr><td style='padding:6px 12px;border-bottom:1px solid #fef9c3;color:#78350f;font-size:13px'>• {i}</td></tr>" for i in issues_list)
        sections += f"""
        <div style="margin-bottom:16px;border:1px solid #fde68a;border-radius:6px;overflow:hidden">
          <div style="background:#fef3c7;padding:8px 12px;display:flex;justify-content:space-between">
            <span><a href="{url}" style="color:#92400e;font-weight:700;text-decoration:none">#{tid}</a> — {company}</span>
            <span style="font-size:12px;color:#92400e">{author} · {created}</span>
          </div>
          <table style="width:100%;border-collapse:collapse">{rows}</table>
        </div>"""
    return f"""
<div style="font-family:Segoe UI,sans-serif;max-width:600px;margin:0 auto">
  <div style="background:#d97706;padding:16px 24px;border-radius:8px 8px 0 0">
    <h2 style="color:#fff;margin:0;font-size:16px">📋 Daily Minor Issues Digest — {today}</h2>
  </div>
  <div style="border:1px solid #fde68a;border-top:none;padding:20px 24px;background:#fff;border-radius:0 0 8px 8px">
    <p style="margin:0 0 16px;font-size:13px;color:#6b7280">{len(items)} ticket(s) with minor data issues raised in the last 24 hours:</p>
    {sections}
    <p style="font-size:12px;color:#9ca3af;margin-top:8px">These are non-critical — fix when possible to keep records clean.</p>
  </div>
  <p style="font-size:11px;color:#9ca3af;margin-top:8px;text-align:center">HiTech Redmine Alert System</p>
</div>"""

# ── Main ──────────────────────────────────────────────────────────────────────
def run_check():
    state = load_state()
    since_id = state.get("last_ticket_id", 0)
    minor_queue = state.get("minor_queue", [])

    print(f"Fetching tickets newer than ID {since_id}...")
    new_tickets = fetch_new_tickets(since_id)
    print(f"Found {len(new_tickets)} new ticket(s)")

    max_id = since_id
    for issue in new_tickets:
        tid = issue["id"]
        max_id = max(max_id, tid)
        company = cf_val(issue, CF["company_name"]) or issue.get("subject", f"Ticket #{tid}")
        author  = issue.get("author", {}).get("name", "Unknown")
        created = issue.get("created_on", "")[:10]

        critical, minor = check_ticket(issue)

        if critical:
            print(f"  #{tid} {company}: {len(critical)} CRITICAL issue(s) — sending email")
            html = critical_email_html(issue, critical)
            send_email(f"🚨 HiTech Redmine Alert — #{tid} {company}", html)

        if minor:
            print(f"  #{tid} {company}: {len(minor)} minor issue(s) — queuing for digest")
            minor_queue.append({
                "id": tid,
                "company": company,
                "author": author,
                "created": created,
                "issues": minor,
            })

        if not critical and not minor:
            print(f"  #{tid} {company}: ✓ Clean")

    state["last_ticket_id"] = max_id
    state["minor_queue"] = minor_queue
    save_state(state)
    print("Done.")

def run_digest():
    state = load_state()
    queue = state.get("minor_queue", [])
    if not queue:
        print("No minor issues queued — skipping digest.")
        return

    print(f"Sending digest for {len(queue)} ticket(s)...")
    items = [(q["id"], q["company"], q["author"], q["created"], q["issues"]) for q in queue]
    html = digest_email_html(items)
    send_email(f"📋 HiTech Daily Digest — {len(items)} ticket(s) with minor issues", html)
    state["minor_queue"] = []
    save_state(state)
    print("Digest sent and queue cleared.")

if __name__ == "__main__":
    if MODE == "digest":
        run_digest()
    else:
        run_check()
