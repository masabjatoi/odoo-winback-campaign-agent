import os
import sys
import json
import re
import time
import threading
from datetime import datetime, timezone, timedelta
from langchain_core.messages import HumanMessage
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.callbacks import BaseCallbackHandler

# LangChain Model integrations
from langchain_mistralai import ChatMistralAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq

# Deep Agent framework imports
from deepagents import create_deep_agent
from langgraph.types import Command
from langgraph.checkpoint.memory import MemorySaver

# Configuration and prompts
import config
from prompt import SUMMARY_AGENT_INSTRUCTION

# Custom Odoo and local state tools
from tools import (
    get_campaign_lead,
    update_campaign_lead,
    check_partner_status,
    check_suppression_criteria,
    check_new_orders,
    check_customer_replies,
    send_winback_email,
    log_campaign_note,
    schedule_partner_activity,
    blacklist_partner_in_odoo,
    check_recent_outreach,
    get_customer_purchased_categories,
    get_inactive_partners,
    get_company_details,
    get_salesperson_details,
    save_customer_memory,
    get_customer_memories,
    manage_todo_list,
    clear_todo_list
)
import html
from html.parser import HTMLParser

STAGE_LABELS = {
    'none': 'No Email Sent',
    'email_1_sent': 'Email 1 Sent',
    'email_2_sent': 'Email 2 Sent',
    'email_3_sent': 'Email 3 Sent',
    'replied': 'Replied',
    'reactivated': 'Reactivated',
    'cold': 'Cold',
    'opt_out': 'Opt Out'
}

STATUS_LABELS = {
    'not_enrolled': 'Not Enrolled',
    'draft': 'Draft (Review Pending)',
    'active': 'Running',
    'completed': 'Replied / Completed',
    'suppressed': 'Suppressed (Skipped)',
    'failed': 'Failed',
    'reactivated': 'Reactivated (Success)',
    'cold': 'Cold (No Response)',
    'opt_out': 'Opt Out (Unsubscribed)'
}

def format_stage(val):
    return STAGE_LABELS.get(val, val)

def format_status(val):
    return STATUS_LABELS.get(val, val)



class HTMLToTextParser(HTMLParser):
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
        text = html.unescape(text)
        text = re.sub(r'\n\s*\n+', '\n\n', text)
        return text.strip()


def clean_html(html_str: str) -> str:
    if not html_str:
        return ""
    # Strip basic block comments if any
    html_str = re.sub(r'<!--.*?-->', '', html_str, flags=re.DOTALL)
    parser = HTMLToTextParser()
    parser.feed(html_str)
    return parser.get_data()


def get_msg_name(msg) -> str | None:
    if hasattr(msg, "name"):
        return msg.name
    if isinstance(msg, dict):
        return msg.get("name")
    return None


def get_msg_content(msg) -> str:
    content = ""
    if hasattr(msg, "content"):
        content = msg.content
    elif isinstance(msg, dict):
        content = msg.get("content", "")
    else:
        content = str(msg)

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if "text" in item:
                    parts.append(item["text"])
                elif "type" in item and item["type"] == "text":
                    parts.append(item.get("text", ""))
                else:
                    parts.append(str(item))
            elif isinstance(item, str):
                parts.append(item)
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


_llm_instance = None
_lock = threading.Lock()


def get_llm() -> BaseChatModel:
    """Initializes and returns the global chat model based on config."""
    global _llm_instance
    if _llm_instance is None:
        with _lock:
            if _llm_instance is None:
                provider = config.LLM_PROVIDER
                if provider == 'mistral':
                    _llm_instance = ChatMistralAI(
                        model="mistral-large-latest",
                        api_key=config.MISTRAL_API_KEY,
                        max_retries=3
                    )
                elif provider in ['google', 'gemini']:
                    _llm_instance = ChatGoogleGenerativeAI(
                        model="gemini-2.0-flash",
                        api_key=config.GEMINI_API_KEY,
                        max_retries=3
                    )
                elif provider == 'groq':
                    _llm_instance = ChatGroq(
                        model="llama-3.3-70b-versatile",
                        api_key=config.GROQ_API_KEY,
                        max_retries=3
                    )
                else:
                    raise ValueError(f"Unsupported LLM provider: {provider}")
    return _llm_instance


def invoke_llm_with_retry(runnable, messages, run_config=None, max_retries=5, initial_delay=5):
    """Invokes LLM with retry logic and exponential backoff to handle rate limits (429 errors)."""
    delay = initial_delay
    for attempt in range(max_retries):
        try:
            return runnable.invoke(messages, config=run_config)
        except Exception as e:
            err_str = str(e)
            is_rate_limit = (
                "429" in err_str or 
                "rate_limit" in err_str.lower() or 
                type(e).__name__ == "RateLimitError"
            )
            if is_rate_limit and attempt < max_retries - 1:
                # Parse wait time if mentioned in Groq message
                wait_time = delay
                match_s = re.search(r'try again in (\d+(?:\.\d+)?)s', err_str)
                if match_s:
                    wait_time = float(match_s.group(1)) + 1.0
                wait_time = min(wait_time, 90.0)
                
                print(f"[Agent] [Rate Limit] Hit rate limit on attempt {attempt+1}/{max_retries}. Sleeping {wait_time:.1f}s...")
                time.sleep(wait_time)
                delay *= 2
            else:
                raise e


