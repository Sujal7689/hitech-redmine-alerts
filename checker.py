"""
HiTech Redmine Discrepancy Alert System v5
- Triggered on every ticket create/update via Redmine webhook
- Checks the specific ticket that was created/updated
- Sends one email per ticket listing ALL issues found
"""

import os
import json
import re
import requests

# ── Config ────────────────────────────────────────────────────────────────────
REDMINE_URL = "http://3.7.179.127:82/redmine"
REDMINE_KEY = "fff4a2a98f942c806109e44d54710c57bf949617"
PROJECT_ID  = 3
TRACKER_ID  = 11

TENANT_ID     = "ee2c1707-fe2f-449b-bf11-679c0e05b4af"
CLIENT_ID     = "a9511178-6add-4011-b94d-738d2c74ffbe"
CLIENT_SECRET = "6Jj8Q~HXhYq0F8xnTRB4pBEJ-.uA~YArjQzCBchH"
SENDER_EMAIL  = "alerts@hitechnepal.com.np"
ALERT_TO      = ["sujal@hitechnepal.com.np", "subodh@hitechnepal.com.np"]

# ── Custom Field IDs ──────────────────────────────────────────────────────────
CF = {
    "company_name":      2,
    "address":           3,
    "contact_person":    4,
    "phone":             5,
    "email_id":          6,
    "software_cat":      26,
    "bill_type":         94,
    "vat":               105,
    "total_amount":      106,
    "payment_received":  91,
    "payment_status":    81,
    "received_amount":   111,
    "lock_issued":       85,
    "lock_no":           86,
    "lock_batch":        95,
    "lock_date":         87,
    "software_expiry":   141,
    "license_code":      93,
    "license_date":      92,
    "sales_executive":   51,
    "amc_amount":        99,
    "asc_amount":        100,
    "cloud_server_type": 123,
    "cloud_url":         142,
    "company_group_id":  117,
    "pan":               90,
}

VALID_PAYMENT_STATUSES = {
    "Fully Paid", "Credit", "Advance Paid", "Partially Paid"
}

WEB_CATEGORIES = {
    "Swastik Web", "Swastik Web(Prod)", "Swastik Web(IRD)"
}

CLOUD_CATEGORIES = {
    "Swastik Gold(CLOUD)", "Swastik Nepal(CLOUD)"
}

ALL_CLOUD_CATEGORIES = WEB_CATEGORIES | CLOUD_CATEGORIES

# ── Helpers ───────────────────────────────────────────────────────────────────
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
    "-", "null", "NULL", "none", "None",
}

def is_blank(val):
    return not val or val in BLANK_VALUES

def is_numeric_clean(val):
    if not val:
        return False
    try:
        float(val)
        if any(c in val for c in [",", "+", "%", "/", " "]):
            return False
        if any(c.isalpha() for c in val):
            return False
        return True
    except ValueError:
        return False

def has_leading_dot(val):
    return bool(
        val and val.startswith(".")
        and len(val) > 1
        and val[1:].replace(".", "").isdigit()
    )

def is_cloud(issue):
    return cf_val(issue, CF["software_cat"]) in ALL_CLOUD_CATEGORIES

def is_web_product(issue):
    return cf_val(issue, CF["software_cat"]) in WEB_CATEGORIES

def is_cloud_product(issue):
    return cf_val(issue, CF["software_cat"]) in CLOUD_CATEGORIES

def ticket_status(issue):
    return issue.get("status", {}).get("name", "")

def is_closed(issue):
    return ticket_status(issue) in ("Closed", "Resolved")

