"""
reset.py - Win-Back Agent Data Reset Script
============================================
Connects to Odoo and removes ALL data written by the Win-Back Agent:
  1. Deletes all winback.campaign records
  2. Clears custom Lisa win-back fields on res.partner
     (x_lisa_wb_email_html, x_lisa_wb_email_subject, lisa_wb_processed)
  3. Deletes chatter messages on res.partner posted by the agent
  4. Cancels / removes mail.activity records posted by the agent
  5. Clears Win-Back Memory Log from partner internal notes

Run from the project directory:
    python reset.py
"""

import sys
from odoo_client import OdooClient
import config

print("=" * 60)
print("  Win-Back Agent -- Odoo Data Reset")
print("=" * 60)

client = OdooClient()
client.authenticate()

def execute(model, method, args, kwargs=None):
    return client.execute(model, method, args, kwargs or {})


# ── 1. Delete all winback.campaign records ────────────────────────────────────
print("\n[1/5] Deleting winback.campaign records...")
try:
    campaign_ids = execute("winback.campaign", "search", [[]])
    if campaign_ids:
        execute("winback.campaign", "unlink", [campaign_ids])
        print(f"      Deleted {len(campaign_ids)} winback.campaign record(s).")
    else:
        print("      No winback.campaign records found.")
except Exception as e:
    print(f"      [Warning] Could not delete winback.campaign records: {e}")


# ── 2. Clear Lisa win-back custom fields on res.partner ──────────────────────
print("\n[2/5] Resetting Lisa win-back fields on res.partner...")
try:
    all_fields = execute("res.partner", "fields_get", [], {"attributes": ["type"]})
    clear_vals = {}
    if "x_lisa_wb_email_html"    in all_fields: clear_vals["x_lisa_wb_email_html"]    = False
    if "x_lisa_wb_email_subject" in all_fields: clear_vals["x_lisa_wb_email_subject"] = False
    if "lisa_wb_processed"       in all_fields: clear_vals["lisa_wb_processed"]       = False

    if clear_vals:
        domain_parts = []
        for field, val in clear_vals.items():
            ftype = all_fields[field].get("type", "")
            if ftype == "boolean":
                domain_parts.append((field, "=", True))
            else:
                domain_parts.append((field, "!=", False))
        if len(domain_parts) > 1:
            domain = ["|"] * (len(domain_parts) - 1) + domain_parts
        else:
            domain = domain_parts

        partner_ids = execute("res.partner", "search", [domain])
        if partner_ids:
            execute("res.partner", "write", [partner_ids, clear_vals])
            print(f"      Cleared fields {list(clear_vals.keys())} on {len(partner_ids)} partner(s).")
        else:
            print("      No partners with Lisa win-back fields set.")
    else:
        print("      No Lisa win-back custom fields found on res.partner.")
except Exception as e:
    print(f"      [Warning] Could not clear partner fields: {e}")


# ── 3. Delete Win-Back chatter messages on res.partner ───────────────────────
print("\n[3/5] Removing Win-Back chatter messages from res.partner...")
try:
    msg_ids = execute("mail.message", "search", [[
        ("model", "=", "res.partner"),
        ("body", "ilike", "[Win-Back]")
    ]])
    if msg_ids:
        execute("mail.message", "unlink", [msg_ids])
        print(f"      Deleted {len(msg_ids)} Win-Back chatter message(s).")
    else:
        print("      No Win-Back chatter messages found.")
except Exception as e:
    print(f"      [Warning] Could not delete chatter messages: {e}")


# ── 4. Remove mail.activity records on res.partner ───────────────────────────
print("\n[4/5] Removing Win-Back scheduled activities on res.partner...")
try:
    activity_ids = execute("mail.activity", "search", [[
        ("res_model", "=", "res.partner"),
        ("note", "ilike", "Win-Back")
    ]])
    if activity_ids:
        execute("mail.activity", "unlink", [activity_ids])
        print(f"      Deleted {len(activity_ids)} Win-Back activity record(s).")
    else:
        print("      No Win-Back activities found.")
except Exception as e:
    print(f"      [Warning] Could not remove activities: {e}")


# ── 5. Clear Win-Back Memory Log from partner internal notes ──────────────────
print("\n[5/5] Clearing Win-Back Memory Logs from partner internal notes...")
try:
    partners_with_notes = execute("res.partner", "search_read", [[
        ("comment", "ilike", "[Win-Back Memory Log]")
    ]], {"fields": ["id", "comment"]})

    cleared = 0
    for p in partners_with_notes:
        comment = p.get("comment") or ""
        header = "[Win-Back Memory Log]"
        if header in comment:
            clean = comment.split(header)[0].strip()
            execute("res.partner", "write", [[p["id"]], {"comment": clean or False}])
            cleared += 1

    if cleared:
        print(f"      Cleared Win-Back Memory Log from {cleared} partner(s).")
    else:
        print("      No Win-Back Memory Logs found in partner notes.")
except Exception as e:
    print(f"      [Warning] Could not clear memory logs: {e}")


print("\n" + "=" * 60)
print("  Win-Back Reset Complete.")
print("=" * 60)