_global_token_counter = {
    "input_tokens": 0,
    "output_tokens": 0
}

class ToolLoggingCallbackHandler(BaseCallbackHandler):
    """Prints start, end, and error events for all tool executions."""
    def on_llm_end(self, response, **kwargs):
        global _global_token_counter
        try:
            for generations in response.generations:
                for gen in generations:
                    msg = getattr(gen, "message", None)
                    if msg is not None:
                        metadata = getattr(msg, "response_metadata", {})
                        usage = metadata.get("token_usage") or getattr(msg, "usage_metadata", {})
                        if usage:
                            p_tokens = usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0)
                            c_tokens = usage.get("completion_tokens", 0) or usage.get("output_tokens", 0)
                            _global_token_counter["input_tokens"] += p_tokens
                            _global_token_counter["output_tokens"] += c_tokens
        except Exception:
            pass

    def __init__(self):
        super().__init__()
        self._run_tool_names = {}

    def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
        name = serialized.get("name", "Unknown Tool")
        self._run_tool_names[run_id] = name
        
        # Internal read/state/utility tools to suppress from verbose logging
        internal_tools = {
            "get_campaign_lead",
            "manage_todo_list",
            "get_company_details",
            "get_salesperson_details",
            "check_partner_status",
            "check_suppression_criteria",
            "check_new_orders",
            "check_customer_replies",
            "get_customer_memories",
            "clear_todo_list",
            "get_customer_purchased_categories"
        }
        if name in internal_tools:
            return
        
        if name in ("reply_analyst", "email_copywriter"):
            print(f"[Agent] [Subagent] Invoking subagent '{name}'...")
        else:
            print(f"[Agent] [Action] Executing '{name}'...")

    def on_tool_end(self, output, *, run_id, **kwargs):
        name = self._run_tool_names.pop(run_id, None)
        if name:
            key_action_tools = {
                "send_winback_email",
                "blacklist_partner_in_odoo",
                "log_campaign_note",
                "save_customer_memory",
                "schedule_partner_activity",
                "update_campaign_lead"
            }
            if name in key_action_tools:
                print(f"[Agent] [Tool Done] '{name}' completed successfully.")

    def on_tool_error(self, error, *, run_id, **kwargs):
        name = self._run_tool_names.pop(run_id, None)
        if name:
            print(f"[Agent] [ERROR] Tool '{name}' failed: {error}")
        else:
            print(f"[Agent] [ERROR] Tool execution failed: {error}")


def read_playbook(filename: str) -> str:
    """Reads a markdown playbook file from the skills directory."""
    skills_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'skills')
    path = os.path.join(skills_dir, filename)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _extract_hitl_request(interrupt_payload) -> dict | None:
    """Extract a HITLRequest dict from LangGraph interrupt payload."""
    if not interrupt_payload:
        return None
    items = interrupt_payload if isinstance(interrupt_payload, (list, tuple)) else [interrupt_payload]
    for item in items:
        value = item.value if hasattr(item, "value") else item
        if isinstance(value, dict) and "action_requests" in value:
            return value
    return None


def _extract_hitl_request_from_exception(exc: Exception) -> dict | None:
    """Extract a HITLRequest dict from a GraphInterrupt-style exception."""
    candidates = []
    if hasattr(exc, "value"):
        candidates.append(exc.value)
    if exc.args:
        first_arg = exc.args[0]
        candidates.append(first_arg)
        if isinstance(first_arg, (list, tuple)) and first_arg:
            candidates.append(first_arg[0])
            if hasattr(first_arg[0], "value"):
                candidates.append(first_arg[0].value)
        elif hasattr(first_arg, "value"):
            candidates.append(first_arg.value)

    for candidate in candidates:
        value = candidate.value if hasattr(candidate, "value") else candidate
        if isinstance(value, dict) and "action_requests" in value:
            return value
    return None