# ── Rules Engine ──────────────────────────────────────────────────────────────
def check_ticket(issue):
    found = []

    def crit(field, desc):
        found.append(("CRITICAL", field, desc))

    def warn(field, desc):
        found.append(("WARNING", field, desc))

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
    amc         = cf_val(issue, CF["amc_amount"])
    asc         = cf_val(issue, CF["asc_amount"])
    company_gid = cf_val(issue, CF["company_group_id"])
    cloud_stype = cf_val(issue, CF["cloud_server_type"])
    sw_cat      = cf_val(issue, CF["software_cat"])
    cloud       = is_cloud(issue)
    closed      = is_closed(issue)

    # ── CRITICAL: Payment Status ──────────────────────────────────────────────
    if is_blank(pay_status):
        crit("Payment Status", "Blank — must be Credit / Fully Paid / Advance Paid / Partially Paid")
    elif pay_status not in VALID_PAYMENT_STATUSES:
        crit("Payment Status", f"Invalid value: '{pay_status}'")

    # ── CRITICAL: Total Amount ────────────────────────────────────────────────
    if is_blank(total) or total == "0":
        crit("Total Amount", "Blank or zero")
    elif not is_numeric_clean(total):
        crit("Total Amount", f"Not a clean number — remove text/commas/symbols: '{total}'")

    # ── CRITICAL: Received Amount ─────────────────────────────────────────────
    if not is_blank(received) and received != "0":
        if has_leading_dot(received):
            crit("Received Amount", f"Leading dot — system reads as 0: '{received}' → correct to '{received.lstrip('.')}'")
        elif not is_numeric_clean(received):
            crit("Received Amount", f"Not a clean number — remove text/commas/symbols: '{received}'")

    # ── CRITICAL: Payment flag vs status ─────────────────────────────────────
    if pay_status == "Fully Paid":
        if is_blank(received) or received == "0":
            crit("Received Amount", "Payment Status = Fully Paid but Received Amount is blank or zero")
        if pay_flag != "1":
            crit("Payment Received Flag", "Payment Status = Fully Paid but Payment Received checkbox is not ticked")
    if pay_flag == "1" and pay_status == "Credit":
        crit("Payment Status", "Payment Received = YES but Status still shows Credit — update to Fully Paid")

    # ── CRITICAL: Received > Total ────────────────────────────────────────────
    if is_numeric_clean(total) and is_numeric_clean(received):
        t = float(total)
        r = float(received)
        if t > 0 and r > t * 1.06:
            crit("Amount Mismatch", f"Received ({r:,.0f}) exceeds Total ({t:,.0f}) by more than 6% — verify amounts")

    # ── CRITICAL: VAT Amount ──────────────────────────────────────────────────
    if not is_blank(vat):
        if not is_numeric_clean(vat):
            crit("VAT Amount", f"Not a clean number: '{vat}' — enter numeric value only (e.g. 4550)")

    # ── CRITICAL: Lock fields (desktop only) ──────────────────────────────────
    if not cloud:
        if lock_issued == "1" and is_blank(lock_no):
            crit("Lock No", "Lock Issued = YES but Lock No is empty")
        if lock_issued == "1" and is_blank(lock_batch):
            crit("Lock Batch No", "Lock Issued = YES but Batch No is empty")
        if lock_issued == "1" and is_blank(lock_date):
            crit("Lock Issued Date", "Lock Issued = YES but Lock Date is empty")
        if lock_issued == "0" and not is_blank(lock_no):
            crit("Lock Issued", f"Lock No is filled ('{lock_no}') but Lock Issued checkbox = NO — tick the checkbox")
        if not is_blank(lock_no) and not is_blank(lock_batch):
            no_looks_batch = lock_no.startswith("5524-") or lock_no.startswith("5523-")
            batch_looks_no = lock_batch.startswith("1003-") or lock_batch.startswith("2003-")
            if no_looks_batch and batch_looks_no:
                crit("Lock No / Batch No", "Fields are REVERSED — Lock No contains batch number and vice versa — swap them")

    # ── CRITICAL: License fields (Swastik Web only, any status) ──────────────
    if is_web_product(issue):
        if is_blank(lic_code):
            crit("License Code", f"{sw_cat} — License Code must be filled")
        if is_blank(lic_date):
            crit("License Issue Date", f"{sw_cat} — License Issue Date must be filled")

    # ── CRITICAL: Software Expiry on closed tickets ───────────────────────────
    if closed and is_blank(expiry):
        crit("Software Expiry Date", "Ticket is closed but Software Expiry Date is blank")

    # ── CRITICAL: AMC / ASC ───────────────────────────────────────────────────
    if not cloud and (is_blank(amc) or amc == "0"):
        crit("AMC Amount", "Desktop product but AMC Amount is blank or zero")
    if cloud and (is_blank(asc) or asc == "0"):
        crit("ASC Amount", f"{sw_cat} — Annual Subscription (ASC) amount is blank or zero")

    # ── WARNING: Contact data ─────────────────────────────────────────────────
    if is_blank(contact):
        warn("Contact Person", "Blank — fill in client contact name")
    if is_blank(address):
        warn("Client Address", "Blank")
    if phone and "@" in phone:
        warn("Phone / Mobile", f"Email address entered in Phone field: '{phone}' — move to Email field")
    if pan and not is_blank(pan) and pan.lower() not in ["n/a", "n.a", "na"]:
        digits = re.sub(r"\D", "", pan)
        if len(digits) > 0 and len(digits) != 9:
            warn("PAN Number", f"{len(digits)} digits — Nepal PAN must be 9 digits: '{pan}'")

    # ── WARNING: Sales Executive ──────────────────────────────────────────────
    if is_blank(sales_exec):
        warn("Sales Executive", "Blank — assign a sales executive")

    # ── WARNING: Bill Type ────────────────────────────────────────────────────
    if is_blank(bill_type):
        warn("Bill Type", "Blank — set to Vat Bill / Internal Bill / PI")
    elif bill_type in ["NA", "N/A", "na"] and closed:
        warn("Bill Type", "Bill Type is NA on a closed ticket — update to actual bill type")

    # ── WARNING: Swastik Cloud-specific (Gold/Nepal Cloud) ────────────────────
    if is_cloud_product(issue):
        if is_blank(company_gid):
            warn("Company Group ID", f"{sw_cat} — Company Group ID is blank")
        if is_blank(cloud_stype):
            warn("Cloud Server Type", f"{sw_cat} — Cloud Server Type is blank")

    return found

