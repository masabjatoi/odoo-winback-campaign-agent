import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from tools import get_odoo_client

def test_chatter():
    models, uid, db, password = get_odoo_client()
    partner_id = 7
    
    # Method 1: mail.message create with 'Note' name search
    subtypes = models.execute_kw(db, uid, password, 'mail.message.subtype', 'search_read', [
        [('name', '=', 'Note')]
    ], {'fields': ['id'], 'limit': 1})
    subtype_id_by_name = subtypes[0]['id'] if subtypes else None
    
    print(f"Subtype by name 'Note': {subtype_id_by_name}")
    
    try:
        msg_id1 = models.execute_kw(db, uid, password, 'mail.message', 'create', [{
            'model': 'res.partner',
            'res_id': partner_id,
            'body': '<b>Test 1:</b> mail.message create (Name Note)',
            'message_type': 'comment',
            'subtype_id': subtype_id_by_name
        }])
        print(f"Method 1 created msg: {msg_id1}")
    except Exception as e:
        print(f"Method 1 failed: {e}")

    # Method 2: mail.message create with 'mail.mt_note' XML ID
    model_data = models.execute_kw(db, uid, password, 'ir.model.data', 'search_read', [
        [('module', '=', 'mail'), ('name', '=', 'mt_note')]
    ], {'fields': ['res_id'], 'limit': 1})
    subtype_id_by_xml = model_data[0]['res_id'] if model_data else None
    print(f"Subtype by xml_id 'mail.mt_note': {subtype_id_by_xml}")

    try:
        msg_id2 = models.execute_kw(db, uid, password, 'mail.message', 'create', [{
            'model': 'res.partner',
            'res_id': partner_id,
            'body': '<b>Test 2:</b> mail.message create (XML ID Note)',
            'message_type': 'comment',
            'subtype_id': subtype_id_by_xml
        }])
        print(f"Method 2 created msg: {msg_id2}")
    except Exception as e:
        print(f"Method 2 failed: {e}")

    # Method 3: message_post
    try:
        msg_id3 = models.execute_kw(db, uid, password, 'res.partner', 'message_post',
            [[partner_id]],
            {'body': '<b>Test 3:</b> message_post', 'message_type': 'comment', 'subtype_xmlid': 'mail.mt_note'}
        )
        print(f"Method 3 created msg: {msg_id3}")
    except Exception as e:
        print(f"Method 3 failed: {e}")

if __name__ == "__main__":
    test_chatter()