def _resolve_send_winback_email_approval(action_args: dict) -> dict:
    """Prompt the operator to approve, edit, or reject a win-back email.

    Returns a HITLResponse dict suitable for Command(resume=...).
    """
    while True:
        print("\n" + "=" * 80)
        print("  HUMAN-IN-THE-LOOP EMAIL APPROVAL REQUIRED")
        print("=" * 80)
        print(f"Customer ID:   {action_args.get('partner_id')}")
        print(f"Customer Name: {action_args.get('customer_name')}")
        print(f"Recipient:     {action_args.get('customer_email')}")
        print(f"Subject:       {action_args.get('subject')}")
        print(f"Salesperson:   {action_args.get('salesperson_name')} <{action_args.get('salesperson_email')}>")
        print("-" * 80)
        print("EMAIL BODY:")
        body_html = action_args.get("body_html", "")
        body_text = clean_html(body_html)
        try:
            print(body_text)
        except UnicodeEncodeError:
            print(body_text.encode("ascii", errors="replace").decode("ascii"))
        print("=" * 80)

        is_interactive = sys.stdin is not None and sys.stdin.isatty()
        choice = ""

        if is_interactive:
            try:
                choice_str = input("\nChoose action: [A]pprove & Send, [E]dit, [W]rite again / Regenerate, [R]eject & Skip: ").strip()
                if choice_str:
                    choice = choice_str[0].upper()
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as prompt_err:
                print(f"[Agent] [Warning] Stdin prompt failed: {prompt_err}")
                is_interactive = False

        if not is_interactive:
            print("\n[HIL] Stdin is not interactive and AUTO_APPROVE is disabled. Automatically rejecting email draft to avoid blocking.")
            choice = "R"
        elif choice not in ("A", "E", "W", "R"):
            print("Invalid choice. Please enter A, E, W, or R.")
            continue

        if choice == "A":
            print("\n[HIL] Approved. Resuming agent to send email...")
            return {"decisions": [{"type": "approve"}]}

        if choice == "W":
            print("\n[HIL] Rewrite/Regenerate requested. Resuming agent to draft email again...")
            return {
                "decisions": [{
                    "type": "reject",
                    "message": "The email draft was rejected by the operator. Please rewrite/regenerate the email with a different, more compelling copy.",
                }]
            }

        if choice == "E":
            print("\n--- Editing Email (Type 'back' or 'b' to return to main menu) ---")
            new_subject = action_args.get("subject")
            new_body = action_args.get("body_html")

            try:
                edited_subject = input(f"Subject [{action_args.get('subject')}]: ").strip()
            except Exception:
                edited_subject = ""
            
            if edited_subject.lower() in ("back", "b", "cancel"):
                print("Returning to main menu...")
                continue
            if edited_subject:
                new_subject = edited_subject

            print("Enter new HTML body (press Enter on empty line to finish, or type 'back' on a single line to return):")
            lines = []
            cancelled = False
            while True:
                try:
                    line = input()
                except Exception:
                    break
                if line.strip().lower() in ("back", "b", "cancel") and not lines:
                    cancelled = True
                    break
                if line == "":
                    break
                lines.append(line)
            
            if cancelled:
                print("Returning to main menu...")
                continue
            if lines:
                new_body = "\n".join(lines)

            edited_args = dict(action_args)
            edited_args["subject"] = new_subject
            edited_args["body_html"] = new_body
            print("\n[HIL] Resuming agent with edited email...")
            return {
                "decisions": [{
                    "type": "edit",
                    "edited_action": {"name": "send_winback_email", "args": edited_args},
                }]
            }

    print("\n[HIL] Email rejected.")
    return {
        "decisions": [{
            "type": "reject",
            "message": "Email rejected by salesperson/operator.",
        }]
    }


def _handle_hitl_interrupt(hitl_request: dict) -> Command:
    """Convert a HITLRequest into a resume Command after operator review."""
    action_requests = hitl_request.get("action_requests", [])
    if not action_requests:
        raise ValueError("Interrupt received without action_requests")

    action = action_requests[0]
    action_name = action.get("name")
    action_args = action.get("args", {})

    if action_name == "send_winback_email":
        resume_value = _resolve_send_winback_email_approval(action_args)
    else:
        print(f"[HIL] Auto-rejecting unhandled interrupt for tool '{action_name}'.")
        resume_value = {
            "decisions": [{
                "type": "reject",
                "message": f"Unhandled tool interrupt: {action_name}",
            }]
        }

    return Command(resume=resume_value)


