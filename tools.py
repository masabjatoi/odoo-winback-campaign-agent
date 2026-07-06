import os
import xmlrpc.client
import smtplib
import socket
import json
import re
from html import escape
# Set default socket timeout of 90 seconds to prevent XML-RPC calls from hanging indefinitely
socket.setdefaulttimeout(90)
from collections import Counter
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone
from typing import Any
from langchain_core.tools import tool, ToolException
import config
from odoo_client import OdooClient

client = OdooClient()

def get_odoo_client():
    """Connects to Odoo and returns (models_proxy, uid, db, password), leveraging the robust OdooClient."""
    try:
        if not client._uid:
            client.authenticate()
        return client, client._uid, config.ODOO_DB, config.ODOO_API_KEY
    except Exception as auth_err:
        raise ToolException(f"Odoo connection failed: {auth_err}")

_config_loaded = False

from html.parser import HTMLParser

class ChatterHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.reset()
        self.fed = []
        self.ignored_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('style', 'script', 'head'):
            self.ignored_depth += 1
        elif tag in ('p', 'br', 'div', 'tr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            if self.ignored_depth == 0:
                self.fed.append('\n')
        elif tag == 'td':
            if self.ignored_depth == 0:
                self.fed.append(' ')

    def handle_endtag(self, tag):
        if tag in ('style', 'script', 'head'):
            self.ignored_depth = max(0, self.ignored_depth - 1)
        elif tag in ('p', 'div', 'tr'):
            if self.ignored_depth == 0:
                self.fed.append('\n')

    def handle_data(self, d):
        if self.ignored_depth == 0:
            self.fed.append(d)

    def get_data(self):
        text = ''.join(self.fed)
        import html as std_html
        text = std_html.unescape(text)
        import re
        text = re.sub(r'\n\s*\n+', '\n\n', text)
        return text.strip()

def clean_html_for_chatter(html_str: str) -> str:
    if not html_str:
        return ""
    import re
    # Strip basic block comments if any
    html_str = re.sub(r'<!--.*?-->', '', html_str, flags=re.DOTALL)
    parser = ChatterHTMLParser()
    parser.feed(html_str)
    plain_text = parser.get_data()
    
    # Reconstruct with clean p tags and br for spacing
    paragraphs = [p.strip() for p in plain_text.split('\n\n') if p.strip()]
    chatter_paragraphs = []
    for p in paragraphs:
        import html as std_html
        formatted_p = std_html.escape(p).replace('\n', '<br/>')
        chatter_paragraphs.append(f"<p>{formatted_p}</p>")
    return "\n".join(chatter_paragraphs)

def normalize_email_body_html(body_html: str) -> str:
    """Convert body-only draft content into clean paragraph HTML for Odoo's mail wrapper."""
    if not body_html:
        return ""

    cleaned = re.sub(r'<!--.*?-->', '', body_html, flags=re.DOTALL).strip()
    cleaned = re.sub(r'</?(?:html|head|body)[^>]*>', '', cleaned, flags=re.IGNORECASE).strip()
    has_block_html = bool(re.search(r'<\s*(p|div|br|ul|ol|li|table|h[1-6])\b', cleaned, flags=re.IGNORECASE))

    if has_block_html:
        cleaned = re.sub(r'<p\b([^>]*)>\s*', r'<p\1>', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\s*</p>', '</p>', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'(</p>|<br\s*/?>)\s*(?=<p\b)', r'\1\n', cleaned, flags=re.IGNORECASE)
        return cleaned.strip()

    paragraphs = [p.strip() for p in re.split(r'\n\s*\n+', cleaned) if p.strip()]
    if not paragraphs:
        paragraphs = [line.strip() for line in cleaned.splitlines() if line.strip()]
    return "\n".join(f"<p>{escape(p).replace(chr(10), '<br/>')}</p>" for p in paragraphs)

def load_odoo_company_config():
    """
    Fetches configurations from Odoo dynamically and updates config module parameters.
    Only runs once per python process lifetime to avoid redundant Odoo calls.
    """
    global _config_loaded
    if _config_loaded:
        return
        
    try:
        models, uid, db, password = get_odoo_client()
        
        # First, fetch current user's active company ID
        user_data = models.execute_kw(db, uid, password, 'res.users', 'read', [[uid]], {'fields': ['company_id']})
        if not user_data or not user_data[0].get('company_id'):
            raise RuntimeError("Could not retrieve active company ID for current user.")
        company_id = user_data[0]['company_id'][0]
        print(f"[Odoo Config] Active Company ID: {company_id}")

        # Dynamically verify which custom lisa_wb_ fields exist on res.company
        fields_to_read = ['name', 'email', 'phone']
        lisa_fields = models.execute_kw(db, uid, password, 'ir.model.fields', 'search_read', [
            [('model', '=', 'res.company'), ('name', 'in', [
                'lisa_wb_inactivity_threshold_days',
                'lisa_wb_interval_days',
                'lisa_wb_offer_email2',
                'lisa_wb_max_emails',
                'lisa_wb_segment_by_category',
                'lisa_wb_auto_reply',
                'lisa_wb_recipient_override'
            ])]
        ], {'fields': ['name']})
        fields_to_read.extend([f['name'] for f in lisa_fields])

        records = models.execute_kw(
            db, uid, password, 'res.company', 'read', [[company_id]],
            {'fields': fields_to_read}
        )
        if records:
            company_config = records[0]
            if 'lisa_wb_inactivity_threshold_days' in company_config:
                config.INACTIVITY_THRESHOLD_DAYS = int(company_config['lisa_wb_inactivity_threshold_days'])
            if 'lisa_wb_interval_days' in company_config:
                config.WINBACK_INTERVAL_DAYS = int(company_config['lisa_wb_interval_days'])
            if 'lisa_wb_offer_email2' in company_config:
                config.WINBACK_OFFER_EMAIL2 = company_config['lisa_wb_offer_email2'] or ''
            if 'lisa_wb_max_emails' in company_config:
                config.MAX_WINBACK_EMAILS = int(company_config['lisa_wb_max_emails'])
            if 'lisa_wb_segment_by_category' in company_config:
                config.SEGMENT_BY_CATEGORY = bool(company_config['lisa_wb_segment_by_category'])
            if 'lisa_wb_auto_reply' in company_config:
                config.AUTO_REPLY = bool(company_config['lisa_wb_auto_reply'])
                config.AUTO_APPROVE = config.AUTO_REPLY
            if 'lisa_wb_recipient_override' in company_config:
                config.RECIPIENT_OVERRIDE = (company_config['lisa_wb_recipient_override'] or '').strip()
            print(f"[Odoo Config] Dynamically loaded from Odoo: "
                  f"INACTIVITY_THRESHOLD_DAYS={config.INACTIVITY_THRESHOLD_DAYS}, "
                  f"WINBACK_INTERVAL_DAYS={config.WINBACK_INTERVAL_DAYS}, "
                  f"WINBACK_OFFER_EMAIL2={config.WINBACK_OFFER_EMAIL2}, "
                  f"MAX_WINBACK_EMAILS={config.MAX_WINBACK_EMAILS}, "
                  f"SEGMENT_BY_CATEGORY={config.SEGMENT_BY_CATEGORY}, "
                  f"AUTO_REPLY={config.AUTO_REPLY}, "
                  f"RECIPIENT_OVERRIDE={config.RECIPIENT_OVERRIDE or 'None'} [{'ACTIVE' if config.RECIPIENT_OVERRIDE else 'DISABLED'}]")
            _config_loaded = True
    except Exception as e:
        print(f"[Odoo Config] [Warning] Failed to load dynamic config from Odoo: {e}. Using local config defaults.")


def parse_odoo_date(date_str: str) -> datetime:
    """Helper to parse various Odoo date string formats into a UTC timezone-aware datetime."""
    if not date_str:
        raise ValueError("Date string is empty")
    
    date_str = date_str.strip()
    
    # Normalize Z to +00:00 to support fromisoformat under older Python versions
    normalized = date_str
    if normalized.endswith('Z'):
        normalized = normalized[:-1] + '+00:00'
    
    # Try fromisoformat first
    try:
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        pass
        
    # If fromisoformat fails, do fallback parsing using formatting rules
    clean_str = date_str.replace('T', ' ')
    if '.' in clean_str:
        clean_str = clean_str.split('.')[0]
        
    formats = [
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d %H:%M:%S%z',
        '%Y-%m-%d',
        '%Y-%m-%d%z'
    ]
    
    for fmt in formats:
        try:
            dt = datetime.strptime(clean_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue
            
    raise ValueError(f"Could not parse Odoo date string: {date_str}")


def format_date_for_odoo(date_input) -> str:
    """Standardizes datetime objects or string inputs into Odoo's query-friendly format 'YYYY-MM-DD HH:MM:SS'."""
    if isinstance(date_input, str):
        dt = parse_odoo_date(date_input)
    elif isinstance(date_input, datetime):
        dt = date_input
    else:
        raise TypeError("Input must be a string or datetime object")
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

@tool
def get_inactive_partners(inactivity_threshold_days: int = None) -> list:
    """Queries Odoo to find active partners who have not placed a confirmed order
    in the last inactivity_threshold_days.
    
    Args:
        inactivity_threshold_days: Number of days of inactivity (default loaded from central config).
        
    Returns:
        A list of dictionaries with partner details: id, name, email, salesperson_id, last_order_date.
    """
    load_odoo_company_config()
    if inactivity_threshold_days is None:
        inactivity_threshold_days = config.INACTIVITY_THRESHOLD_DAYS
    try:
        models, uid, db, password = get_odoo_client()
        
        # 1. Query sale.order to get the last order date for each partner
        # We group by partner_id and get the max date_order for confirmed sales
        domain = [('state', 'in', ['sale', 'done'])]
        fields = ['partner_id', 'date_order:max']
        groupby = ['partner_id']
        
        orders = models.execute_kw(db, uid, password, 'sale.order', 'read_group', [domain, fields, groupby])
        
        now = datetime.now(timezone.utc)
        inactivity_limit = now - timedelta(days=inactivity_threshold_days)
        
        # 2. Filter partners who exceed the inactivity threshold
        inactive_candidates = []
        for o in orders:
            partner_info = o.get('partner_id')
            if not partner_info:
                continue
            partner_id = partner_info[0]
            last_order_str = o.get('date_order')
            
            if last_order_str:
                try:
                    last_order_dt = parse_odoo_date(last_order_str)
                    if last_order_dt <= inactivity_limit:
                        inactive_candidates.append((partner_id, last_order_str))
                except Exception as date_err:
                    print(f"Warning: Failed to parse order date '{last_order_str}': {date_err}")
                    continue
        
        if not inactive_candidates:
            return []
            
        # 3. Retrieve contact details for these partners to verify eligibility
        partner_ids = [c[0] for c in inactive_candidates]
        partner_details = models.execute_kw(
            db, uid, password, 'res.partner', 'search_read',
            [[('id', 'in', partner_ids), ('active', '=', True), ('email', '!=', False)]],
            {'fields': ['id', 'name', 'email', 'user_id', 'lang', 'country_id']}
        )
        
        # Check blacklist status in mail.blacklist for all candidate emails in batch
        emails = list(set((p.get('email') or '').strip() for p in partner_details if p.get('email')))
        blacklisted_emails = set()
        if emails:
            try:
                blacklist_records = models.execute_kw(
                    db, uid, password, 'mail.blacklist', 'search_read',
                    [[('email', 'in', emails), ('active', '=', True)]],
                    {'fields': ['email']}
                )
                blacklisted_emails = {r['email'].strip().lower() for r in blacklist_records if r.get('email')}
            except Exception as bl_err:
                print(f"Warning: Could not query mail.blacklist ({bl_err}). Skipping blacklist filter.")
        
        # Map last order dates back to the eligible partners
        last_order_map = {c[0]: c[1] for c in inactive_candidates}
        
        eligible_partners = []
        seen_emails = set()
        for p in partner_details:
            pid = p['id']
            email = (p.get('email') or '').strip().lower()
            if email in blacklisted_emails:
                continue
            if email in seen_emails:
                print(f"[Tools] Skipping partner ID {pid} because email '{email}' is already being processed under another partner ID.")
                continue
            seen_emails.add(email)
                
            salesperson = p.get('user_id')
            salesperson_id = salesperson[0] if salesperson else None
            
            # Safe unwrapping of country_id tuple
            country_info = p.get('country_id')
            country_name = country_info[1] if (country_info and len(country_info) > 1) else None
            
            eligible_partners.append({
                'id': pid,
                'name': p.get('name'),
                'email': p.get('email'),
                'salesperson_id': salesperson_id,
                'last_order_date': last_order_map.get(pid),
                'lang': p.get('lang') or 'en_US',
                'country': country_name
            })
            
        # Filter by category if segment_by_category is True
        if config.SEGMENT_BY_CATEGORY:
            filtered_partners = []
            for p in eligible_partners:
                cats = get_customer_purchased_categories.invoke({"partner_id": p['id']})
                if cats:
                    filtered_partners.append(p)
                else:
                    print(f"[Tools] Skipping partner {p['id']} ({p['name']}) because they have no purchase category history and segment_by_category is enabled.")
            eligible_partners = filtered_partners

        return eligible_partners
        
    except Exception as e:
        print(f"Error fetching inactive partners: {e}")
        raise ToolException(f"Error fetching inactive partners: {e}")

@tool
def check_partner_status(partner_id: int) -> dict:
    """Checks Odoo to verify if the customer is still active and eligible for communications.
    
    Args:
        partner_id: The Odoo customer ID.
        
    Returns:
        A dictionary with 'active' (bool) and 'is_blacklisted' (bool).
    """
    try:
        models, uid, db, password = get_odoo_client()
        records = models.execute_kw(
            db, uid, password, 'res.partner', 'search_read',
            [[('id', '=', partner_id)]],
            {'fields': ['active', 'email']}
        )
        if not records:
            return {'active': False, 'is_blacklisted': False, 'exists': False}
        
        p = records[0]
        email = (p.get('email') or '').strip()
        is_blacklisted = False
        
        if email:
            try:
                blacklist_count = models.execute_kw(
                    db, uid, password, 'mail.blacklist', 'search_count',
                    [[('email', '=', email), ('active', '=', True)]]
                )
                is_blacklisted = blacklist_count > 0
            except Exception as bl_err:
                print(f"Warning: Could not check mail.blacklist for {email}: {bl_err}")
                
        return {
            'active': p.get('active', False),
            'is_blacklisted': is_blacklisted,
            'exists': True
        }
    except Exception as e:
        print(f"Error checking partner status for ID {partner_id}: {e}")
        raise ToolException(f"Error checking partner status for ID {partner_id}: {e}")

@tool
def check_new_orders(partner_id: int, since_date_utc: str) -> list:
    """Checks Odoo for any confirmed orders placed by the customer since the given date.
    
    Args:
        partner_id: The Odoo customer ID.
        since_date_utc: ISO datetime string or Odoo datetime string 'YYYY-MM-DD HH:MM:SS' in UTC.
        
    Returns:
        A list of confirmed orders placed after the since_date_utc.
    """
    try:
        since_date_utc = format_date_for_odoo(since_date_utc)
        models, uid, db, password = get_odoo_client()
        domain = [
            ('partner_id', '=', partner_id),
            ('state', 'in', ['sale', 'done']),
            ('date_order', '>', since_date_utc)
        ]
        fields = ['name', 'date_order', 'amount_total']
        
        orders = models.execute_kw(db, uid, password, 'sale.order', 'search_read', [domain], {'fields': fields})
        return orders
    except Exception as e:
        print(f"Error checking new orders for partner {partner_id} since {since_date_utc}: {e}")
        raise ToolException(f"Error checking new orders for partner {partner_id} since {since_date_utc}: {e}")

@tool
def check_customer_replies(partner_id: int, since_date_utc: str) -> list:
    """Scans Odoo mail.message logs for incoming customer replies since a specific date across all Odoo models.
    Matches either by the partner ID as author or by the sender's email matching the customer's email.
    
    Args:
        partner_id: The Odoo customer ID.
        since_date_utc: Odoo datetime string 'YYYY-MM-DD HH:MM:SS' in UTC or ISO string.
        
    Returns:
        A list of incoming email replies from the customer.
    """
    try:
        since_date_utc = format_date_for_odoo(since_date_utc)
        models, uid, db, password = get_odoo_client()
        
        # Fetch partner's email to verify by email_from as well
        partner_data = models.execute_kw(db, uid, password, 'res.partner', 'read', [[partner_id]], {'fields': ['email']})
        partner_email = partner_data[0].get('email') if partner_data else None
        partner_email_lower = partner_email.strip().lower() if partner_email else None
        
        # Query mail.message across all models (invoices, CRM leads, sales orders) where the sender is the customer
        domain = [
            ('message_type', '=', 'email'),
            ('date', '>', since_date_utc),
        ]
        if partner_email_lower:
            domain.extend([
                '|',
                ('author_id', '=', partner_id),
                ('email_from', 'ilike', partner_email_lower)
            ])
        else:
            domain.append(('author_id', '=', partner_id))
            
        fields = ['id', 'author_id', 'email_from', 'date', 'body']
        messages = models.execute_kw(db, uid, password, 'mail.message', 'search_read', [domain], {'fields': fields})
        
        # Verify the message sender is the customer
        replies = []
        for msg in messages:
            author_info = msg.get('author_id')
            author_id = author_info[0] if author_info else None
            email_from = msg.get('email_from') or ''
            
            # Match by partner ID
            if author_id == partner_id:
                replies.append(msg)
                continue
                
            # Match by email_from
            if partner_email_lower and partner_email_lower in email_from.lower():
                replies.append(msg)
                continue
                
        return replies
    except Exception as e:
        print(f"Error checking customer replies for partner {partner_id}: {e}")
        raise ToolException(f"Error checking customer replies for partner {partner_id}: {e}")

@tool
def send_winback_email(
    partner_id: int,
    customer_name: str,
    customer_email: str,
    subject: str,
    body_html: str,
    salesperson_name: str = None,
    salesperson_email: str = None,
    campaign_stage_tag: str = None
) -> dict:
    """Sends a win-back outreach email to the customer. 
    
    Args:
        partner_id: The Odoo customer ID.
        customer_name: Customer's name.
        customer_email: Customer's email.
        subject: Subject of the email.
        body_html: HTML content of the email body.
        salesperson_name: Salesperson's full name.
        salesperson_email: Salesperson's email address.
        campaign_stage_tag: Machine-readable tag corresponding to the outreach stage (e.g. 'WB-1', 'WB-2', 'WB-3').
        
    Returns:
        A dictionary indicating success status.
    """
    # Determine stage tag
    tag = campaign_stage_tag
    if not tag:
        try:
            lead = reconstruct_lead_state_from_odoo(partner_id)
            if lead:
                curr_stage = lead.get("campaign_stage", "none")
                if curr_stage == "none":
                    tag = "WB-1"
                elif curr_stage == "email_1_sent":
                    tag = "WB-2"
                elif curr_stage == "email_2_sent":
                    tag = "WB-3"
        except Exception as e:
            print(f"Warning: Failed to derive campaign stage tag: {e}")
            
    if tag:
        tag_str = f"[{tag.upper().strip()}]"
        if tag_str not in subject:
            subject = f"{subject} {tag_str}"

    # Production mode - Write/send via Odoo mail template and custom fields
    try:
        load_odoo_company_config()
        models, uid, db, password = get_odoo_client()
        
        sent_to = config.RECIPIENT_OVERRIDE if config.RECIPIENT_OVERRIDE else customer_email
        email_body_html = normalize_email_body_html(body_html)
        audit_footer = (
            f"<p style='color:#888;font-size:11px;font-family:sans-serif;'>"
            f"[AUDIT REDIRECT] Intended recipient: {escape(customer_name)} &lt;{escape(customer_email)}&gt;"
            f"</p>"
        )
        sent_body_html = f"{email_body_html}\n{audit_footer}" if config.RECIPIENT_OVERRIDE else email_body_html

        # Step 1: Write HTML content to res.partner.x_lisa_wb_email_html
        processed_val = bool(config.AUTO_REPLY)
        partner_vals = {
            'x_lisa_wb_email_html': sent_body_html if config.AUTO_REPLY else email_body_html,
            'lisa_wb_processed': processed_val
        }
        partner_fields = models.execute_kw(
            db, uid, password, 'res.partner', 'fields_get',
            [], {'attributes': ['string']}
        )
        if 'x_lisa_wb_email_subject' in partner_fields:
            partner_vals['x_lisa_wb_email_subject'] = subject
        models.execute_kw(db, uid, password, 'res.partner', 'write', [[int(partner_id)], partner_vals])
        print(f"[Tools] Wrote win-back HTML draft to partner {partner_id} (Processed: {processed_val})")

        # Removed redundant winback.campaign creation logic to prevent duplicate records.
        # The update_campaign_lead tool handles this step exclusively.

        # Helper: post a clean note to chatter via direct mail.message create (bypasses XML-RPC HTML escaping)
        def _post_wb_chatter(body_html: str):
            subtypes = models.execute_kw(db, uid, password, 'mail.message.subtype', 'search_read', [
                [('name', '=', 'Note')]
            ], {'fields': ['id'], 'limit': 1})
            subtype_id = subtypes[0]['id'] if subtypes else None
            models.execute_kw(db, uid, password, 'mail.message', 'create', [{
                'model': 'res.partner',
                'res_id': int(partner_id),
                'body': body_html,
                'message_type': 'comment',
                'subtype_id': subtype_id
            }])

        # Step 2: If auto_reply is True, trigger the Odoo mail template to send immediately
        if config.AUTO_REPLY:
            search_name = 'Lisa Win-Back Campaign Outreach'
            templates = models.execute_kw(db, uid, password, 'mail.template', 'search_read', [
                [('name', '=', search_name)]
            ], {'fields': ['id'], 'limit': 1})
            
            if not templates:
                raise ToolException(f"Error: Mail template '{search_name}' not found in Odoo.")
            template_id_val = templates[0]['id']
            
            email_values = {}
            if config.RECIPIENT_OVERRIDE:
                email_values.update({
                    'email_to': config.RECIPIENT_OVERRIDE,
                    'subject': f"{subject} [To: {customer_email}]",
                    'partner_ids': [(6, 0, [])],
                    'recipient_ids': [(6, 0, [])]
                })
            else:
                email_values['subject'] = subject
            
            if salesperson_name and salesperson_email:
                email_values['email_from'] = f'"{salesperson_name}" <{salesperson_email}>'

            # Send email using template
            try:
                models.execute_kw(db, uid, password, 'mail.template', 'send_mail',
                    [template_id_val, int(partner_id)],
                    {'force_send': True, 'email_values': email_values}
                )
            except Exception as send_err:
                if 'cannot marshal None' in str(send_err):
                    print('[Tools] Email triggered (ignored None-marshalling fault)')
                else:
                    raise send_err
            print(f"[Tools] Email sent automatically via template for partner {partner_id} (Sent to {sent_to})")

            # Determine stage number (1, 2, or 3) from campaign_stage_tag or derived tag
            stage_num = "1"
            if tag:
                tag_upper = tag.upper().strip()
                if "WB-2" in tag_upper or "WB2" in tag_upper:
                    stage_num = "2"
                elif "WB-3" in tag_upper or "WB3" in tag_upper:
                    stage_num = "3"
                elif "WB-1" in tag_upper or "WB1" in tag_upper:
                    stage_num = "1"

            # Chatter confirmation post removed by request to keep chatter feed clean
            pass

        else:
            # AUTO_REPLY=OFF — Manual Review mode
            # Determine stage number (1, 2, or 3) from campaign_stage_tag or derived tag
            stage_num = "1"
            if tag:
                tag_upper = tag.upper().strip()
                if "WB-2" in tag_upper or "WB2" in tag_upper:
                    stage_num = "2"
                elif "WB-3" in tag_upper or "WB3" in tag_upper:
                    stage_num = "3"
                elif "WB-1" in tag_upper or "WB1" in tag_upper:
                    stage_num = "1"

            # Chatter draft post removed by request to keep chatter feed clean
            print(f"[Tools] Draft compiled and saved for partner {partner_id}.")

        return {
            "success": True,
            "sent": bool(config.AUTO_REPLY),
            "message": "Email sent automatically via template." if config.AUTO_REPLY else "Email draft saved on customer record for manual review."
        }
    except Exception as e:
        print(f"Error sending email via Odoo: {e}")
        raise ToolException(f"Error sending email via Odoo: {e}")

@tool
def log_campaign_note(partner_id: int, message_body: str) -> dict:
    """Logs a campaign note/comment on the customer's chatter log in Odoo.
    
    Args:
        partner_id: The Odoo customer ID.
        message_body: Content of the note (supports HTML).
        
    Returns:
        A dictionary indicating success status.
    """
    try:
        models, uid, db, password = get_odoo_client()
        subtypes = models.execute_kw(db, uid, password, 'mail.message.subtype', 'search_read', [
            [('name', '=', 'Note')]
        ], {'fields': ['id'], 'limit': 1})
        subtype_id = subtypes[0]['id'] if subtypes else None

        models.execute_kw(db, uid, password, 'mail.message', 'create', [{
            'model': 'res.partner',
            'res_id': int(partner_id),
            'body': message_body,
            'message_type': 'comment',
            'subtype_id': subtype_id
        }])
        return {"success": True}
    except Exception as e:
        print(f"Error logging chatter note for partner {partner_id}: {e}")
        raise ToolException(f"Error logging chatter note for partner {partner_id}: {e}")

@tool
def schedule_partner_activity(
    partner_id: int,
    summary: str,
    note_html: str,
    assigned_user_id: int = None,
    days_to_deadline: int = 1
) -> dict:
    """Schedules a To-Do activity on the customer record in Odoo.
    
    Args:
        partner_id: The Odoo customer ID.
        summary: Activity summary (header).
        note_html: Detailed instructions/notes for the activity.
        assigned_user_id: Odoo User ID to assign to. Defaults to customer's salesperson.
        days_to_deadline: Number of days until deadline.
        
    Returns:
        A dictionary indicating success status.
    """
    try:
        models, uid, db, password = get_odoo_client()
        
        # If no user ID is explicitly passed, fetch the customer's salesperson ID
        if assigned_user_id is None:
            records = models.execute_kw(
                db, uid, password, 'res.partner', 'search_read',
                [[('id', '=', partner_id)]],
                {'fields': ['user_id']}
            )
            if records and records[0].get('user_id'):
                assigned_user_id = records[0]['user_id'][0]
            else:
                # Fallback to the API user
                assigned_user_id = uid
                
        # Calculate deadline date (e.g. today + days_to_deadline)
        deadline = (datetime.now(timezone.utc) + timedelta(days=days_to_deadline)).strftime('%Y-%m-%d')
        
        # Look up activity type dynamically
        activity_type_id = 4  # Default fallback
        try:
            activity_types = models.execute_kw(
                db, uid, password, 'mail.activity.type', 'search',
                [[('name', 'ilike', 'To-Do')]]
            )
            if activity_types:
                activity_type_id = activity_types[0]
            else:
                activity_types = models.execute_kw(
                    db, uid, password, 'mail.activity.type', 'search',
                    [[('category', '=', 'default')]]
                )
                if activity_types:
                    activity_type_id = activity_types[0]
        except Exception as lookup_err:
            print(f"Warning: Could not dynamically lookup activity type: {lookup_err}. Using default ID 4.")
        
        vals = {
            'res_model': 'res.partner',
            'res_id': partner_id,
            'activity_type_id': activity_type_id,
            'summary': summary,
            'note': note_html,
            'user_id': assigned_user_id,
            'date_deadline': deadline
        }
        
        activity_id = models.execute_kw(db, uid, password, 'mail.activity', 'create', [vals])
        print(f"Odoo activity scheduled successfully. Activity ID: {activity_id}")
        return {"success": True}
    except Exception as e:
        print(f"Error scheduling Odoo activity for partner {partner_id}: {e}")
        raise ToolException(f"Error scheduling Odoo activity for partner {partner_id}: {e}")
@tool
def get_customer_purchased_categories(partner_id: int) -> list:
    """Queries Odoo to find the product categories historically purchased by the customer,
    ranked by purchase frequency in descending order.
    
    Args:
        partner_id: The Odoo customer ID.
        
    Returns:
        A list of product category names purchased by the customer.
    """
    try:
        models, uid, db, password = get_odoo_client()
        lines = models.execute_kw(db, uid, password, 'sale.order.line', 'search_read', [[
            ('order_id.partner_id', '=', partner_id),
            ('order_id.state', 'in', ['sale', 'done'])
        ]], {'fields': ['product_id']})
        if not lines:
            return []
        
        product_ids = list(set(line['product_id'][0] for line in lines if line.get('product_id')))
        if not product_ids:
            return []
            
        products = models.execute_kw(db, uid, password, 'product.product', 'read', [
            product_ids
        ], {'fields': ['categ_id']})
        
        categories = []
        for p in products:
            categ = p.get('categ_id')
            if categ:
                categories.append(categ[1])
                
        # Count category occurrences and sort by frequency descending
        counter = Counter(categories)
        sorted_categories = [cat for cat, count in counter.most_common()]
        return sorted_categories
    except Exception as e:
        print(f"Error fetching purchased categories for partner {partner_id}: {e}")
        raise ToolException(f"Error fetching purchased categories for partner {partner_id}: {e}")


@tool
def blacklist_partner_in_odoo(email: str) -> dict:
    """Adds the given email to Odoo's global email blacklist.
    
    Args:
        email: The email address to blacklist.
        
    Returns:
        A dictionary indicating success status.
    """
    try:
        models, uid, db, password = get_odoo_client()
        exists = models.execute_kw(db, uid, password, 'mail.blacklist', 'search', [[('email', '=', email)]])
        if exists:
            print(f"Email {email} is already in the blacklist.")
            return {"success": True}
        res = models.execute_kw(db, uid, password, 'mail.blacklist', 'create', [{'email': email}])
        print(f"Email {email} added to blacklist. Record ID: {res}")
        return {"success": True}
    except Exception as e:
        print(f"Error blacklisting email {email} in Odoo: {e}")
        raise ToolException(f"Error blacklisting email {email} in Odoo: {e}")


@tool
def check_recent_outreach(partner_id: int, days_limit: int = 7) -> dict:
    """Checks Odoo mail.message logs for any outgoing campaign or salesperson emails sent to this customer in the last days_limit days.
    
    Args:
        partner_id: The Odoo customer ID.
        days_limit: Number of days to check for recent outreach (default 7).
        
    Returns:
        A dictionary with keys 'has_recent_outreach' (bool) and 'last_outreach_date' (str or None).
    """
    try:
        models, uid, db, password = get_odoo_client()
        
        # Fetch customer email to avoid counting customer's own emails
        partner_data = models.execute_kw(db, uid, password, 'res.partner', 'read', [[partner_id]], {'fields': ['email']})
        partner_email = partner_data[0].get('email') if partner_data else None
        partner_email_lower = partner_email.strip().lower() if partner_email else None
        
        since_date = (datetime.now(timezone.utc) - timedelta(days=days_limit)).strftime('%Y-%m-%d %H:%M:%S')
        
        # Query mail.message across all models where the customer is in partner_ids
        domain = [
            ('message_type', '=', 'email'),
            ('date', '>', since_date),
            ('partner_ids', 'in', [partner_id])
        ]
        fields = ['id', 'author_id', 'email_from', 'date']
        messages = models.execute_kw(db, uid, password, 'mail.message', 'search_read', [domain], {'fields': fields})
        
        newest_date = None
        for msg in messages:
            author_info = msg.get('author_id')
            author_id = author_info[0] if author_info else None
            email_from = msg.get('email_from') or ''
            
            # If the author matches partner_id or sender email matches customer, skip (it's inbound)
            if author_id == partner_id:
                continue
            if partner_email_lower and partner_email_lower in email_from.lower():
                continue
            
            msg_date = msg.get('date')
            if not newest_date or msg_date > newest_date:
                newest_date = msg_date
                
        if newest_date:
            return {'has_recent_outreach': True, 'last_outreach_date': newest_date}
        return {'has_recent_outreach': False, 'last_outreach_date': None}
    except Exception as e:
        print(f"Error checking recent outreach for partner {partner_id}: {e}")
        raise ToolException(f"Error checking recent outreach for partner {partner_id}: {e}")


_crm_lead_model_exists = None

@tool
def check_suppression_criteria(partner_id: int) -> dict:
    """Checks if the customer should be suppressed from the win-back campaign (e.g. VIP tag, or active CRM opportunity).
    
    Args:
        partner_id: The Odoo customer ID.
        
    Returns:
        A dictionary with keys 'suppressed' (bool) and 'reason' (str).
    """
    try:
        models, uid, db, password = get_odoo_client()
        
        # 1. Check partner categories/tags (category_id field points to res.partner.category)
        partner_data = models.execute_kw(db, uid, password, 'res.partner', 'read', [[partner_id]], {'fields': ['category_id']})
        if partner_data:
            tag_ids = partner_data[0].get('category_id', [])
            if tag_ids:
                tags = models.execute_kw(db, uid, password, 'res.partner.category', 'read', [tag_ids], {'fields': ['name']})
                for t in tags:
                    name = t.get('name', '').lower()
                    if any(term in name for term in ['vip', 'no contact', 'suppress']):
                        return {'suppressed': True, 'reason': f"Customer has tag: {t.get('name')}"}
                         
        # 2. Check for active CRM opportunities
        global _crm_lead_model_exists
        if _crm_lead_model_exists is None:
            try:
                model_ids = models.execute_kw(db, uid, password, 'ir.model', 'search', [[('model', '=', 'crm.lead')]])
                _crm_lead_model_exists = bool(model_ids)
            except Exception:
                _crm_lead_model_exists = False

        if _crm_lead_model_exists:
            try:
                opps = models.execute_kw(db, uid, password, 'crm.lead', 'search_read', [
                    [('partner_id', '=', partner_id), ('type', '=', 'opportunity'), ('active', '=', True), ('probability', '>', 0), ('probability', '<', 100)]
                ], {'fields': ['name', 'probability']})
                if opps:
                    return {'suppressed': True, 'reason': f"Active CRM opportunity: {opps[0]['name']} (Probability: {opps[0]['probability']}%)"}
            except Exception as crm_err:
                print(f"[Tools] Warning: crm.lead model not accessible: {crm_err}. Skipping active opportunities check.")
            
        return {'suppressed': False, 'reason': ''}
    except Exception as e:
        print(f"Error checking suppression criteria for partner {partner_id}: {e}")
        raise ToolException(f"Error checking suppression criteria for partner {partner_id}: {e}")



@tool
def get_company_details() -> dict:
    """Queries Odoo to retrieve the main company details (name, email, phone, website).
    
    Returns:
        A dictionary with the company details.
    """
    try:
        models, uid, db, password = get_odoo_client()
        companies = models.execute_kw(
            db, uid, password, 'res.company', 'search_read',
            [[]],
            {'fields': ['name', 'email', 'phone', 'website'], 'limit': 1}
        )
        if companies:
            c = companies[0]
            return {
                'name': c.get('name') or 'Our Company',
                'email': c.get('email') or '',
                'phone': c.get('phone') or '',
                'website': c.get('website') or ''
            }
        return {'name': 'Our Company', 'email': '', 'phone': '', 'website': ''}
    except Exception as e:
        print(f"Error fetching company details: {e}")
        raise ToolException(f"Error fetching company details: {e}")


@tool
def get_salesperson_details(salesperson_id: Any = None, name: Any = None, email: Any = None) -> dict:
    """Queries Odoo to retrieve the salesperson's full name and email.
    
    Args:
        salesperson_id: The Odoo user ID of the salesperson (optional).
        name: The name of the salesperson (optional fallback).
        email: The email of the salesperson (optional fallback).
        
    Returns:
        A dictionary with the salesperson's name and email.
    """
    try:
        # If name or email are passed (due to LLM hallucinating args), return them
        if name or email:
            return {
                'name': name or 'Sales Representative',
                'email': email or ''
            }
            
        if not salesperson_id:
            return {'name': 'Sales Representative', 'email': ''}
            
        if isinstance(salesperson_id, dict):
            return {
                'name': salesperson_id.get('name') or 'Sales Representative',
                'email': salesperson_id.get('email') or ''
            }
            
        if isinstance(salesperson_id, list) and salesperson_id:
            salesperson_id = salesperson_id[0]
            
        try:
            sp_id = int(str(salesperson_id).strip())
        except (ValueError, TypeError):
            return {'name': 'Sales Representative', 'email': ''}
            
        models, uid, db, password = get_odoo_client()
        users = models.execute_kw(
            db, uid, password, 'res.users', 'read',
            [[sp_id]],
            {'fields': ['partner_id']}
        )
        if users and users[0].get('partner_id'):
            p_id = users[0]['partner_id'][0]
            partner_records = models.execute_kw(
                db, uid, password, 'res.partner', 'read',
                [[p_id]],
                {'fields': ['name', 'email', 'phone']}
            )
            if partner_records:
                p_rec = partner_records[0]
                name = p_rec.get('name') or 'Sales Representative'
                if "," in name:
                    name = name.split(",")[-1].strip()
                return {
                    'name': name,
                    'email': p_rec.get('email') or '',
                    'phone': p_rec.get('phone') or ''
                }
        return {'name': 'Sales Representative', 'email': '', 'phone': ''}
    except Exception as e:
        print(f"Error fetching salesperson details: {e}")
        raise ToolException(f"Error fetching salesperson details: {e}")
_todo_cache = {}

@tool
def clear_todo_list(partner_id: int) -> dict:
    """Clears the checklist cache for the given partner ID.
    
    Args:
        partner_id: The Odoo customer ID.
    """
    global _todo_cache
    if partner_id in _todo_cache:
        del _todo_cache[partner_id]
    return {"success": True}

def reconstruct_lead_state_from_odoo(partner_id: int) -> dict:
    try:
        models, uid, db, password = get_odoo_client()
        
        # 1. Fetch partner basic info
        partner_fields = ['name', 'email', 'user_id', 'active', 'lang', 'country_id']
        try:
            res_partner_fields = models.execute_kw(db, uid, password, 'res.partner', 'fields_get', [], {'attributes': []})
            if 'x_lisa_wb_email_html' in res_partner_fields:
                partner_fields.append('x_lisa_wb_email_html')
            if 'lisa_wb_processed' in res_partner_fields:
                partner_fields.append('lisa_wb_processed')
        except Exception:
            pass
        partner_data = models.execute_kw(db, uid, password, 'res.partner', 'read', [[partner_id]], {'fields': partner_fields})
        if not partner_data:
            return {}
        p = partner_data[0]
        
        salesperson_id = p.get('user_id')[0] if p.get('user_id') else None
        email = p.get('email') or ''
        name = p.get('name') or ''
        active = p.get('active', True)
        lang = p.get('lang') or 'en_US'
        country_info = p.get('country_id')
        country_name = country_info[1] if (country_info and len(country_info) > 1) else None
        
        # Check global blacklist
        is_blacklisted = False
        if email:
            blacklist_domain = [('email', '=', email), ('active', '=', True)]
            blacklist_records = models.execute_kw(db, uid, password, 'mail.blacklist', 'search_count', [blacklist_domain])
            is_blacklisted = blacklist_records > 0
            
        # 2. Determine last confirmed order date
        order_domain = [
            ('partner_id', '=', partner_id),
            ('state', 'in', ['sale', 'done'])
        ]
        orders = models.execute_kw(db, uid, password, 'sale.order', 'search_read', [order_domain], {'fields': ['date_order'], 'limit': 1, 'order': 'date_order desc'})
        last_order_date = orders[0].get('date_order') if orders else None
        
        # 3. Retrieve campaign details directly from the Odoo winback.campaign model
        try:
            campaign_records = models.execute_kw(db, uid, password, 'winback.campaign', 'search_read', [
                [('partner_id', '=', partner_id)]
            ], {'fields': ['stage', 'status', 'suppression_reason', 'email_1_sent_date', 'email_2_sent_date', 'email_3_sent_date'], 'limit': 1})
        except Exception as e:
            print(f"[Tools] Warning: winback.campaign model not accessible: {e}. Defaulting to empty campaign state.")
            campaign_records = []
        
        campaign_stage = 'none'
        status = 'active'
        suppression_reason = None
        last_email_sent_date = None
        next_email_date = None
        
        if campaign_records:
            rec = campaign_records[0]
            campaign_stage = rec.get('stage') or 'none'
            status = rec.get('status') or 'active'
            suppression_reason = rec.get('suppression_reason')
            
            # Find the most recent sent date
            sent_dates = []
            for field in ['email_1_sent_date', 'email_2_sent_date', 'email_3_sent_date']:
                val = rec.get(field)
                if val:
                    try:
                        sent_dates.append(parse_odoo_date(val))
                    except Exception:
                        pass
            if sent_dates:
                # Get the latest datetime
                latest_dt = max(sent_dates)
                last_email_sent_date = latest_dt.isoformat()
                
                # Calculate next email date
                load_odoo_company_config()
                interval = getattr(config, 'WINBACK_INTERVAL_DAYS', 7)
                next_dt = latest_dt + timedelta(days=interval)
                next_email_date = next_dt.isoformat()
                
                max_emails = getattr(config, 'MAX_WINBACK_EMAILS', 3)
                stage_to_index = {
                    'email_1_sent': 1,
                    'email_2_sent': 2,
                    'email_3_sent': 3,
                }
                curr_index = stage_to_index.get(campaign_stage, 0)
                if curr_index >= max_emails and datetime.now(timezone.utc) >= next_dt:
                    status = 'cold'
                    
        # 4. Fallback checks
        if is_blacklisted:
            status = 'opt_out'
        elif not active:
            status = 'cold'
            
        # Check reactivated: new confirmed order since last_email_sent_date (or campaign enrollment)
        since_date = last_email_sent_date if last_email_sent_date else last_order_date
        if since_date:
            odoo_since_date = format_date_for_odoo(since_date)
            new_order_domain = [
                ('partner_id', '=', partner_id),
                ('state', 'in', ['sale', 'done']),
                ('date_order', '>', odoo_since_date)
            ]
            new_orders_count = models.execute_kw(db, uid, password, 'sale.order', 'search_count', [new_order_domain])
            if new_orders_count > 0:
                status = 'reactivated'
                campaign_stage = 'none'
                
        has_pending_draft = False
        if p.get('x_lisa_wb_email_html') and not p.get('lisa_wb_processed'):
            has_pending_draft = True

        return {
            'partner_id': partner_id,
            'partner_name': name,
            'email': email,
            'salesperson_id': salesperson_id,
            'last_order_date': last_order_date,
            'campaign_stage': campaign_stage,
            'last_email_sent_date': last_email_sent_date,
            'next_email_date': next_email_date,
            'status': status,
            'is_blacklisted': 1 if is_blacklisted else 0,
            'suppressed': 1 if status == 'suppressed' else 0,
            'suppression_reason': suppression_reason,
            'lang': lang,
            'country': country_name,
            'has_pending_draft': has_pending_draft
        }
    except Exception as e:
        print(f"Error reconstructing lead state from Odoo for {partner_id}: {e}")
        return {}


@tool
def get_campaign_lead(partner_id: int) -> dict:
    """Retrieves the campaign lead state.
    
    Args:
        partner_id: The Odoo customer ID.
        
    Returns:
        A dictionary with the lead's state.
    """
    return reconstruct_lead_state_from_odoo(partner_id)


@tool
def update_campaign_lead(
    partner_id: int,
    campaign_stage: str = None,
    last_email_sent_date: str = None,
    next_email_date: str = None,
    status: str = None,
    is_blacklisted: int = None,
    suppressed: int = None,
    suppression_reason: str = None
) -> dict:
    """Updates the campaign lead state natively in Odoo's winback.campaign model.
    
    Args:
        partner_id: The Odoo customer ID.
        campaign_stage: The new campaign stage.
        last_email_sent_date: ISO UTC datetime string of the last email sent.
        next_email_date: ISO UTC datetime string of when the next email can be sent.
        status: The lead status.
        is_blacklisted: 1 if blacklisted, 0 otherwise.
        suppressed: 1 if suppressed, 0 otherwise.
        suppression_reason: Reason for suppression.
        
    Returns:
        A dictionary indicating success status.
    """
    try:
        models, uid, db, password = get_odoo_client()
        
        # 1. Log update note to customer chatter (disabled to avoid chatter clutter)
        # log_msg = []
        # if campaign_stage:
        #     log_msg.append(f"Campaign Stage updated to: {campaign_stage}")
        # if status:
        #     log_msg.append(f"Campaign Status updated to: {status}")
        # if suppression_reason:
        #     log_msg.append(f"Suppression Reason: {suppression_reason}")
        #     
        # if log_msg:
        #     try:
        #         log_campaign_note.invoke({"partner_id": partner_id, "message_body": f"<b>Win-Back Update:</b><br/>" + "<br/>".join(log_msg)})
        #     except Exception as e:
        #         print(f"Error logging state update note in Odoo: {e}")

        # 2. Write updates directly to Odoo winback.campaign model
        try:
            existing = models.execute_kw(db, uid, password, 'winback.campaign', 'search_read', [
                [('partner_id', '=', int(partner_id))]
            ], {'fields': ['id'], 'limit': 1})
            
            vals = {}
            if campaign_stage is not None:
                vals['stage'] = campaign_stage
            
            # Sync date fields if last_email_sent_date is passed
            if last_email_sent_date and config.AUTO_REPLY:
                odoo_date = format_date_for_odoo(last_email_sent_date)
                if campaign_stage == 'email_1_sent':
                    vals['email_1_sent_date'] = odoo_date
                elif campaign_stage == 'email_2_sent':
                    vals['email_2_sent_date'] = odoo_date
                elif campaign_stage == 'email_3_sent':
                    vals['email_3_sent_date'] = odoo_date
            
            if status is not None:
                vals['status'] = status
                
            if suppression_reason is not None:
                vals['suppression_reason'] = suppression_reason
                
            if vals or not existing:
                if existing:
                    if vals:
                        models.execute_kw(db, uid, password, 'winback.campaign', 'write', [
                            [existing[0]['id']], vals
                        ])
                        print(f"[Tools] winback.campaign record updated in Odoo for partner {partner_id}")
                else:
                    vals['partner_id'] = int(partner_id)
                    if 'stage' not in vals or not vals['stage']:
                        vals['stage'] = 'none'
                    if 'status' not in vals or not vals['status']:
                        vals['status'] = 'draft' if not config.AUTO_REPLY else 'active'
                    models.execute_kw(db, uid, password, 'winback.campaign', 'create', [vals])
                    print(f"[Tools] winback.campaign record created in Odoo for partner {partner_id}")
        except Exception as e:
            print(f"[Tools] Warning: Could not sync campaign state back to winback.campaign model: {e}")
            
        return {"success": True}
    except Exception as e:
        print(f"Error updating campaign lead state in Odoo: {e}")
        raise ToolException(f"Error updating campaign lead state in Odoo: {e}")


@tool
def save_customer_memory(partner_id: int, memory_text: str) -> dict:
    """Saves a new memory/interaction note for the customer to persistent memory in Odoo's res.partner internal notes.
    
    Args:
        partner_id: The Odoo customer ID.
        memory_text: Summary of the memory/note to persist.
        
    Returns:
        A dictionary indicating success status.
    """
    now_str = datetime.now(timezone.utc).isoformat()
    formatted_note = f"\n- {now_str[:10]}: {memory_text}"
    try:
        models, uid, db, password = get_odoo_client()
        records = models.execute_kw(
            db, uid, password, 'res.partner', 'read',
            [[partner_id]],
            {'fields': ['comment']}
        )
        existing_comment = ""
        if records and records[0].get('comment'):
            existing_comment = records[0]['comment']
            
        header = "[Win-Back Memory Log]"
        if header not in existing_comment:
            new_comment = f"{existing_comment}\n\n{header}{formatted_note}".strip()
        else:
            new_comment = f"{existing_comment}{formatted_note}".strip()
            
        models.execute_kw(
            db, uid, password, 'res.partner', 'write',
            [[partner_id], {'comment': new_comment}]
        )
        print(f"Memory saved natively in Odoo for partner {partner_id}: {memory_text}")
        return {"success": True}
    except Exception as e:
        print(f"Error saving Odoo native memory for partner {partner_id}: {e}")
        raise ToolException(f"Error saving Odoo native memory for partner {partner_id}: {e}")


@tool
def get_customer_memories(partner_id: int) -> str:
    """Retrieves all past memory logs and notes for the customer from Odoo's res.partner internal notes.
    
    Args:
        partner_id: The Odoo customer ID.
        
    Returns:
        A string containing all past memory logs.
    """
    try:
        models, uid, db, password = get_odoo_client()
        records = models.execute_kw(
            db, uid, password, 'res.partner', 'read',
            [[partner_id]],
            {'fields': ['comment']}
        )
        if records and records[0].get('comment'):
            comment_text = records[0]['comment']
            header = "[Win-Back Memory Log]"
            if header in comment_text:
                parts = comment_text.split(header)
                return header + parts[-1]
            return ""
        return ""
    except Exception as e:
        print(f"Error reading Odoo native memory for partner {partner_id}: {e}")
        raise ToolException(f"Error reading Odoo native memory for partner {partner_id}: {e}")


@tool
def manage_todo_list(partner_id: int, action: str, task_name: str = None, status: str = None) -> str:
    """Manages the orchestrator's checklist for processing a customer's campaign.
    
    Args:
        partner_id: The Odoo customer ID.
        action: One of 'get' (retrieves checklist), 'add' (adds a task), or 'update' (updates a task's status).
        task_name: The name of the task/step.
        status: The new status of the task ('pending' or 'completed').
        
    Returns:
        A string representing the status or list of tasks.
    """
    global _todo_cache
    now_str = datetime.now(timezone.utc).isoformat()
    
    try:
        if action == 'get':
            rows = _todo_cache.get(partner_id)
            if not rows:
                default_tasks = [
                    'check_eligibility',
                    'check_suppression',
                    'check_memories',
                    'check_reactivation',
                    'check_replies',
                    'draft_email',
                    'send_email',
                    'log_notes'
                ]
                rows = {task: 'pending' for task in default_tasks}
                _todo_cache[partner_id] = rows
            
            checklist = [f"- [{ 'x' if s == 'completed' else ' ' }] {task}" for task, s in rows.items()]
            return "\n".join(checklist)
            
        elif action == 'add':
            if not task_name:
                raise ToolException("Task name is required for action='add'")
            if partner_id not in _todo_cache:
                _todo_cache[partner_id] = {}
            _todo_cache[partner_id][task_name] = status or 'pending'
            return f"Task '{task_name}' added successfully."
            
        elif action == 'update':
            if not task_name or not status:
                raise ToolException("Task name and status are required for action='update'")
            if partner_id not in _todo_cache:
                _todo_cache[partner_id] = {}
            _todo_cache[partner_id][task_name] = status
            return f"Task '{task_name}' updated to '{status}'."
            
        else:
            raise ToolException(f"Unsupported action: {action}")
            
    except Exception as e:
        print(f"Error in manage_todo_list for partner {partner_id}: {e}")
        raise ToolException(f"Error in manage_todo_list: {e}")
