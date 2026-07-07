import pytest
from unittest.mock import MagicMock, patch, mock_open
from datetime import datetime, timezone, timedelta
from graph import route_after_discovery, route_after_processing
from agent import discovery_node, process_lead_node

# 1. Test Router Logic
def test_router_logic():
    # Scenario A: Queue is not empty -> should go to process_lead
    state_a = {"leads_to_process": [{"partner_id": 12}]}
    assert route_after_discovery(state_a) == "process_lead"
    assert route_after_processing(state_a) == "process_lead"
    
    # Scenario B: Queue is empty -> should go to summary
    state_b = {"leads_to_process": []}
    assert route_after_discovery(state_b) == "summary"
    assert route_after_processing(state_b) == "summary"


# 2. Test discovery_node
@patch("agent.get_inactive_partners")
@patch("agent.get_campaign_lead")
def test_discovery_node(mock_get_lead, mock_get_inactive):
    mock_get_inactive.invoke.return_value = [
        {"id": 12, "name": "Customer A"},
        {"id": 15, "name": "Customer B"}
    ]
    # Mock lead states: ID 12 is active, ID 15 is completed
    mock_get_lead.invoke.side_effect = lambda args: {
        12: {"status": "active", "campaign_stage": "none"},
        15: {"status": "completed", "campaign_stage": "email_1_sent"}
    }.get(args.get("partner_id"))
    
    state = {}
    result = discovery_node(state)
    
    assert len(result["leads_to_process"]) == 1
    assert result["leads_to_process"][0]["partner_id"] == 12
    assert result["leads_to_process"][0]["partner_name"] == "Customer A"


# 3. Test process_lead_node - skipped case (not active)
@patch("agent.get_campaign_lead")
def test_process_lead_node_skipped_not_active(mock_get_lead):
    mock_get_lead.invoke.return_value = {"status": "completed"}
    
    state = {
        "leads_to_process": [{"partner_id": 12, "partner_name": "Customer A"}],
        "processed_leads": []
    }
    
    result = process_lead_node(state)
    
    assert len(result["processed_leads"]) == 1
    assert result["processed_leads"][0]["status"] == "skipped"
    assert "not active" in result["processed_leads"][0]["log"].lower()


# 4. Test process_lead_node - skipped case (next_email_date is in the future)
@patch("agent.get_campaign_lead")
def test_process_lead_node_skipped_future_date(mock_get_lead):
    future_date = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    mock_get_lead.invoke.return_value = {
        "status": "active",
        "campaign_stage": "email_1_sent",
        "next_email_date": future_date,
        "last_email_sent_date": datetime.now(timezone.utc).isoformat()
    }
    
    state = {
        "leads_to_process": [{"partner_id": 12, "partner_name": "Customer A"}],
        "processed_leads": []
    }
    
    result = process_lead_node(state)
    
    assert len(result["processed_leads"]) == 1
    assert result["processed_leads"][0]["status"] == "skipped"
    assert "not due yet" in result["processed_leads"][0]["log"].lower()


# 5. Test process_lead_node - skipped case (blacklisted in Odoo check)
@patch("agent.get_campaign_lead")
@patch("agent.check_partner_status")
@patch("agent.update_campaign_lead")
@patch("agent.log_campaign_note")
def test_process_lead_node_skipped_blacklisted(mock_log, mock_update, mock_partner_status, mock_get_lead):
    mock_get_lead.invoke.return_value = {"status": "active", "campaign_stage": "none"}
    mock_partner_status.invoke.return_value = {"active": True, "is_blacklisted": True}
    
    state = {
        "leads_to_process": [{"partner_id": 12, "partner_name": "Customer A"}],
        "processed_leads": []
    }
    
    result = process_lead_node(state)
    
    assert len(result["processed_leads"]) == 1
    assert result["processed_leads"][0]["status"] == "success"
    assert result["processed_leads"][0]["campaign_status"] == "opt_out"
    assert "blacklisted" in result["processed_leads"][0]["log"].lower()