def run_agent_for_lead(partner_id: int):
    """Compiles and executes the deep orchestrator agent for a specific lead."""
    # Clear any previous checklist items for this lead to ensure a fresh campaign run
    try:
        clear_todo_list.invoke({"partner_id": partner_id})
    except Exception as err:
        print(f"[Warning] Could not clear old checklist for lead {partner_id}: {err}")

    orchestrator_prompt = read_playbook("orchestrator_playbook.md")
    copywriter_prompt = read_playbook("copywriter_playbook.md")
    reply_analyst_prompt = read_playbook("reply_analyst_playbook.md")

    # Setup the LLM
    llm = get_llm()

    # Compile the Main Orchestrator Agent with inline subagents
    agent = create_deep_agent(
        model=llm,
        tools=[
            get_campaign_lead,
            update_campaign_lead,
            check_partner_status,
            check_suppression_criteria,
            check_new_orders,
            check_customer_replies,
            send_winback_email,
            log_campaign_note,
            schedule_partner_activity,
            check_recent_outreach,
            get_company_details,
            get_salesperson_details,
            save_customer_memory,
            get_customer_memories,
            manage_todo_list
        ],
        system_prompt=orchestrator_prompt,
        subagents=[
            {
                "name": "reply_analyst",
                "description": "Analyzes incoming customer email replies to campaign outreach. Evaluates and classifies customer intent into categories (Inquiry, Grievance, OOO, Contact Change, or Opt-Out). Invokes blacklist tools to register unsubscribes, or schedules follow-up task activities in Odoo assigned to the customer's salesperson.",
                "system_prompt": reply_analyst_prompt,
                "tools": [
                    blacklist_partner_in_odoo,
                    schedule_partner_activity,
                    log_campaign_note,
                    save_customer_memory
                ]
            },
            {
                "name": "email_copywriter",
                "description": "Generates personalized, low-pressure, and professional HTML re-engagement outreach emails (Email 1, 2, and 3). Queries product category histories to inject tailored purchase recommendations, and dynamically retrieves company information and salesperson details from Odoo to format a clean, professional email signature.",
                "system_prompt": copywriter_prompt,
                "tools": [
                    get_customer_purchased_categories,
                    get_company_details,
                    get_salesperson_details
                ]
            }
        ],
        interrupt_on={"send_winback_email": True} if config.AUTO_REPLY and not getattr(config, 'AUTO_APPROVE', False) else None,
        checkpointer=MemorySaver(),
    )

    run_config = {
        "callbacks": [ToolLoggingCallbackHandler()],
        "configurable": {"thread_id": f"winback_{partner_id}"}
    }

    # Execute the agent graph with streaming updates and interactive HIL
    now_str = datetime.now(timezone.utc).isoformat()
    from tools import load_odoo_company_config
    load_odoo_company_config()
    promo_code = config.WINBACK_OFFER_EMAIL2 or ""
    inputs = {
        "messages": [
            HumanMessage(
                content=(
                    f"Process the win-back campaign for customer Odoo ID {partner_id}. "
                    f"Current UTC date/time is {now_str}. "
                    f"Campaign settings: Win-back Offer Promo Code = '{promo_code}'."
                )
            )
        ]
    }
    
    current_input = inputs
    response = None
    while True:
        interrupted = False
        try:
            for chunk in agent.stream(current_input, config=run_config, stream_mode="updates"):
                response = chunk
                hitl_request = _extract_hitl_request(chunk.get("__interrupt__"))
                if hitl_request:
                    current_input = _handle_hitl_interrupt(hitl_request)
                    interrupted = True
                    break

                for node_name, node_update in chunk.items():
                    if node_name == "__interrupt__":
                        continue
                    
                    # Ignore internal middleware nodes to keep logging clean
                    ignored_nodes = {
                        "PatchToolCallsMiddleware",
                        "HumanInTheLoopMiddleware",
                        "TodoListMiddleware"
                    }
                    if any(ignored in node_name for ignored in ignored_nodes):
                        continue

                    if not isinstance(node_update, dict):
                        continue
                    if "messages" in node_update and node_update["messages"]:
                        latest_msg = node_update["messages"][-1]
                        msg_name = get_msg_name(latest_msg)
                        msg_content = get_msg_content(latest_msg).strip()
                        
                        if msg_content:
                            if node_name == "model":
                                try:
                                    print(f"[Agent] [Orchestrator]:\n{msg_content}\n")
                                except UnicodeEncodeError:
                                    print(f"[Agent] [Orchestrator]:\n{msg_content.encode('ascii', errors='replace').decode('ascii')}\n")
                            elif msg_name == "reply_analyst":
                                try:
                                    print(f"[Agent] [Analysis] Reply Analyst Report:\n{msg_content}\n")
                                except UnicodeEncodeError:
                                    print(f"[Agent] [Analysis] Reply Analyst Report:\n{msg_content.encode('ascii', errors='replace').decode('ascii')}\n")
                            elif msg_name == "email_copywriter":
                                print(f"[Agent] [Copywriter] Email draft generated. Pending operator approval.\n")
            if not interrupted:
                break
        except Exception as e:
            hitl_request = _extract_hitl_request_from_exception(e)
            if hitl_request:
                current_input = _handle_hitl_interrupt(hitl_request)
                continue
            raise
                
    return response


# ── LangGraph Node Implementations ────────────────────────────────────────────

