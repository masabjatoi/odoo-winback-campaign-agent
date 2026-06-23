import os
import xmlrpc.client
import sqlite3
import smtplib
import socket
import json
# Set default socket timeout of 90 seconds to prevent XML-RPC calls from hanging indefinitely
socket.setdefaulttimeout(90)
from collections import Counter
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone
from typing import Any
from langchain_core.tools import tool, ToolException

import config

# Odoo XML-RPC connection caching with TTL (1 hour)
_cached_uid = None
_cached_uid_time = None
CACHE_TTL_SECONDS = 3600

def get_odoo_client():
    """Connects to Odoo and returns (models_proxy, uid, db, password), caching uid to optimize overhead."""
    global _cached_uid, _cached_uid_time
    now = datetime.now(timezone.utc)
    if (_cached_uid is None or _cached_uid_time is None or 
        (now - _cached_uid_time).total_seconds() > CACHE_TTL_SECONDS):
        try:
            common = xmlrpc.client.ServerProxy(f'{config.ODOO_URL}/xmlrpc/2/common')
            _cached_uid = common.authenticate(config.ODOO_DB, config.ODOO_USERNAME, config.ODOO_API_KEY, {})
            _cached_uid_time = now
        except Exception as auth_err:
            _cached_uid = None
            _cached_uid_time = None
            raise ToolException(f"Odoo authentication failed: {auth_err}")
            
    models = xmlrpc.client.ServerProxy(f'{config.ODOO_URL}/xmlrpc/2/object')
    return models, _cached_uid, config.ODOO_DB, config.ODOO_API_KEY


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
        for p in partner_details:
            pid = p['id']
            email = (p.get('email') or '').strip().lower()
            if email in blacklisted_emails:
                continue
                
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
    salesperson_email: str = None
) -> dict:
    """Sends a win-back outreach email to the customer. 
    If TEST_MODE is enabled, bypasses Odoo and routes via Gmail SMTP to the test address.
    
    Args:
        partner_id: The Odoo customer ID.
        customer_name: Customer's name.
        customer_email: Customer's email.
        subject: Subject of the email.
        body_html: HTML content of the email body.
        salesperson_name: Salesperson's full name.
        salesperson_email: Salesperson's email address.
        
    Returns:
        A dictionary indicating success status.
    """
    if config.TEST_MODE:
        print(f"[TEST MODE] Sending email via Gmail SMTP for partner {partner_id} ({customer_name})...")
        print(f"[TEST MODE] Sender: {config.GMAIL_SMTP_USER} | Recipient: {config.TEST_EMAIL_TO}")
        
        server = None
        try:
            # Construct email
            msg = MIMEMultipart()
            msg['From'] = f'"{salesperson_name}" <{config.GMAIL_SMTP_USER}>' if salesperson_name else config.GMAIL_SMTP_USER
            msg['To'] = config.TEST_EMAIL_TO
            msg['Subject'] = f"[TEST] {subject} (Intended for: {customer_name} <{customer_email}>)"
            
            # Add footer to denote test mode
            full_body = body_html + f"<br/><br/><hr/><i>[This test email was sent via Gmail SMTP. Intended recipient: {customer_name} &lt;{customer_email}&gt; (Odoo ID: {partner_id})]</i>"
            msg.attach(MIMEText(full_body, 'html'))
            
            # Connect and send
            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()
            server.login(config.GMAIL_SMTP_USER, config.GMAIL_SMTP_APP_PASSWORD)
            server.send_message(msg)
            
            print(f"[TEST MODE] Email successfully sent to {config.TEST_EMAIL_TO}")
            return {"success": True}
        except Exception as e:
            print(f"[TEST MODE] Failed to send Gmail SMTP email: {e}")
            raise ToolException(f"[TEST MODE] Failed to send Gmail SMTP email: {e}")
        finally:
            if server is not None:
                try:
                    server.quit()
                except Exception:
                    pass
    else:
        # Production mode - Write/send via Odoo mail.mail
        try:
            models, uid, db, password = get_odoo_client()
            
            # Format sender email
            email_from = config.ODOO_USERNAME
            if salesperson_name and salesperson_email:
                email_from = f'"{salesperson_name}" <{salesperson_email}>'
                
            vals = {
                'email_to': customer_email,
                'email_from': email_from,
                'subject': subject,
                'body': body_html,
                'model': 'res.partner',
                'res_id': partner_id,
            }
            mail_id = models.execute_kw(db, uid, password, 'mail.mail', 'create', [vals])
            models.execute_kw(db, uid, password, 'mail.mail', 'send', [[mail_id]])
            print(f"Odoo email sent successfully. Mail ID: {mail_id}")
            return {"success": True}
        except Exception as e:
            print(f"Error sending email via Odoo mail.mail: {e}")
            raise ToolException(f"Error sending email via Odoo mail.mail: {e}")