# ── Fetch single ticket ───────────────────────────────────────────────────────
def fetch_ticket(ticket_id):
    headers = {"X-Redmine-API-Key": REDMINE_KEY}
    resp = requests.get(
        f"{REDMINE_URL}/issues/{ticket_id}.json",
        headers=headers,
        params={"include": "custom_fields"},
        timeout=30,
    )
    resp.raise_for_status()
    issue = resp.json()["issue"]
    # Only process Installation tracker in our project
    if issue.get("project", {}).get("id") != PROJECT_ID:
        return None
    if issue.get("tracker", {}).get("id") != TRACKER_ID:
        return None
    return issue

# ── Microsoft Graph Email ─────────────────────────────────────────────────────
def get_access_token():
    url  = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    data = {
        "grant_type":    "client_credentials",
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope":         "https://graph.microsoft.com/.default",
    }
    resp = requests.post(url, data=data, timeout=30)
    resp.raise_for_status()
    return resp.json()["access_token"]

def send_email(subject, html_body):
    token = get_access_token()
    url   = f"https://graph.microsoft.com/v1.0/users/{SENDER_EMAIL}/sendMail"
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": html_body},
            "toRecipients": [
                {"emailAddress": {"address": a}} for a in ALERT_TO
            ],
        }
    }
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    print(f"    Email sent (HTTP {resp.status_code})")

def ticket_url(tid):
    return f"{REDMINE_URL}/issues/{tid}"