def discovery_node(state) -> dict:
    """Discovery Node: Fetches inactive partners, enrolls new ones in SQLite,

    and populates the leads queue in graph state.
    """
    print("[Agent] [Node] Starting Discovery & Enrollment...")
    
    # 1. Fetch candidates using Odoo inactivity threshold
    try:
        inactive_customers = get_inactive_partners.invoke({})
    except Exception as e:
        print(f"[Agent] [Node] [ERROR] Discovery failed: {e}")
        # Gracefully handle the error and avoid crashing the pipeline
        return {
            "leads_to_process": [],
            "processed_leads": []
        }
    print(f"[Agent] [Node] Discovery found {len(inactive_customers)} inactive candidate(s) in Odoo.")

    # 3. Apply runtime limit if set to avoid performing too many state checkups
    limit = config.get_limit()
    if limit:
        inactive_customers = inactive_customers[:limit]
        print(f"[Agent] [Node] Applied processing limit to discovery: truncated to {len(inactive_customers)} candidate(s).")

    # 4. Reconstruct state and build the queue of active leads dynamically
    active_leads = []
    for c in inactive_customers:
        partner_id = c['id']
        name = c['name']
        
        lead_state = get_campaign_lead.invoke({"partner_id": partner_id})
        if lead_state and lead_state.get("status") == "active":
            active_leads.append({
                "partner_id": partner_id,
                "partner_name": name
            })
            
    print(f"[Agent] [Node] Active queue compiled from Odoo/JSON: {len(active_leads)} lead(s)")
        
    # Write initial progress
    progress_data = {
        "status": "running",
        "total": len(active_leads),
        "processed": 0,
        "percentage": 0,
        "current_lead": None,
        "last_update": datetime.now(timezone.utc).isoformat()
    }
    progress_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'progress.json')
    try:
        with open(progress_file, 'w', encoding='utf-8') as f:
            json.dump(progress_data, f, indent=4)
    except Exception as pe:
        print(f"[Warning] Could not write initial progress: {pe}")
        
    return {
        "leads_to_process": active_leads,
        "processed_leads": []
    }