@tool
def log_campaign_note(partner_id: int, message_body: str) -> dict:
    """Logs a campaign note/comment on the customer's chatter log in Odoo.
    If TEST_MODE is enabled, prints to console and bypasses Odoo writes.
    
    Args:
        partner_id: The Odoo customer ID.
        message_body: Content of the note (supports HTML).
        
    Returns:
        A dictionary indicating success status.
    """
    if config.TEST_MODE:
        print(f"[TEST MODE] Would log Odoo chatter note on Partner {partner_id}:")
        print(f"            {message_body}")
        return {"success": True}
    try:
        models, uid, db, password = get_odoo_client()
        models.execute_kw(
            db, uid, password, 'res.partner', 'message_post',
            [[partner_id]],
            {'body': message_body, 'message_type': 'comment'}
        )
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
    If TEST_MODE is enabled, prints to console and bypasses Odoo writes.
    
    Args:
        partner_id: The Odoo customer ID.
        summary: Activity summary (header).
        note_html: Detailed instructions/notes for the activity.
        assigned_user_id: Odoo User ID to assign to. Defaults to customer's salesperson.
        days_to_deadline: Number of days until deadline.
        
    Returns:
        A dictionary indicating success status.
    """
    if config.TEST_MODE:
        print(f"[TEST MODE] Would schedule Odoo activity on Partner {partner_id}:")
        print(f"            Summary: {summary}")
        print(f"            Assigned to: {assigned_user_id}")
        print(f"            Deadline offset: {days_to_deadline} days")
        print(f"            Note: {note_html}")
        return {"success": True}
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
        order_ids = models.execute_kw(db, uid, password, 'sale.order', 'search', [[
            ('partner_id', '=', partner_id),
            ('state', 'in', ['sale', 'done'])
        ]])
        if not order_ids:
            return []
        
        lines = models.execute_kw(db, uid, password, 'sale.order.line', 'search_read', [
            [('order_id', 'in', order_ids)]
        ], {'fields': ['product_id']})
        
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
    If TEST_MODE is enabled, prints to console and bypasses Odoo writes.
    
    Args:
        email: The email address to blacklist.
        
    Returns:
        A dictionary indicating success status.
    """
    if config.TEST_MODE:
        print(f"[TEST MODE] Would add email to Odoo global blacklist: {email}")
        return {"success": True}
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
def check_recent_outreach(partner_id: int, days_limit: int = 7) -> bool:
    """Checks Odoo mail.message logs for any outgoing campaign or salesperson emails sent to this customer in the last days_limit days.
    
    Args:
        partner_id: The Odoo customer ID.
        days_limit: Number of days to check for recent outreach (default 7).
        
    Returns:
        True if there was a recent email outreach, False otherwise.
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
        
        for msg in messages:
            author_info = msg.get('author_id')
            author_id = author_info[0] if author_info else None
            email_from = msg.get('email_from') or ''
            
            # If the author matches partner_id or sender email matches customer, skip (it's inbound)
            if author_id == partner_id:
                continue
            if partner_email_lower and partner_email_lower in email_from.lower():
                continue
            
            # Found an outreach email sent to the customer
            return True
        return False
    except Exception as e:
        print(f"Error checking recent outreach for partner {partner_id}: {e}")
        raise ToolException(f"Error checking recent outreach for partner {partner_id}: {e}")


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
        opps = models.execute_kw(db, uid, password, 'crm.lead', 'search_read', [
            [('partner_id', '=', partner_id), ('type', '=', 'opportunity'), ('active', '=', True), ('probability', '>', 0), ('probability', '<', 100)]
        ], {'fields': ['name', 'probability']})
        if opps:
            return {'suppressed': True, 'reason': f"Active CRM opportunity: {opps[0]['name']} (Probability: {opps[0]['probability']}%)"}
            
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
def get_salesperson_details(salesperson_id: Any = None, **kwargs) -> dict:
    """Queries Odoo to retrieve the salesperson's full name and email.
    
    Args:
        salesperson_id: The Odoo user ID of the salesperson (optional).
        
    Returns:
        A dictionary with the salesperson's name and email.
    """
    try:
        # If name or email are passed as kwargs (due to LLM hallucinating args), return them
        if kwargs and ('name' in kwargs or 'email' in kwargs):
            return {
                'name': kwargs.get('name') or 'Sales Representative',
                'email': kwargs.get('email') or ''
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
            {'fields': ['name', 'email']}
        )
        if users:
            u = users[0]
            return {
                'name': u.get('name') or 'Sales Representative',
                'email': u.get('email') or ''
            }
        return {'name': 'Sales Representative', 'email': ''}
    except Exception as e:
        print(f"Error fetching salesperson details: {e}")
        raise ToolException(f"Error fetching salesperson details: {e}")
# Local JSON state file for TEST_MODE
TEST_STATE_FILE = "campaign_test_state.json"
_todo_cache = {}

def _read_test_state() -> dict:
    if os.path.exists(TEST_STATE_FILE):
        try:
            with open(TEST_STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _write_test_state(state: dict):
    try:
        with open(TEST_STATE_FILE, "w") as f:
            json.dump(state, f, indent=4)
    except Exception as e:
        print(f"Error writing test state to JSON: {e}")
@tool
def clear_todo_list(partner_id: int):
    """Clears the checklist cache for the given partner ID.
    
    Args:
        partner_id: The Odoo customer ID.
    """
    global _todo_cache
    if partner_id in _todo_cache:
        del _todo_cache[partner_id]

def reconstruct_lead_state_from_odoo(partner_id: int) -> dict:
    try:
        models, uid, db, password = get_odoo_client()
        
        # 1. Fetch partner basic info
        partner_fields = ['name', 'email', 'user_id', 'active', 'lang', 'country_id']
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
        
        # 3. Fetch Odoo chatter messages to identify campaign emails
        message_domain = [
            ('model', '=', 'res.partner'),
            ('res_id', '=', partner_id),
            ('message_type', '=', 'email')
        ]
        messages = models.execute_kw(db, uid, password, 'mail.message', 'search_read', [message_domain], {'fields': ['subject', 'body', 'date', 'author_id']})
        
        # Count sent campaign emails
        campaign_emails = []
        for msg in messages:
            subject = (msg.get('subject') or '').lower()
            body = (msg.get('body') or '').lower()
            
            author_info = msg.get('author_id')
            author_id = author_info[0] if author_info else None
            if author_id == partner_id:
                continue
                
            is_campaign = False
            stage_found = None
            if "friendly reminder" in subject or "checking in" in subject:
                is_campaign = True
                stage_found = 'email_1_sent'
            elif "welcome10" in body or "value-based" in subject or "special offer" in subject:
                is_campaign = True
                stage_found = 'email_2_sent'
            elif "final attempt" in subject or "last reminder" in subject or "stop reaching out" in body:
                is_campaign = True
                stage_found = 'email_3_sent'
                
            if is_campaign:
                campaign_emails.append({
                    'stage': stage_found,
                    'date': msg.get('date')
                })
        
        # Sort campaign emails by date ascending
        campaign_emails.sort(key=lambda x: x['date'])
        
        # Determine campaign stage and dates
        campaign_stage = 'none'
        last_email_sent_date = None
        next_email_date = None
        status = 'active'
        
        if campaign_emails:
            last_email = campaign_emails[-1]
            campaign_stage = last_email['stage']
            last_email_sent_date = last_email['date']
            
            last_dt = parse_odoo_date(last_email_sent_date)
            next_dt = last_dt + timedelta(days=7)
            next_email_date = next_dt.isoformat()
            
            if campaign_stage == 'email_3_sent' and datetime.now(timezone.utc) >= next_dt:
                status = 'cold'
        
        if is_blacklisted:
            status = 'opt_out'
        elif not active:
            status = 'cold'
            
        # Check reactivated: new confirmed order since last_email_sent_date (or campaign enrollment)
        since_date = last_email_sent_date if last_email_sent_date else last_order_date
        if since_date:
            new_order_domain = [
                ('partner_id', '=', partner_id),
                ('state', 'in', ['sale', 'done']),
                ('date_order', '>', since_date)
            ]
            new_orders_count = models.execute_kw(db, uid, password, 'sale.order', 'search_count', [new_order_domain])
            if new_orders_count > 0:
                status = 'reactivated'
                campaign_stage = 'none'
                
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
            'suppressed': 0,
            'suppression_reason': None,
            'lang': lang,
            'country': country_name
        }
    except Exception as e:
        print(f"Error reconstructing lead state from Odoo for {partner_id}: {e}")
        return {}


@tool
def get_campaign_lead(partner_id: int) -> dict:
    """Retrieves the campaign lead state.
    In TEST_MODE, reads from local JSON state.
    In production mode, dynamically reconstructs the state from Odoo.
    
    Args:
        partner_id: The Odoo customer ID.
        
    Returns:
        A dictionary with the lead's state.
    """
    if config.TEST_MODE:
        state_dict = _read_test_state()
        lead = state_dict.get(str(partner_id))
        if lead:
            return lead
        
        try:
            models, uid, db, password = get_odoo_client()
            partner_data = models.execute_kw(db, uid, password, 'res.partner', 'read', [[partner_id]], {'fields': ['name', 'email', 'user_id', 'lang', 'country_id']})
            if partner_data:
                p = partner_data[0]
                country_info = p.get('country_id')
                country_name = country_info[1] if (country_info and len(country_info) > 1) else None
                lead = {
                    'partner_id': partner_id,
                    'partner_name': p.get('name') or '',
                    'email': p.get('email') or '',
                    'salesperson_id': p.get('user_id')[0] if p.get('user_id') else None,
                    'last_order_date': None,
                    'campaign_stage': 'none',
                    'last_email_sent_date': None,
                    'next_email_date': datetime.now(timezone.utc).isoformat(),
                    'status': 'active',
                    'is_blacklisted': 0,
                    'suppressed': 0,
                    'suppression_reason': None,
                    'lang': p.get('lang') or 'en_US',
                    'country': country_name
                }
                state_dict[str(partner_id)] = lead
                _write_test_state(state_dict)
                return lead
        except Exception:
            pass
        return {}
    else:
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
    """Updates the campaign lead state.
    In TEST_MODE, writes to local JSON state.
    In production mode, updates Odoo or relies on Odoo chatter to dynamically derive state.
    
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
    if config.TEST_MODE:
        state_dict = _read_test_state()
        lead = state_dict.get(str(partner_id)) or {}
        lead['partner_id'] = partner_id
        if campaign_stage is not None:
            lead['campaign_stage'] = campaign_stage
        if last_email_sent_date is not None:
            lead['last_email_sent_date'] = last_email_sent_date
        if next_email_date is not None:
            lead['next_email_date'] = next_email_date
        if status is not None:
            lead['status'] = status
        if is_blacklisted is not None:
            lead['is_blacklisted'] = is_blacklisted
        if suppressed is not None:
            lead['suppressed'] = suppressed
        if suppression_reason is not None:
            lead['suppression_reason'] = suppression_reason
            
        state_dict[str(partner_id)] = lead
        _write_test_state(state_dict)
        return {"success": True}
    else:
        log_msg = []
        if campaign_stage:
            log_msg.append(f"Campaign Stage updated to: {campaign_stage}")
        if status:
            log_msg.append(f"Campaign Status updated to: {status}")
        if suppression_reason:
            log_msg.append(f"Suppression Reason: {suppression_reason}")
            
        if log_msg:
            try:
                log_campaign_note.invoke({"partner_id": partner_id, "message_body": f"<b>Win-Back Update:</b><br/>" + "<br/>".join(log_msg)})
            except Exception as e:
                print(f"Error logging state update note in Odoo: {e}")
        return {"success": True}


