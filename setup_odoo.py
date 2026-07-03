import os
import sys
from dotenv import load_dotenv

# Load env file
load_dotenv(override=True)

# Add current directory to python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import config
from odoo_client import OdooClient

def setup():
    print("=" * 60)
    print("Odoo Win-Back Customization Setup")
    print("=" * 60)
    
    # 1. Initialize Client
    client = OdooClient()
    try:
        client.authenticate()
    except Exception as e:
        print(f"[Error] Failed to authenticate with Odoo: {e}")
        sys.exit(1)
        
    # 2. Verify res.partner model
    try:
        model_ids = client.execute('ir.model', 'search', [[('model', '=', 'res.partner')]])
        if not model_ids:
            print("[Error] Could not find 'res.partner' model in Odoo.")
            sys.exit(1)
    except Exception as e:
        print(f"[Error] Failed to read ir.model: {e}")
        sys.exit(1)

    # 3. Configure Outgoing Mail Server (SMTP) using GMAIL_SMTP_USER (jatoimasab@gmail.com)
    gmail_user = os.getenv("GMAIL_SMTP_USER", "jatoimasab@gmail.com")
    gmail_pass = os.getenv("GMAIL_SMTP_APP_PASSWORD")
    
    if not gmail_pass or gmail_pass == "your_gmail_app_password":
        print("[Setup] [Warning] GMAIL_SMTP_APP_PASSWORD not set or using placeholder in .env. Please configure it to enable mail sending.")
    
    try:
        # Search for existing mail server with smtp_user = gmail_user
        existing_servers = client.execute('ir.mail_server', 'search_read', [
            [('smtp_user', '=', gmail_user)]
        ], {'fields': ['id']})
        
        server_vals = {
            'name': f'Gmail Win-Back Outgoing Server ({gmail_user})',
            'smtp_host': 'smtp.gmail.com',
            'smtp_port': 465,
            'smtp_encryption': 'ssl',
            'smtp_user': gmail_user,
            'smtp_pass': gmail_pass or '',
            'sequence': 10,  # Specific sequence to avoid collision
            'active': True
        }
        
        if existing_servers:
            server_id = existing_servers[0]['id']
            client.execute('ir.mail_server', 'write', [[server_id], server_vals])
            print(f"[OK] Outgoing SMTP server updated for '{gmail_user}' (ID: {server_id}).")
        else:
            server_id = client.execute('ir.mail_server', 'create', [server_vals])
            print(f"[Success] Outgoing SMTP server created for '{gmail_user}' (ID: {server_id}).")
            
    except Exception as e:
        print(f"[Error] Failed to configure outgoing SMTP mail server: {e}")

    # 4. Configure Incoming Mail Server (IMAP) using GMAIL_SMTP_USER
    try:
        existing_imap = client.execute('fetchmail.server', 'search_read', [
            [('user', '=', gmail_user)]
        ], {'fields': ['id']})
        
        imap_vals = {
            'name': f'Gmail Win-Back Incoming IMAP Server ({gmail_user})',
            'server_type': 'imap',
            'server': 'imap.gmail.com',
            'port': 993,
            'is_ssl': True,
            'user': gmail_user,
            'password': gmail_pass or '',
            'active': True
        }
        
        if existing_imap:
            imap_id = existing_imap[0]['id']
            client.execute('fetchmail.server', 'write', [[imap_id], imap_vals])
            print(f"[OK] Incoming IMAP server updated for '{gmail_user}' (ID: {imap_id}).")
        else:
            imap_id = client.execute('fetchmail.server', 'create', [imap_vals])
            print(f"[Success] Incoming IMAP server created for '{gmail_user}' (ID: {imap_id}).")
            
    except Exception as e:
        print(f"[Error] Failed to configure incoming IMAP mail server: {e}")

    # 5. Update module list & Install/Upgrade lisa_win_back_agent
    print("[Setup] Updating Odoo modules list...")
    try:
        client.execute('ir.module.module', 'update_list', [])
        print("[Setup] Modules list updated.")
        
        module = client.execute('ir.module.module', 'search_read', [
            [('name', '=', 'lisa_win_back_agent')]
        ], {'fields': ['id', 'state']})
        
        if module:
            m_id = module[0]['id']
            state = module[0]['state']
            if state in ('uninstalled', 'to install'):
                print("[Setup] Installing 'lisa_win_back_agent' module...")
                client.execute('ir.module.module', 'button_immediate_install', [[m_id]])
                print("[Success] Module 'lisa_win_back_agent' installed successfully!")
            else:
                print("[Setup] Upgrading 'lisa_win_back_agent' module...")
                client.execute('ir.module.module', 'button_immediate_upgrade', [[m_id]])
                print("[Success] Module 'lisa_win_back_agent' upgraded successfully!")
        else:
            print("[Warning] Module 'lisa_win_back_agent' not found in database. Make sure it is placed in Odoo's addons path.")
    except Exception as e:
        print(f"[Error] Failed to update/install Odoo module 'lisa_win_back_agent': {e}")
        
    print("\n" + "=" * 60)
    print("  Setup execution completed successfully!")
    print("  Note: Remember to restart the Odoo service for schema changes to take effect.")
    print("=" * 60)

if __name__ == "__main__":
    setup()