def process_lead_node(state) -> dict:
    """Lead Processing Node: Processes a single lead from the queue,
    running the orchestrator deep agent graph and isolating any errors.
    """
    queue = list(state.get("leads_to_process") or [])
    processed = list(state.get("processed_leads") or [])

    if not queue:
        return {"leads_to_process": [], "processed_leads": processed}

    current = queue.pop(0)
    partner_id = current.get("partner_id")
    partner_name = current.get("partner_name")

    total_leads = len(queue) + len(processed) + 1
    current_idx = len(processed) + 1
    percent = int((current_idx / total_leads) * 100) if total_leads > 0 else 100
    
    # Progress bar visualization (using ASCII characters for maximum terminal compatibility)
    bar_length = 20
    filled_length = int(bar_length * current_idx // total_leads) if total_leads > 0 else bar_length
    bar = '=' * filled_length + '-' * (bar_length - filled_length)
    try:
        print(f"\n[Agent] [Progress] [{bar}] {percent}% ({current_idx}/{total_leads}) | Checking constraints for customer: '{partner_name}' (ID: {partner_id})...")
    except UnicodeEncodeError:
        safe_name = partner_name.encode('ascii', errors='replace').decode('ascii')
        print(f"\n[Agent] [Progress] [{bar}] {percent}% ({current_idx}/{total_leads}) | Checking constraints for customer: '{safe_name}' (ID: {partner_id})...")

    # Write to progress.json
    progress_data = {
        "status": "running",
        "total": total_leads,
        "processed": current_idx,
        "percentage": percent,
        "current_lead": {
            "partner_id": partner_id,
            "partner_name": partner_name
        },
        "last_update": datetime.now(timezone.utc).isoformat()
    }
    progress_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'progress.json')
    try:
        with open(progress_file, 'w', encoding='utf-8') as f:
            json.dump(progress_data, f, indent=4)
    except Exception as pe:
        print(f"[Warning] Could not write progress.json: {pe}")

    status_log = {}
    try:
        # 1. Read Local State
        lead_state = get_campaign_lead.invoke({"partner_id": partner_id})
        if not lead_state or lead_state.get("status") != "active":
            print(f"  [Skip] Lead is not active in local campaign leads database (status: {format_status(lead_state.get('status'))}).")
            status_log = {
                "partner_id": partner_id,
                "partner_name": partner_name,
                "status": "skipped",
                "campaign_status": lead_state.get("status", "none"),
                "campaign_stage": lead_state.get("campaign_stage", "none"),
                "log": "Lead is not active."
            }
            processed.append(status_log)
            return {
                "leads_to_process": queue,
                "processed_leads": processed,
                "current_lead": current
            }

        campaign_stage = lead_state.get("campaign_stage", "none")
        last_email_sent_date_str = lead_state.get("last_email_sent_date")
        next_email_date_str = lead_state.get("next_email_date")

        # Check if a draft is pending manual review (lisa_wb_processed is False)
        if lead_state.get("has_pending_draft"):
            print(f"  [Skip] Customer has an email draft pending manual review in Odoo.")
            status_log = {
                "partner_id": partner_id,
                "partner_name": partner_name,
                "status": "skipped",
                "campaign_status": "active",
                "campaign_stage": campaign_stage,
                "log": "Skipped: Outreach email draft is pending manual review in Odoo."
            }
            processed.append(status_log)
            return {
                "leads_to_process": queue,
                "processed_leads": processed,
                "current_lead": current
            }

        # 2. Check Timing / Schedule Check
        is_due = False
        if next_email_date_str:
            next_email_date = datetime.fromisoformat(next_email_date_str.replace('Z', '+00:00'))
            if datetime.now(timezone.utc) >= next_email_date:
                is_due = True
        else:
            if campaign_stage == "none":
                is_due = True
            elif last_email_sent_date_str:
                last_dt = datetime.fromisoformat(last_email_sent_date_str.replace('Z', '+00:00'))
                next_email_date = last_dt + timedelta(days=config.WINBACK_INTERVAL_DAYS)
                if datetime.now(timezone.utc) >= next_email_date:
                    is_due = True
            else:
                print(f"  [Skip] Lead stage is '{campaign_stage}' but next_email_date and last_email_sent_date are missing/invalid in state. Skipping lead to prevent duplicate outreach.")
                status_log = {
                    "partner_id": partner_id,
                    "partner_name": partner_name,
                    "status": "skipped",
                    "campaign_status": "active",
                    "campaign_stage": campaign_stage,
                    "log": f"Skipped: Missing schedule dates for campaign stage '{campaign_stage}'."
                }
                processed.append(status_log)
                return {
                    "leads_to_process": queue,
                    "processed_leads": processed,
                    "current_lead": current
                }

        if not is_due:
            print(f"  [Skip] Next email date ({next_email_date_str}) is in the future. Not due yet.")
            status_log = {
                "partner_id": partner_id,
                "partner_name": partner_name,
                "status": "skipped",
                "campaign_status": "active",
                "campaign_stage": campaign_stage,
                "log": f"Not due yet. Next email date: {next_email_date_str}."
            }
            processed.append(status_log)
            return {
                "leads_to_process": queue,
                "processed_leads": processed,
                "current_lead": current
            }

        # 3. Check Eligibility & Blacklist in Odoo
        status_info = check_partner_status.invoke({"partner_id": partner_id})
        if not status_info.get("active") or status_info.get("is_blacklisted"):
            new_status = "cold" if not status_info.get("active") else "opt_out"
            update_campaign_lead.invoke({"partner_id": partner_id, "status": new_status})
            log_campaign_note.invoke({"partner_id": partner_id, "message_body": f"Win-back campaign stopped. Partner active={status_info.get('active')}, blacklisted={status_info.get('is_blacklisted')}"})
            print(f"  [Halt] Partner active={status_info.get('active')}, blacklisted={status_info.get('is_blacklisted')} in Odoo. Setting campaign status to '{new_status}'.")
            status_log = {
                "partner_id": partner_id,
                "partner_name": partner_name,
                "status": "success",
                "campaign_status": new_status,
                "campaign_stage": campaign_stage,
                "log": f"Partner inactive/blacklisted in Odoo. Set status to {new_status}."
            }
            processed.append(status_log)
            return {
                "leads_to_process": queue,
                "processed_leads": processed,
                "current_lead": current
            }

        # 4. Check Suppression Criteria
        suppression_info = check_suppression_criteria.invoke({"partner_id": partner_id})
        if suppression_info.get("suppressed"):
            update_campaign_lead.invoke({"partner_id": partner_id, "status": "opt_out", "suppression_reason": suppression_info.get("reason")})
            log_campaign_note.invoke({"partner_id": partner_id, "message_body": f"Win-back campaign skipped. Suppression reason: {suppression_info.get('reason')}"})
            print(f"  [Halt] Partner meets suppression criteria: {suppression_info.get('reason')}. Setting status to 'opt_out'.")
            status_log = {
                "partner_id": partner_id,
                "partner_name": partner_name,
                "status": "success",
                "campaign_status": "opt_out",
                "campaign_stage": campaign_stage,
                "log": f"Suppressed: {suppression_info.get('reason')}."
            }
            processed.append(status_log)
            return {
                "leads_to_process": queue,
                "processed_leads": processed,
                "current_lead": current
            }

        # 5. Check Persistent Memory Logs for permanent objections
        memories = get_customer_memories.invoke({"partner_id": partner_id})
        if memories:
            objections = ["business closed", "closed down", "switched to competitor", "invalid contact"]
            found_objection = None
            for obj in objections:
                if obj in memories.lower():
                    found_objection = obj
                    break
            if found_objection:
                new_status = "opt_out" if ("opt_out" in memories.lower() or "unsubscribe" in memories.lower()) else "cold"
                update_campaign_lead.invoke({"partner_id": partner_id, "status": new_status})
                log_campaign_note.invoke({"partner_id": partner_id, "message_body": f"Win-back campaign skipped based on persistent memory logs: {memories}"})
                print(f"  [Halt] Permanent objection in memory: '{memories}'. Setting status to '{new_status}'.")
                status_log = {
                    "partner_id": partner_id,
                    "partner_name": partner_name,
                    "status": "success",
                    "campaign_status": new_status,
                    "campaign_stage": campaign_stage,
                    "log": f"Skipped via memory objection: {memories}."
                }
                processed.append(status_log)
                return {
                    "leads_to_process": queue,
                    "processed_leads": processed,
                    "current_lead": current
                }

        # 6. Check for Reactivation via Purchase
        check_since_date = last_email_sent_date_str if last_email_sent_date_str else lead_state.get("last_order_date")
        if not check_since_date:
            # Default to 60 days ago
            check_since_date = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
            
        new_orders = check_new_orders.invoke({"partner_id": partner_id, "since_date_utc": check_since_date})
        if new_orders:
            order_names = [o.get("name", "Unknown Order") for o in new_orders]
            update_campaign_lead.invoke({"partner_id": partner_id, "status": "reactivated", "campaign_stage": "none"})
            log_campaign_note.invoke({"partner_id": partner_id, "message_body": f"Customer reactivated via new order {', '.join(order_names)}!"})
            schedule_partner_activity.invoke({"partner_id": partner_id, "summary": "Win-Back: Customer reactivated by placing order", "note_html": f"Orders: {', '.join(order_names)}"})
            print(f"  [Reactivated] Lead placed new orders: {order_names}. Campaign status set to 'Reactivated (Success)'.")
            status_log = {
                "partner_id": partner_id,
                "partner_name": partner_name,
                "status": "success",
                "campaign_status": "reactivated",
                "campaign_stage": "none",
                "log": f"Reactivated via new order(s): {order_names}."
            }
            processed.append(status_log)
            return {
                "leads_to_process": queue,
                "processed_leads": processed,
                "current_lead": current
            }

        # 7. Check for Customer Replies
        reply_since_date = last_email_sent_date_str if last_email_sent_date_str else check_since_date
        if not reply_since_date:
            # Default to 30 days ago
            reply_since_date = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
            
        replies = check_customer_replies.invoke({"partner_id": partner_id, "since_date_utc": reply_since_date})
        if replies:
            print(f"  [Reply Detected] Customer replied to outreach (stage: '{format_stage(campaign_stage)}'). Stopping campaign and notifying salesperson...")
            # Pre-set status to 'replied' so the orchestrator knows the campaign must stop.
            # The reply_analyst subagent (invoked by the orchestrator) will update this further
            # (e.g. to 'opt_out') if the reply is an explicit unsubscribe request.
            update_campaign_lead.invoke({"partner_id": partner_id, "status": "replied"})
            run_agent_for_lead(partner_id)
            updated_lead = get_campaign_lead.invoke({"partner_id": partner_id})
            status = updated_lead.get("status", "replied")
            stage = updated_lead.get("campaign_stage", campaign_stage)
            status_log = {
                "partner_id": partner_id,
                "partner_name": partner_name,
                "status": "success",
                "campaign_status": status,
                "campaign_stage": stage,
                "log": f"Reply detected at stage '{campaign_stage}'. Campaign stopped. Final status: {status}. Salesperson activity assigned."
            }
            processed.append(status_log)
            return {
                "leads_to_process": queue,
                "processed_leads": processed,
                "current_lead": current
            }

        # 8. Check for Recent Outreach (Frequency Cap)
        recent_outreach_info = check_recent_outreach.invoke({"partner_id": partner_id, "days_limit": config.WINBACK_INTERVAL_DAYS})
        if recent_outreach_info.get("has_recent_outreach"):
            last_date_str = recent_outreach_info.get("last_outreach_date")
            last_date = datetime.fromisoformat(last_date_str.replace(' ', 'T').replace('Z', '+00:00'))
            rescheduled_date = last_date + timedelta(days=config.WINBACK_INTERVAL_DAYS)
            rescheduled_date_str = rescheduled_date.isoformat()
            
            update_campaign_lead.invoke({
                "partner_id": partner_id,
                "next_email_date": rescheduled_date_str
            })
            
            print(f"  [Skip] Customer had outreach email sent in the last {config.WINBACK_INTERVAL_DAYS} days (on {last_date_str}). Rescheduling next check to {rescheduled_date_str}.")
            status_log = {
                "partner_id": partner_id,
                "partner_name": partner_name,
                "status": "skipped",
                "campaign_status": "active",
                "campaign_stage": campaign_stage,
                "log": f"Skipped: Frequency cap active. Rescheduled next check to {rescheduled_date_str}."
            }
            processed.append(status_log)
            return {
                "leads_to_process": queue,
                "processed_leads": processed,
                "current_lead": current
            }

        # If all checks pass, we run the Deep Agent to draft and send the email
        # (or to finalize the campaign as cold if stage is 'email_3_sent' with no reply)
        if campaign_stage == "email_3_sent":
            print(f"  [Due] 3rd email wait period complete. No reply detected. Invoking Deep Agent to mark customer as Cold...")
        else:
            print(f"  [Due] All checks passed. Invoking Deep Agent to draft email...")
        run_agent_for_lead(partner_id)

        # Retrieve the updated lead state after agent run
        updated_lead = get_campaign_lead.invoke({"partner_id": partner_id})
        status = updated_lead.get("status", "active")
        stage = updated_lead.get("campaign_stage", "none")

        if campaign_stage == "email_3_sent" and status == "cold":
            log_msg = f"3-email campaign complete. No reply received. Customer marked as Cold (No Response) and salesperson notified."
        else:
            log_msg = f"Successfully processed lead. Status: {format_status(status)}, Stage: {format_stage(stage)}."

        status_log = {
            "partner_id": partner_id,
            "partner_name": partner_name,
            "status": "success",
            "campaign_status": status,
            "campaign_stage": stage,
            "log": log_msg
        }
    except Exception as e:
        print(f"  [ERROR] Processing lead {partner_id} failed: {e}")
        status_log = {
            "partner_id": partner_id,
            "partner_name": partner_name,
            "status": "failed",
            "campaign_status": "failed",
            "campaign_stage": "none",
            "log": f"Failed during campaign run: {str(e)}."
        }

    processed.append(status_log)

    return {
        "leads_to_process": queue,
        "processed_leads": processed,
        "current_lead": current
    }


