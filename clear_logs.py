import os
import sys
import argparse
import socket
import xmlrpc.client

socket.setdefaulttimeout(90)


def _load_env():
    env = dict(os.environ)
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip().strip('"').strip("'")
    return env


class OdooSession:
    def __init__(self):
        env = _load_env()
        self.url = env.get("ODOO_URL", "").rstrip("/")
        self.db = env.get("ODOO_DB", "")
        self.username = env.get("ODOO_USERNAME", "")
        self.password = env.get("ODOO_API_KEY", "")
        missing = [
            key for key, value in {
                "ODOO_URL": self.url,
                "ODOO_DB": self.db,
                "ODOO_USERNAME": self.username,
                "ODOO_API_KEY": self.password,
            }.items()
            if not value
        ]
        if missing:
            raise RuntimeError(f"Missing Odoo environment values: {', '.join(missing)}")
        common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common", allow_none=True)
        self.uid = common.authenticate(self.db, self.username, self.password, {})
        if not self.uid:
            raise PermissionError("Odoo authentication failed.")
        print(f"[Odoo] Authenticated as UID {self.uid}")

    def execute_kw(self, *call_args):
        if len(call_args) == 3:
            model, method, args = call_args
            kwargs = None
        elif len(call_args) == 4:
            model, method, args, kwargs = call_args
        elif len(call_args) == 6:
            _db, _uid, _password, model, method, args = call_args
            kwargs = None
        elif len(call_args) == 7:
            _db, _uid, _password, model, method, args, kwargs = call_args
        else:
            raise TypeError("execute_kw expects either compact or XML-RPC style arguments")

        models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object", allow_none=True)
        return models.execute_kw(
            self.db, self.uid, self.password,
            model, method, args, kwargs or {}
        )


def get_odoo_client():
    session = OdooSession()
    return session, session.uid, session.db, session.password

WINBACK_CHATTER_KEYWORDS = [
    'win-back', 'win back', 'lisa win-back', 'lisa ai win-back',
    '[audit redirect]', 'campaign stage', 'campaign status',
    'win-back draft ready', 'win-back update', 'win-back memory log',
    'test 1:', 'test 2:', 'test 3:',
    'partner re-engagement', 'outreach email sent',
    'draft ready for review', 'friendly reminder',
    'special update from our team', 'send lisa win-back',
]


def _matches_winback_chatter(message):
    text = " ".join(
        str(message.get(field) or "")
        for field in ("subject", "body", "record_name")
    ).lower()
    return any(keyword in text for keyword in WINBACK_CHATTER_KEYWORDS)


def _find_partner_ids(models, uid, db, password, partner_email=None, partner_name=None):
    domain = []
    if partner_email:
        domain.append(('email', 'ilike', partner_email.strip()))
    if partner_name:
        domain.append(('name', 'ilike', partner_name.strip()))
    if not domain:
        return []
    return models.execute_kw(db, uid, password, 'res.partner', 'search', [domain])


def _delete_contact_chatter(models, uid, db, password, partner_ids=None):
    domain = [('model', '=', 'res.partner')]
    if partner_ids:
        domain.append(('res_id', 'in', partner_ids))

    messages = models.execute_kw(
        db, uid, password, 'mail.message', 'search_read',
        [domain],
        {'fields': ['id', 'subject', 'body', 'record_name'], 'limit': 0}
    )
    message_ids = [m['id'] for m in messages if _matches_winback_chatter(m)]

    if message_ids:
        models.execute_kw(db, uid, password, 'mail.message', 'unlink', [message_ids])
        print(f"Deleted {len(message_ids)} win-back chatter messages.")
    else:
        print("No win-back chatter messages found.")
    return len(message_ids)


def clear_logs(partner_email=None, partner_name=None, chatter_only=False):
    print("Connecting to Odoo...")
    models, uid, db, password = get_odoo_client()

    partner_ids = _find_partner_ids(models, uid, db, password, partner_email, partner_name)
    if partner_email or partner_name:
        print(f"Matched partner IDs: {partner_ids or 'none'}")
        if not partner_ids:
            print("No matching partner found; nothing cleared.")
            return

    if not chatter_only:
        print("Clearing winback.campaign records...")
        campaign_domain = []
        if partner_ids:
            campaign_domain.append(('partner_id', 'in', partner_ids))
        campaign_ids = models.execute_kw(db, uid, password, 'winback.campaign', 'search', [campaign_domain])
        if campaign_ids:
            models.execute_kw(db, uid, password, 'winback.campaign', 'unlink', [campaign_ids])
            print(f"Deleted {len(campaign_ids)} winback.campaign records.")
        else:
            print("No winback.campaign records found.")
    else:
        print("Skipping winback.campaign records.")

    print("Clearing win-back mail.message chatter logs...")
    _delete_contact_chatter(models, uid, db, password, partner_ids or None)

    if not chatter_only:
        print("Clearing x_lisa_wb_email_html drafts on res.partner...")
        try:
            draft_domain = [('x_lisa_wb_email_html', '!=', False)]
            if partner_ids:
                draft_domain.append(('id', 'in', partner_ids))
            draft_partner_ids = models.execute_kw(db, uid, password, 'res.partner', 'search', [draft_domain])
            if draft_partner_ids:
                models.execute_kw(db, uid, password, 'res.partner', 'write', [draft_partner_ids, {'x_lisa_wb_email_html': False, 'lisa_wb_processed': False}])
                print(f"Cleared HTML drafts for {len(draft_partner_ids)} partners.")
            else:
                print("No HTML drafts found.")
        except Exception as e:
            print(f"Warning: Could not clear HTML drafts: {e}")
    else:
        print("Skipping HTML drafts.")

    print("Done!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Clear Lisa win-back chatter from Odoo contacts.")
    parser.add_argument("--partner-email", help="Limit cleanup to contacts matching this email.")
    parser.add_argument("--partner-name", help="Limit cleanup to contacts matching this name.")
    parser.add_argument("--chatter-only", action="store_true", help="Only delete contact chatter; keep campaigns and drafts.")
    args = parser.parse_args()
    clear_logs(
        partner_email=args.partner_email,
        partner_name=args.partner_name,
        chatter_only=args.chatter_only,
    )