def build_email(issue, all_issues):
    tid     = issue["id"]
    company = cf_val(issue, CF["company_name"]) or issue.get("subject", f"#{tid}")
    author  = issue.get("author", {}).get("name", "Unknown")
    created = issue.get("created_on", "")[:10]
    updated = issue.get("updated_on", "")[:10]
    status  = ticket_status(issue)
    sw_cat  = cf_val(issue, CF["software_cat"])
    url     = ticket_url(tid)

    crits = [(f, d) for s, f, d in all_issues if s == "CRITICAL"]
    warns = [(f, d) for s, f, d in all_issues if s == "WARNING"]

    def make_rows(items, bg_hdr, col_hdr, icon, label):
        if not items:
            return ""
        hdr = f"""<tr><th colspan="2" style="padding:9px 14px;background:{bg_hdr};
            color:{col_hdr};font-size:11px;text-transform:uppercase;
            letter-spacing:.5px;text-align:left">{icon} {label}</th></tr>"""
        rows = "".join(f"""<tr>
            <td style="padding:7px 14px;border-bottom:1px solid #f3f4f6;
                font-weight:600;color:#374151;width:170px;vertical-align:top">{f}</td>
            <td style="padding:7px 14px;border-bottom:1px solid #f3f4f6;
                color:#6b7280;vertical-align:top">{d}</td>
        </tr>""" for f, d in items)
        return hdr + rows

    crit_rows = make_rows(crits, "#fee2e2", "#991b1b", "🔴", f"Critical Issues ({len(crits)})")
    warn_rows = make_rows(warns, "#fef3c7", "#92400e", "🟡", f"Warnings ({len(warns)})")

    hdr_color  = "#dc2626" if crits else "#d97706"
    title      = "🚨 Critical Issues Found" if crits else "⚠️ Data Issues Found"
    count_line = f"{len(crits)} critical, {len(warns)} warning(s)" if crits else f"{len(warns)} warning(s)"

    return f"""
<div style="font-family:Segoe UI,Arial,sans-serif;max-width:640px;margin:0 auto">
  <div style="background:{hdr_color};padding:16px 24px;border-radius:8px 8px 0 0">
    <h2 style="color:#fff;margin:0;font-size:16px">{title} — Ticket #{tid}</h2>
    <p style="color:rgba(255,255,255,.8);margin:4px 0 0;font-size:13px">{count_line}</p>
  </div>
  <div style="border:1px solid #e5e7eb;border-top:none;background:#fff;
      padding:18px 24px;border-radius:0 0 8px 8px">
    <table style="width:100%;border-collapse:collapse;margin-bottom:16px;
        font-size:13px;background:#f9fafb;border-radius:6px">
      <tr>
        <td style="padding:6px 14px;color:#6b7280;width:130px">Ticket</td>
        <td style="padding:6px 14px"><a href="{url}"
            style="color:#2563eb;font-weight:600">#{tid}</a></td>
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
        <td style="padding:6px 14px;color:#6b7280">Created by</td>
        <td style="padding:6px 14px">{author} on {created}</td>
      </tr>
      <tr style="background:#f3f4f6">
        <td style="padding:6px 14px;color:#6b7280">Last updated</td>
        <td style="padding:6px 14px">{updated}</td>
      </tr>
    </table>
    <table style="width:100%;border-collapse:collapse;font-size:13px">
      {crit_rows}{warn_rows}
    </table>
    <div style="margin-top:16px">
      <a href="{url}" style="display:inline-block;background:{hdr_color};
          color:#fff;padding:9px 20px;border-radius:6px;text-decoration:none;
          font-size:13px;font-weight:600">Open Ticket in Redmine →</a>
    </div>
  </div>
  <p style="font-size:11px;color:#9ca3af;margin-top:8px;text-align:center">
    HiTech Redmine Alert System v5 — triggered on ticket update</p>
</div>"""

# ── Main ──────────────────────────────────────────────────────────────────────
def run():
    # Get ticket ID passed from GitHub Actions via environment variable
    ticket_id = os.environ.get("TICKET_ID", "").strip()

    if not ticket_id:
        print("No TICKET_ID provided — nothing to check.")
        return

    print(f"Checking ticket #{ticket_id}...")
    issue = fetch_ticket(int(ticket_id))

    if not issue:
        print(f"Ticket #{ticket_id} is not an Installation ticket in Project 3 — skipping.")
        return

    company = cf_val(issue, CF["company_name"]) or issue.get("subject", f"#{ticket_id}")
    found   = check_ticket(issue)

    if not found:
        print(f"  #{ticket_id} {company}: ✓ Clean — no issues found")
        return

    crits = [x for x in found if x[0] == "CRITICAL"]
    warns = [x for x in found if x[0] == "WARNING"]
    print(f"  #{ticket_id} {company}: {len(crits)} critical, {len(warns)} warning(s) — sending email")

    prefix = "🚨" if crits else "⚠️"
    subj   = f"{prefix} Redmine #{ticket_id} — {len(found)} issue(s) — {company}"
    html   = build_email(issue, found)
    send_email(subj, html)
    print("Done.")

if __name__ == "__main__":
    run()