def summary_node(state) -> dict:
    """Summary Node: Compiles a run execution report using LLM,

    with fallback logic on API rate limits.
    """
    print("\n[Agent] [Node] Compiling Final Pipeline Summary...")
    processed = state.get("processed_leads") or []

    # Write to progress.json as completed
    progress_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'progress.json')
    try:
        total = len(processed)
        if os.path.exists(progress_file):
            with open(progress_file, 'r', encoding='utf-8') as f:
                existing_data = json.load(f)
                total = existing_data.get("total", total)
        progress_data = {
            "status": "completed",
            "total": total,
            "processed": total,
            "percentage": 100,
            "current_lead": None,
            "last_update": datetime.now(timezone.utc).isoformat()
        }
        with open(progress_file, 'w', encoding='utf-8') as f:
            json.dump(progress_data, f, indent=4)
        print("[Agent] [Progress] Campaign runner progress: COMPLETED (100%)")
    except Exception as pe:
        print(f"[Warning] Could not write final progress.json: {pe}")

    if not processed:
        processed_str = "No active campaign leads were processed in this run."
    else:
        processed_str = "\n".join(
            f"- Lead {p['partner_id']} ({p['partner_name']}): Execution: {p['status'].upper()} | Campaign Stage: {format_stage(p['campaign_stage'])} | Campaign Status: {format_status(p['campaign_status'])} | Log: {p['log']}"
            for p in processed
        )

    try:
        print("[Agent] Requesting summary report from LLM...")
        llm = get_llm()
        prompt = SUMMARY_AGENT_INSTRUCTION.format(
            date=datetime.now().strftime("%Y-%m-%d"),
            processed_leads=processed_str
        )
        response = invoke_llm_with_retry(llm, [HumanMessage(content=prompt)])
        if response:
            metadata = getattr(response, "response_metadata", {})
            usage = metadata.get("token_usage") or getattr(response, "usage_metadata", {})
            if usage:
                _global_token_counter["input_tokens"] += usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0)
                _global_token_counter["output_tokens"] += usage.get("completion_tokens", 0) or usage.get("output_tokens", 0)
        return {"final_report": response.content}
    except Exception as e:
        print(f"[Agent] [Warning] LLM summary report failed ({e}). Generating fallback python report...")
        
        # Python fallback formatting
        report_lines = [
            "### Today's Win-Back Pipeline Execution Summary (Python Fallback)",
            f"**Execution Date:** {datetime.now().strftime('%Y-%m-%d')}",
            "**Processed Customers & Campaign Leads:**",
            processed_str,
            "*Note: This report was compiled by Python fallback because the LLM summary endpoint was rate-limited or unavailable.*"
        ]
        return {"final_report": "\n\n".join(report_lines)}