@tool
def save_customer_memory(partner_id: int, memory_text: str) -> dict:
    """Saves a new memory/interaction note for the customer to persistent memory.
    In TEST_MODE (true), saves to a local JSON state.
    In production mode (false), appends the note directly to Odoo's res.partner internal notes (comment field).
    
    Args:
        partner_id: The Odoo customer ID.
        memory_text: Summary of the memory/note to persist.
        
    Returns:
        A dictionary indicating success status.
    """
    now_str = datetime.now(timezone.utc).isoformat()
    
    if config.TEST_MODE:
        state_dict = _read_test_state()
        lead = state_dict.get(str(partner_id)) or {}
        memories = lead.get('memories') or []
        memories.append({
            'memory_text': memory_text,
            'created_at': now_str
        })
        lead['memories'] = memories
        state_dict[str(partner_id)] = lead
        _write_test_state(state_dict)
        print(f"[TEST MODE] Memory saved locally in JSON for partner {partner_id}: {memory_text}")
        return {"success": True}
    else:
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
    """Retrieves all past memory logs and notes for the customer.
    In TEST_MODE (true), reads from the local JSON state.
    In production mode (false), reads from Odoo's res.partner internal notes (comment field).
    
    Args:
        partner_id: The Odoo customer ID.
        
    Returns:
        A string containing all past memory logs.
    """
    if config.TEST_MODE:
        state_dict = _read_test_state()
        lead = state_dict.get(str(partner_id)) or {}
        memories = lead.get('memories') or []
        if not memories:
            return ""
        logs = [f"- {row['created_at'][:10]}: {row['memory_text']}" for row in memories]
        return "[Win-Back Memory Log]\n" + "\n".join(logs)
    else:
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
