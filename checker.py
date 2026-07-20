"""
HiTech Redmine Discrepancy Alert System
- Checks every new Installation ticket (Project 3, Tracker 11)
- Sends one email per ticket listing ALL issues found
- No digest — everything is instant
"""

import os
import json
import re
import smtplib
import requests
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ── Config ────────────────────────────────────────────────────────────────────
REDMINE_URL = "http://3.7.179.127:82/redmine"
REDMINE_KEY = "fff4a2a98f942c806109e44d54710c57bf949617"
PROJECT_ID  = 3
TRACKER_ID  = 11

SMTP_USER   = "alerts@hitechnepal.com.np"
SMTP_PASS   = os.environ.get("OUTLOOK_PASS", "")
ALERT_TO    = ["sujal@hitechnepal.com.np", "subodh@hitechnepal.com.np"]

STATE_FILE  = "state.json"

# ── Custom Field IDs ──────────────────────────────────────────────────────────
CF = {
    "company_name":     2,
    "pan":              90,
    "address":          3,
    "contact_person":   4,
    "phone":            5,
    "email_id":         6,
    "software_cat":     26,
    "bill_type":        94,
    "vat":              105,
    "total_amount":     106,
    "payment_received": 91,
    "payment_status":   81,
    "received_amount":  111,
    "lock_issued":      85,
    "lock_no":          86,
    "lock_batch":       95,
    "lock_date":        87,
    "software_expiry":  141,
    "license_code":     93,
    "license_date":     92,
    "sales_executive":  51,
    "amc_amount":       99,
    "asc_amount":       100,
    "server_type":      38,
    "cloud_url":        142,
    "company_group_id": 117,
    "ird_doc":          145,
}

VALID_PAYMENT_STATUSES = {
    "Fully Paid", "Credit", "Advance Paid", "Partially Paid"
}

CLOUD_CATEGORIES = {
    "Swastik Gold(CLOUD)", "Swastik Nepal(CLOUD)",
    "Swastik Web", "Swastik Web(Prod)", "Swastik Web(IRD)"
}

# ── Helpers ───────────────────────────────────────────────────────────────────
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"last_ticket_id": 0}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def cf_val(issue, cf_id):
    for cf in issue.get("custom_fields", []):
        if cf["id"] == cf_id:
            v = cf.get("value")
            if isinstance(v, list):
                return ", ".join(str(x) for x in v).strip()
            return (v or "").strip()
    return ""

BLANK_VALUES = {
    "", ".", "..", "...", "....", ".....",
    "n.a", "n,a", "na", "NA", "N/A", "n/a",
    "-", "null", "NULL", "none", "None", "0"
}

def is_blank(val):
    return not val or val in BLANK_VALUES

def is_numeric_clean(val):
    """True only if val is a plain number with no text, commas, symbols."""
    if not val:
        return False
    try:
        float(val)
        # reject anything with formatting or text
        if any(c in val for c in [",", "+", "%", "/", " "]):
            return False
        if any(c.isalpha() for c in val):
            return False
        return True
    except ValueError:
        return False

def has_leading_dot(val):
    """Catches values like .162720 which parse as 0."""
    return bool(
        val
        and val.startswith(".")
        and len(val) > 1
        and val[1:].replace(".", "").isdigit()
    )

def has_formula_text(val):
    """Catches values like '40000+vat', '35,000+13%', '50000+Vat'."""
    if not val:
        return False
    return any(c in val for c in ["+", "%"]) or (
        "," in val and any(c.isalpha() for c in val)
    )

def has_commas(val):
    """Catches '71,190.00', '33,458.00' etc."""
    return bool(val and "," in val and not any(c.isalpha() for c in val))

def is_cloud_product(issue):
    return cf_val(issue, CF["software_cat"]) in CLOUD_CATEGORIES

def ticket_status(issue):
    return issue.get("status", {}).get("name", "")