def export_metrics():
    global _global_token_counter
    import json
    import os
    from datetime import datetime, timezone
    
    input_tokens = _global_token_counter["input_tokens"]
    output_tokens = _global_token_counter["output_tokens"]
    
    # Pricing configuration (USD per 1M tokens)
    pricing = {
        "mistral": {"input": 2.0 / 1000000, "output": 6.0 / 1000000},
        "groq": {"input": 0.59 / 1000000, "output": 0.79 / 1000000},
        "gemini": {"input": 0.075 / 1000000, "output": 0.30 / 1000000}
    }
    
    provider = config.LLM_PROVIDER
    rates = pricing.get(provider, {"input": 0.0, "output": 0.0})
    estimated_cost = (input_tokens * rates["input"]) + (output_tokens * rates["output"])
    
    metrics_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    os.makedirs(metrics_dir, exist_ok=True)
    metrics_path = os.path.join(metrics_dir, "run_metrics.json")
    
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "llm_provider": provider,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "estimated_cost_usd": float(f"{estimated_cost:.6f}")
    }
    
    # Read existing entries
    existing_entries = []
    if os.path.exists(metrics_path) and os.path.getsize(metrics_path) > 0:
        try:
            with open(metrics_path, "r", encoding="utf-8") as f:
                existing_entries = json.load(f)
                if not isinstance(existing_entries, list):
                    existing_entries = []
        except Exception:
            pass
            
    existing_entries.append(entry)
    
    try:
        with open(metrics_path, "w", encoding="utf-8") as f:
            json.dump(existing_entries, f, indent=4)
        print(f"[Metrics Logger] Run metrics saved: input_tokens={input_tokens}, output_tokens={output_tokens}, cost=${estimated_cost:.6f}")
    except Exception as e:
        print(f"[Metrics Logger] [Warning] Failed to save metrics: {e}")