# 6. Test process_lead_node - skipped case (memory objections)
@patch("agent.get_campaign_lead")
@patch("agent.check_partner_status")
@patch("agent.check_suppression_criteria")
@patch("agent.get_customer_memories")
@patch("agent.update_campaign_lead")
@patch("agent.log_campaign_note")
def test_process_lead_node_memory_objection(mock_log, mock_update, mock_get_memories, mock_suppress, mock_partner_status, mock_get_lead):
    mock_get_lead.invoke.return_value = {"status": "active", "campaign_stage": "none"}
    mock_partner_status.invoke.return_value = {"active": True, "is_blacklisted": False}
    mock_suppress.invoke.return_value = {"suppressed": False}
    mock_get_memories.invoke.return_value = "Customer switched to competitor."
    
    state = {
        "leads_to_process": [{"partner_id": 12, "partner_name": "Customer A"}],
        "processed_leads": []
    }
    
    result = process_lead_node(state)
    
    assert len(result["processed_leads"]) == 1
    assert result["processed_leads"][0]["status"] == "success"
    assert result["processed_leads"][0]["campaign_status"] == "cold"
    assert "memory objection" in result["processed_leads"][0]["log"].lower()


# 7. Test process_lead_node - reactivated via new purchase
@patch("agent.get_campaign_lead")
@patch("agent.check_partner_status")
@patch("agent.check_suppression_criteria")
@patch("agent.get_customer_memories")
@patch("agent.check_new_orders")
@patch("agent.update_campaign_lead")
@patch("agent.log_campaign_note")
@patch("agent.schedule_partner_activity")
def test_process_lead_node_reactivated(mock_activity, mock_log, mock_update, mock_check_orders, mock_get_memories, mock_suppress, mock_partner_status, mock_get_lead):
    mock_get_lead.invoke.return_value = {
        "status": "active", 
        "campaign_stage": "email_1_sent",
        "last_email_sent_date": (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(),
        "next_email_date": (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    }
    mock_partner_status.invoke.return_value = {"active": True, "is_blacklisted": False}
    mock_suppress.invoke.return_value = {"suppressed": False}
    mock_get_memories.invoke.return_value = ""
    mock_check_orders.invoke.return_value = [{"name": "SO999"}]
    
    state = {
        "leads_to_process": [{"partner_id": 12, "partner_name": "Customer A"}],
        "processed_leads": []
    }
    
    result = process_lead_node(state)
    
    assert len(result["processed_leads"]) == 1
    assert result["processed_leads"][0]["campaign_status"] == "reactivated"
    assert "reactivated via new order" in result["processed_leads"][0]["log"].lower()


# 8. Test process_lead_node - active processing (calls run_agent_for_lead)
@patch("agent.get_campaign_lead")
@patch("agent.check_partner_status")
@patch("agent.check_suppression_criteria")
@patch("agent.get_customer_memories")
@patch("agent.check_new_orders")
@patch("agent.check_customer_replies")
@patch("agent.check_recent_outreach")
@patch("agent.run_agent_for_lead")
def test_process_lead_node_active_run(mock_run_agent, mock_recent, mock_replies, mock_orders, mock_memories, mock_suppress, mock_partner_status, mock_get_lead):
    # Setup constraints to pass all checks
    mock_get_lead.invoke.side_effect = [
        {"status": "active", "campaign_stage": "none"},
        {"status": "active", "campaign_stage": "email_1_sent"}
    ]
    
    mock_partner_status.invoke.return_value = {"active": True, "is_blacklisted": False}
    mock_suppress.invoke.return_value = {"suppressed": False}
    mock_memories.invoke.return_value = ""
    mock_orders.invoke.return_value = []
    mock_replies.invoke.return_value = []
    mock_recent.invoke.return_value = {"has_recent_outreach": False}
    
    state = {
        "leads_to_process": [{"partner_id": 12, "partner_name": "Customer A"}],
        "processed_leads": []
    }
    
    # Patch load config inside node
    with patch("tools.load_odoo_company_config"):
        with patch("config.WINBACK_INTERVAL_DAYS", 7):
            result = process_lead_node(state)
            
            assert len(result["leads_to_process"]) == 0
            mock_run_agent.assert_called_once_with(12)
            assert len(result["processed_leads"]) == 1
            assert result["processed_leads"][0]["campaign_stage"] == "email_1_sent"