# ── Rules Engine ──────────────────────────────────────────────────────────────
def check_ticket(issue):
    """Returns list of (severity, field, description) tuples."""
    issues = []

    def flag(severity, field, desc):
        issues.append((severity, field, desc))

    pay_status  = cf_val(issue, CF["payment_status"])
    pay_flag    = cf_val(issue, CF["payment_received"])
    total       = cf_val(issue, CF["total_amount"])
    received    = cf_val(issue, CF["received_amount"])
    vat         = cf_val(issue, CF["vat"])
    lock_issued = cf_val(issue, CF["lock_issued"])
    lock_no     = cf_val(issue, CF["lock_no"])
    lock_batch  = cf_val(issue, CF["lock_batch"])
    lock_date   = cf_val(issue, CF["lock_date"])
    lic_code    = cf_val(issue, CF["license_code"])
    lic_date    = cf_val(issue, CF["license_date"])
    expiry      = cf_val(issue, CF["software_expiry"])
    contact     = cf_val(issue, CF["contact_person"])
    phone       = cf_val(issue, CF["phone"])
    pan         = cf_val(issue, CF["pan"])
    bill_type   = cf_val(issue, CF["bill_type"])
    sales_exec  = cf_val(issue, CF["sales_executive"])
    address     = cf_val(issue, CF["address"])
    email_id    = cf_val(issue, CF["email_id"])
    sw_cat      = cf_val(issue, CF["software_cat"])
    server_type = cf_val(issue, CF["server_type"])
    cloud_url   = cf_val(issue, CF["cloud_url"])
    company_gid = cf_val(issue, CF["company_group_id"])
    amc         = cf_val(issue, CF["amc_amount"])
    asc         = cf_val(issue, CF["asc_amount"])
    ird_doc     = cf_val(issue, CF["ird_doc"])
    status      = ticket_status(issue)
    is_cloud    = is_cloud_product(issue)
    is_closed   = status == "Closed"

    # ── PAYMENT STATUS ────────────────────────────────────────────────────────
    if is_blank(pay_status):
        flag("CRITICAL", "Payment Status", "Blank — must be set to Credit / Fully Paid / Advance Paid / Partially Paid")
    elif pay_status not in VALID_PAYMENT_STATUSES:
        flag("CRITICAL", "Payment Status", f"Invalid value: '{pay_status}'")

    # ── TOTAL AMOUNT ──────────────────────────────────────────────────────────
    if is_blank(total) or total == "0":
        flag("CRITICAL", "Total Amount", "Blank or zero")
    elif has_formula_text(total):
        flag("CRITICAL", "Total Amount", f"Contains formula text instead of a number: '{total}'")
    elif has_commas(total):
        flag("CRITICAL", "Total Amount", f"Has comma formatting — system may misread: '{total}' → remove commas")
    elif not is_numeric_clean(total):
        flag("CRITICAL", "Total Amount", f"Not a clean number: '{total}'")

    # ── RECEIVED AMOUNT ───────────────────────────────────────────────────────
    if not is_blank(received) and received != "0":
        if has_leading_dot(received):
            flag("CRITICAL", "Received Amount", f"Leading dot — system reads as 0: '{received}' → should be '{received.lstrip('.')}'")
        elif has_formula_text(received):
            flag("CRITICAL", "Received Amount", f"Contains formula text: '{received}'")
        elif has_commas(received):
            flag("CRITICAL", "Received Amount", f"Has comma formatting: '{received}' → remove commas")
        elif not is_numeric_clean(received):
            flag("CRITICAL", "Received Amount", f"Not a clean number: '{received}'")

    # ── PAYMENT FLAG vs STATUS MISMATCH ──────────────────────────────────────
    if pay_status == "Fully Paid":
        if is_blank(received) or received == "0":
            flag("CRITICAL", "Received Amount", "Payment Status = Fully Paid but Received Amount is blank or zero")
        if pay_flag != "1":
            flag("CRITICAL", "Payment Received Flag", "Payment Status = Fully Paid but Payment Received checkbox is not ticked")

    if pay_flag == "1" and pay_status == "Credit":
        flag("CRITICAL", "Payment Status", "Payment Received flag = YES but Status still shows Credit — update status")

    # ── RECEIVED > TOTAL (beyond TDS tolerance) ───────────────────────────────
    if is_numeric_clean(total) and is_numeric_clean(received):
        t = float(total)
        r = float(received)
        if t > 0 and r > t * 1.06:
            flag("CRITICAL", "Amount Mismatch", f"Received ({r:,.0f}) exceeds Total ({t:,.0f}) by more than 6% — verify amounts")

    # ── VAT FIELD ─────────────────────────────────────────────────────────────
    if not is_blank(vat):
        if has_leading_dot(vat):
            flag("CRITICAL", "VAT Amount", f"Leading dot: '{vat}' — remove the dot")
        elif vat in ["...", "....", "..", "....."]:
            flag("CRITICAL", "VAT Amount", f"Placeholder dots entered instead of amount: '{vat}'")
        elif has_commas(vat):
            flag("WARNING", "VAT Amount", f"Has comma formatting: '{vat}' → remove commas")

    # ── LOCK FIELDS ───────────────────────────────────────────────────────────
    if not is_cloud:
        # Physical lock expected for desktop products
        if lock_issued == "1" and is_blank(lock_no):
            flag("CRITICAL", "Lock No", "Lock Issued = YES but Lock No is empty")
        if lock_issued == "1" and is_blank(lock_batch):
            flag("WARNING", "Lock Batch No", "Lock Issued = YES but Batch No is empty")
        if lock_issued == "1" and is_blank(lock_date):
            flag("WARNING", "Lock Issued Date", "Lock Issued = YES but Lock Date not filled")
        if lock_issued == "0" and not is_blank(lock_no):
            flag("CRITICAL", "Lock Issued", "Lock No is filled but Lock Issued checkbox = NO — tick the checkbox")
        if not is_blank(lock_no) and not is_blank(lock_batch):
            # Detect reversed fields: lock serial in batch field and batch in lock field
            lock_no_looks_like_batch = lock_no.startswith("5524-") or lock_no.startswith("5523-")
            batch_looks_like_serial  = lock_batch.startswith("1003-") or lock_batch.startswith("2003-")
            if lock_no_looks_like_batch and batch_looks_like_serial:
                flag("CRITICAL", "Lock No / Batch No", "Fields appear REVERSED — Lock No contains batch number and Batch No contains serial number")
        if is_blank(lock_issued) and is_closed:
            flag("WARNING", "Lock Issued", "Ticket is closed but Lock Issued field is blank")
    else:
        # Cloud product — no physical lock needed
        if lock_issued == "1" and is_blank(lock_no):
            flag("WARNING", "Lock Issued", "Cloud product — Lock Issued = YES but no Lock No. Consider reverting to NO")

    # ── LICENSE FIELDS ────────────────────────────────────────────────────────
    if is_blank(lic_code) and not is_blank(lic_date):
        flag("WARNING", "License Code", "License Date is filled but License Code is empty")
    if not is_blank(lic_code) and is_blank(lic_date):
        flag("WARNING", "License Issue Date", "License Code is filled but License Date is empty")
    if is_closed and is_blank(lic_code):
        flag("WARNING", "License Code", "Ticket is closed but License Code is still empty")
    if is_closed and is_blank(lic_date):
        flag("WARNING", "License Issue Date", "Ticket is closed but License Issue Date is still empty")

    # ── SOFTWARE EXPIRY ───────────────────────────────────────────────────────
    if is_closed and is_blank(expiry):
        flag("WARNING", "Software Expiry Date", "Ticket is closed but Software Expiry Date is blank")

    # ── BILL TYPE ─────────────────────────────────────────────────────────────
    if is_blank(bill_type):
        flag("WARNING", "Bill Type", "Bill Type is blank")
    elif bill_type in ["NA", "N/A", "na"] and is_closed:
        flag("WARNING", "Bill Type", "Bill Type is NA on a closed ticket — update to actual bill type issued")

    # ── CONTACT DATA ──────────────────────────────────────────────────────────
    if is_blank(contact):
        flag("WARNING", "Contact Person", "Blank — fill in client contact name")
    if phone and "@" in phone:
        flag("WARNING", "Phone / Mobile", f"Email address entered in Phone field: '{phone}' — move to Email field")
    if is_blank(phone) and is_blank(email_id):
        flag("WARNING", "Contact Details", "Both Phone and Email are blank")
    if is_blank(address):
        flag("WARNING", "Client Address", "Blank")
    if pan and not is_blank(pan) and pan.lower() not in ["n/a", "n.a", "na"]:
        digits = re.sub(r"\D", "", pan)
        if len(digits) > 0 and len(digits) != 9:
            flag("WARNING", "PAN Number", f"{len(digits)} digits found — Nepal PAN must be 9 digits: '{pan}'")

    # ── SALES EXECUTIVE ───────────────────────────────────────────────────────
    if is_blank(sales_exec):
        flag("WARNING", "Sales Executive", "Blank — assign a sales executive")

    # ── CLOUD-SPECIFIC ────────────────────────────────────────────────────────
    if is_cloud:
        if is_blank(company_gid):
            flag("WARNING", "Company Group ID", "Cloud product but Company Group ID is blank")
        if is_closed and is_blank(cloud_url):
            flag("WARNING", "Cloud/Web URL", "Cloud product — ticket closed but Cloud URL not filled")
        if is_blank(asc) or asc == "0":
            flag("WARNING", "ASC Amount", "Cloud product but Annual Subscription (ASC) amount is blank")

    # ── DESKTOP-SPECIFIC ──────────────────────────────────────────────────────
    if not is_cloud:
        if is_blank(amc) or amc == "0":
            flag("WARNING", "AMC Amount", "Desktop product but AMC amount is blank")

    return issues

