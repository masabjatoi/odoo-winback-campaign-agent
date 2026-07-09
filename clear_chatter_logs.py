"""Remove [Win-Back] and draft messages from Odoo chatter, reset partner win-back fields, and delete campaign records to allow clean testing rerun."""
from odoo_client import OdooClient

c = OdooClient()
c.authenticate()

search_domain = [
    ('model', '=', 'res.partner'),
    ('message_type', 'in', ['email', 'email_outgoing', 'comment']),
    ('body', 'ilike', '%[Win-Back]%')
]
messages = c.execute('mail.message', 'search_read', [search_domain], {'fields': ['id', 'res_id', 'body']})
if messages:
    print(f"Found {len(messages)} win-back chatter messages to delete:")
    msg_ids = [m['id'] for m in messages]
    for m in messages:
        # Safe encoding print to avoid Windows charmap errors
        snippet = m.get('body', '') or ''
        snippet_safe = snippet[:80].encode('ascii', errors='replace').decode('ascii')
        print(f"  Message ID: {m['id']} on Partner ID: {m['res_id']} - Snippet: {snippet_safe}...")
    c.execute('mail.message', 'unlink', [msg_ids])
    print("[Success] All matching chatter messages have been deleted from the database!\n")
else:
    print("No matching win-back chatter messages found in the database.\n")

# 2. Reset partner win-back fields
partners = c.execute('res.partner', 'search_read', [[
    '|',
    ('x_lisa_wb_email_html', '!=', False),
    ('lisa_wb_processed', '=', True)
]], {'fields': ['id', 'name']})
if partners:
    print(f"Found {len(partners)} partners to reset win-back fields:")
    partner_ids = [p['id'] for p in partners]
    for p in partners:
        print(f"  Partner ID: {p['id']} ({p['name']})")
    c.execute('res.partner', 'write', [partner_ids, {
        'x_lisa_wb_email_html': False,
        'x_lisa_wb_email_subject': False,
        'lisa_wb_processed': False
    }])
    print("[Success] Win-back custom fields reset on all affected partners!\n")
else:
    print("No partners found with active win-back data to clear.\n")

# 3. Delete winback.campaign records
campaigns = c.execute('winback.campaign', 'search_read', [[]], {'fields': ['id', 'partner_id']})
if campaigns:
    print(f"Found {len(campaigns)} winback.campaign records to delete:")
    camp_ids = [camp['id'] for camp in campaigns]
    for camp in campaigns:
        partner_name = camp['partner_id'][1] if camp.get('partner_id') else "Unknown"
        print(f"  Campaign ID: {camp['id']} (Partner: {partner_name})")
    c.execute('winback.campaign', 'unlink', [camp_ids])
    print("[Success] All winback.campaign records have been deleted from the database!\n")
else:
    print("No winback.campaign records found to delete.\n")