# ── Email ─────────────────────────────────────────────────────────────────────
def ticket_url(tid):
    return f"{REDMINE_URL}/issues/{tid}"

def send_email(subject, html_body):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SMTP_USER
    msg["To"]      = ", ".join(ALERT_TO)
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP("smtp.office365.com", 587) as smtp:
        smtp.starttls()
        smtp.login(SMTP_USER, SMTP_PASS)
        smtp.sendmail(SMTP_USER, ALERT_TO, msg.as_string())

def build_email_html(issue, all_issues):
    tid     = issue["id"]
    company = cf_val(issue, CF["company_name"]) or issue.get("subject", f"Ticket #{tid}")
    author  = issue.get("author", {}).get("name", "Unknown")
    created = issue.get("created_on", "")[:10]
    status  = ticket_status(issue)
    sw_cat  = cf_val(issue, CF["software_cat"])
    url     = ticket_url(tid)

    critical_items = [(f, d) for s, f, d in all_issues if s == "CRITICAL"]
    warning_items  = [(f, d) for s, f, d in all_issues if s == "WARNING"]

    def rows(items, color, bg, icon):
        if not items:
            return ""
        header = f"""<tr>
          <th colspan="2" style="padding:8px 14px;background:{bg};color:{color};
              font-size:11px;text-transform:uppercase;letter-spacing:.5px;text-align:left">
            {icon} {"Critical Issues" if icon == "🔴" else "Warnings"}
          </th>
        </tr>"""
        body = "".join(f"""<tr>
          <td style="padding:7px 14px;border-bottom:1px solid #f3f4f6;
              font-weight:600;color:#374151;width:160px;vertical-align:top">{f}</td>
          <td style="padding:7px 14px;border-bottom:1px solid #f3f4f6;
              color:#6b7280;vertical-align:top">{d}</td>
        </tr>""" for f, d in items)
        return header + body

    critical_rows = rows(critical_items, "#991b1b", "#fee2e2", "🔴")
    warning_rows  = rows(warning_items,  "#92400e", "#fef3c7", "🟡")

    header_color = "#dc2626" if critical_items else "#d97706"
    title        = "🚨 Critical Issues Found" if critical_items else "⚠️ Data Issues Found"
    count_line   = f"{len(critical_items)} critical, {len(warning_items)} warning(s)" if critical_items else f"{len(warning_items)} warning(s)"

    return f"""
<div style="font-family:Segoe UI,Arial,sans-serif;max-width:640px;margin:0 auto">
  <div style="background:{header_color};padding:16px 24px;border-radius:8px 8px 0 0">
    <h2 style="color:#fff;margin:0;font-size:16px">{title} — New Ticket #{tid}</h2>
    <p style="color:rgba(255,255,255,.8);margin:4px 0 0;font-size:13px">{count_line}</p>
  </div>
  <div style="border:1px solid #e5e7eb;border-top:none;background:#fff;
      padding:18px 24px;border-radius:0 0 8px 8px">
    <table style="width:100%;border-collapse:collapse;margin-bottom:16px;
        font-size:13px;background:#f9fafb;border-radius:6px">
      <tr>
        <td style="padding:6px 14px;color:#6b7280;width:130px">Ticket</td>
        <td style="padding:6px 14px"><a href="{url}" style="color:#2563eb;font-weight:600">#{tid}</a></td>
      </tr>
      <tr style="background:#f3f4f6">
        <td style="padding:6px 14px;color:#6b7280">Company</td>
        <td style="padding:6px 14px;font-weight:600">{company}</td>
      </tr>
      <tr>
        <td style="padding:6px 14px;color:#6b7280">Product</td>
        <td style="padding:6px 14px">{sw_cat}</td>
      </tr>
      <tr style="background:#f3f4f6">
        <td style="padding:6px 14px;color:#6b7280">Status</td>
        <td style="padding:6px 14px">{status}</td>
      </tr>
      <tr>
        <td style="padding:6px 14px;color:#6b7280">Raised by</td>
        <td style="padding:6px 14px">{author} on {created}</td>
      </tr>
    </table>
    <table style="width:100%;border-collapse:collapse;font-size:13px">
      {critical_rows}
      {warning_rows}
    </table>
    <div style="margin-top:16px">
      <a href="{url}" style="display:inline-block;background:{header_color};color:#fff;
          padding:9px 20px;border-radius:6px;text-decoration:none;font-size:13px;font-weight:600">
        Open Ticket in Redmine →
      </a>
    </div>
  </div>
  <p style="font-size:11px;color:#9ca3af;margin-top:8px;text-align:center">
    HiTech Redmine Alert System — auto-generated
  </p>
</div>"""

# ── Fetch ─────────────────────────────────────────────────────────────────────
def fetch_new_tickets(since_id):
    headers = {"X-Redmine-API-Key": REDMINE_KEY}
    tickets = []
    offset  = 0
    while True:
        resp = requests.get(
            f"{REDMINE_URL}/issues.json",
            headers=headers,
            params={
                "project_id": PROJECT_ID,
                "tracker_id": TRACKER_ID,
                "status_id":  "*",
                "include":    "custom_fields",
                "limit":      100,
                "offset":     offset,
                "sort":       "id:asc",
            },
            timeout=30
        )
        resp.raise_for_status()
        data  = resp.json()
        batch = data.get("issues", [])
        if not batch:
            break
        for t in batch:
            if t["id"] > since_id:
                tickets.append(t)
        offset += len(batch)
        if offset >= data.get("total_count", 0):
            break
    return tickets

# ── Main ──────────────────────────────────────────────────────────────────────
def run():
    state    = load_state()
    since_id = state.get("last_ticket_id", 0)
    max_id   = since_id

    print(f"Checking for tickets newer than ID {since_id}...")
    new_tickets = fetch_new_tickets(since_id)
    print(f"Found {len(new_tickets)} new ticket(s)")

    for issue in new_tickets:
        tid     = issue["id"]
        max_id  = max(max_id, tid)
        company = cf_val(issue, CF["company_name"]) or issue.get("subject", f"#{tid}")

        found = check_ticket(issue)

        if not found:
            print(f"  #{tid} {company}: ✓ Clean")
        else:
            criticals = [x for x in found if x[0] == "CRITICAL"]
            warnings  = [x for x in found if x[0] == "WARNING"]
            print(f"  #{tid} {company}: {len(criticals)} critical, {len(warnings)} warning(s) — sending email")
            prefix = "🚨" if criticals else "⚠️"
            subj   = f"{prefix} Redmine #{tid} — {len(found)} issue(s) found — {company}"
            html   = build_email_html(issue, found)
            send_email(subj, html)

    state["last_ticket_id"] = max_id
    save_state(state)
    print("Done.")

if __name__ == "__main__":
    run()
