# (Done) Project Summary: Win-Back Sales Agent Pipeline

This document provides a detailed overview of the Win-Back Sales Agent project objectives, the completed implementation, and the finished migration to the advanced Deep Agent architecture.

---

## (Done) 1. Project Objective

The goal of the **Win-Back Sales Agent** is to automatically identify inactive customers (who have not placed a confirmed sales order in 60+ days) and enroll them in a structured, low-pressure 3-step re-engagement sequence. 
* **If they buy:** Stop the automated sequence, mark them as `reactivated` in local tracking, and alert their assigned salesperson (sales rep) in Odoo.
* **If they reply:** Stop the automated sequence, mark them as `replied` in local tracking, and schedule a review/follow-up activity for their assigned salesperson in Odoo.
* **If they request an opt-out:** Halt the campaign and add them to Odoo's global email blacklist.
* **If they remain inactive:** After completing the 3-step sequence and a 7-day final wait, mark them as `Cold` and notify the salesperson.

---

## (Done) 2. Current Implementation Status

We have successfully completed both **Phase 1 (Discovery & Enrollment)** and **Phase 2 (Deep Agent Campaign Execution)** of the pipeline:

### (Done) ⚙️ Configuration File: [[.env](file:///d:/Win-Back%20Agent/.env)]
Contains all connection parameters, campaign thresholds, and testing configurations:
* `INACTIVITY_THRESHOLD_DAYS=60`
* `WINBACK_INTERVAL_DAYS=7`
* `WINBACK_OFFER_EMAIL2=WELCOME10`
* `MAX_WINBACK_EMAILS=3`
* `FINAL_WAIT_DAYS=7`
* `TEST_MODE=true` (If enabled, bypasses Odoo writes and routes all outgoing emails via Gmail SMTP)
* `GMAIL_SMTP_USER` (Gmail sender for tests)
* `GMAIL_SMTP_APP_PASSWORD` (App-specific password for Gmail SMTP)
* `TEST_EMAIL_TO` (Recipient address for all test emails)

### (Done) 🛠️ Custom Odoo Tools: [[tools.py](file:///d:/Win-Back%20Agent/tools.py)]
A module containing standard, reusable LangChain tool definitions decorated with `@tool` to allow easy integration into the Deep Agent:
1. **`get_inactive_partners`**: Queries Odoo for active, non-blacklisted partner records who have not placed a confirmed order in $X$ days.
2. **`check_partner_status`**: Queries Odoo `res.partner` to check if a customer is still active and not blacklisted.
3. **`check_new_orders`**: Scans Odoo `sale.order` for confirmed orders placed by the customer since a specific UTC date.
4. **`check_customer_replies`**: Scans Odoo `mail.message` logs for incoming email responses from the customer since the campaign start date.
5. **`send_winback_email`**: Dispatches the campaign email. In `TEST_MODE`, it safely bypasses Odoo and sends HTML emails directly via Gmail SMTP to `TEST_EMAIL_TO`.
6. **`log_campaign_note`**: Logs updates and campaign stage transitions directly on the customer's chatter log in Odoo (bypassed in `TEST_MODE` to avoid cluttering Odoo).
7. **`schedule_partner_activity`**: Automatically creates a To-Do activity (`mail.activity`) in Odoo assigned to the customer's salesperson when they reply (bypassed in `TEST_MODE`).

### (Done) 🚀 Main Pipeline Runner: [[main.py](file:///d:/Win-Back%20Agent/main.py)]
The orchestrating script that runs the discovery loop:
1. Validates environmental configurations.
2. Invokes `get_inactive_partners` using the threshold from `.env` to check inactive candidates.
3. Launches the LangGraph pipeline compiling the active leads queue.

### (Done) 🗄️ JSON Test State Persistence (`campaign_test_state.json`)
Tracks the campaign state for each customer locally during test execution runs under `TEST_MODE=true` to isolate testing without polluting Odoo chatter:
- Key-valued by customer Odoo ID.
- Automatically stores simulated values for: `campaign_stage`, `last_email_sent_date`, `next_email_date`, `status`, and `memories`.

---

## (Done) 3. Odoo Parameters & Schema Mapping

Through Odoo inspection, we verified and mapped the following native parameters:
* **`res.partner`**: Used to read `name`, `email`, `active`, `is_blacklisted` (global blacklist flag), and `user_id` (the assigned salesperson / sales rep). Notes are written directly to chatter using `message_post`.
* **`sale.order`**: Confirmed orders are identified by `state in ['sale', 'done']` and `date_order`.
* **`mail.message`**: Chatter logs are searched where `model = 'res.partner'` and `message_type = 'email'` to detect customer replies.
* **`mail.activity`**: Tasks are scheduled on partners for salespeople to review replies using `activity_type_id=4` (To-Do) and `user_id` (salesperson).
* **`mail.blacklist`**: Global unsubscribes are registered in this model by inserting the customer's email.

---

## (Done) 4. Deep Agent Architecture

We have successfully migrated the codebase to the **Deep Agents** framework, implementing a dynamic, state-driven LangGraph pipeline that aligns directly with the **Cross-Sell Agent** architecture:

### (Done) 📁 Codebase Organization
- **[main.py](file:///d:/Win-Back%20Agent/main.py)**: The entry point script that validates configurations, compiles the LangGraph workflow, and runs it synchronously.
- **[config.py](file:///d:/Win-Back%20Agent/config.py)**: Centralizes environment parameters, thresholds, and performs credential validation.
- **[prompt.py](file:///d:/Win-Back%20Agent/prompt.py)**: Stores prompt templates and instructions for B2B summary reporting.
- **[graph.py](file:///d:/Win-Back%20Agent/graph.py)**: Defines the workflow's State (`PipelineState`), wires the nodes and transitions, and maps conditional routing edges.
- **[agent.py](file:///d:/Win-Back%20Agent/agent.py)**: Implements the graph node executors:
  * `discovery_node`: Scans candidates and builds the queue.
  * `process_lead_node`: Runs the orchestrator Deep Agent (with sub-agents nested inline) on a single lead.
  * `summary_node`: Uses LLM to draft a run execution report.
- **[tools.py](file:///d:/Win-Back%20Agent/tools.py)**: Module containing all 12 custom Odoo and SQLite tools.

### (Done) 🤖 Agent Roles & Playbooks
1. **Main Orchestrator Agent (The Hub):**
   * **Playbook:** [orchestrator_playbook.md](file:///d:/Win-Back%20Agent/skills/orchestrator_playbook.md)
   * **Function:** Sequentially executes lead checkups, checks suppression, manages SQLite state, checks timing intervals, and delegates creative reasoning.
2. **`reply_analyst` Sub-Agent (Spoke):**
   * **Playbook:** [reply_analyst_playbook.md](file:///d:/Win-Back%20Agent/skills/reply_analyst_playbook.md)
   * **Function:** Evaluates incoming customer emails, classifies intent, and automatically invokes blacklist or activity tools.
3. **`email_copywriter` Sub-Agent (Spoke):**
   * **Playbook:** [copywriter_playbook.md](file:///d:/Win-Back%20Agent/skills/copywriter_playbook.md)
   * **Function:** Custom-tailors HTML outreach emails, querying historical Odoo purchase categories for Email 2 recommendations.

### (Done) 🛠️ Comprehensive Agent Tools (tools.py)
1. **`get_campaign_lead`**: Reads the campaign lead's current stage and status dynamically from Odoo (or from `campaign_test_state.json` under `TEST_MODE`).
2. **`update_campaign_lead`**: Writes campaign stage and status updates directly to Odoo chatter logs (or to `campaign_test_state.json` under `TEST_MODE`).
3. **`check_partner_status`**: Verifies if the customer is active and not globally blacklisted in Odoo.
4. **`check_suppression_criteria`**: Detects if the customer has VIP/No Contact tags or has active CRM negotiations.
5. **`check_recent_outreach`**: Checks Odoo chatter to prevent sending campaign emails if other emails were sent in the last 7 days.
6. **`check_new_orders`**: Scans Odoo sales orders to detect customer reactivation.
7. **`check_customer_replies`**: Scans Odoo chatter for incoming customer responses since campaign start.
8. **`get_customer_purchased_categories`**: Queries Odoo product templates for categories historically bought by the customer.
9. **`send_winback_email`**: Sends outreach emails (supports test/production routing).
10. **`log_campaign_note`**: Logs updates and stage transitions to customer chatter in Odoo.
11. **`schedule_partner_activity`**: Creates a To-Do activity in Odoo assigned to the customer's salesperson.
12. **`blacklist_partner_in_odoo`**: Adds the customer's email to Odoo's native global blacklist.

---

## (Done) 5. Toggling Environments: Test Mode vs Production Mode

To make operations simple and prevent errors, the system isolates test environments from production through the [.env](file:///d:/Win-Back%20Agent/.env) file. Changing `TEST_MODE` shifts the operational behavior of the entire system as follows:

| Feature / Behavior | Testing Environment (`TEST_MODE=true`) | Production Environment (`TEST_MODE=false`) |
|:---|:---|:---|
| **Local State Tracking** | Persistent flat JSON file (`campaign_test_state.json`) tracks simulated campaign stages. | **Stateless**. No local files or databases. State is dynamically derived from Odoo. |
| **Email Outreach** | Outgoing emails are redirected to `TEST_EMAIL_TO` (`jatoimasab@gmail.com`) via developer Gmail SMTP. | Outgoing emails are sent directly to the customer's real email address via Odoo's native `mail.mail` model. |
| **Odoo Chatter Logs** | Chatter logs are bypassed; notes are printed to the console only. | Notes are posted natively to the customer's chatter feed (`res.partner`) using `message_post`. |
| **Sales Rep Activities** | Activity creation is bypassed and printed to console only. | Actual `mail.activity` (To-Do) records are created natively in Odoo assigned to the salesperson. |
| **Semantic Memories** | Written and read from local `campaign_test_state.json` memories block. | Written and read from the Odoo native **Internal Notes (`comment`)** field on the customer's `res.partner` record. |
| **Global Blacklist** | Blacklist creation is bypassed and printed to console only. | Opt-out replies automatically add the customer's email address to Odoo's native global `mail.blacklist` table. |

---

## (Done) 6. Persistent Memory Management (Phase 3)

We have successfully implemented persistent semantic memory to record and check customer preferences, objections, or status updates:
* **Dual-Mode Persistence**:
  * **Testing Mode (`TEST_MODE=true`):** Memories are written to and retrieved from the local JSON file `campaign_test_state.json`.
  * **Production Mode (`TEST_MODE=false`):** Memories are appended to and retrieved from Odoo's native **Internal Notes (`comment`)** field on the customer's record (`res.partner`).
* **Workflow Automation**:
  * **Checking Memory**: Before any re-engagement outreach is drafted or sent, the Orchestrator checks the customer's memories. If a permanent objection (e.g. out of business, closed down, switched to competitor) is found, the campaign is immediately halted, and the lead status is set to `'cold'` or `'opt_out'`.
  * **Writing Memory**: When the `reply_analyst` classifies customer replies (OOO, Inquiry, Grievance, Opt-out, Contact Change), it dynamically extracts the key details and invokes `save_customer_memory` to store them.

---

## (Done) 7. Human-in-the-Loop Approval (Phase 4)

We have successfully implemented Human-in-the-Loop (HIL) verification for outreach emails:
* **Tool Level Interrupts**: Configured the Orchestrator with `interrupt_on={"send_winback_email": True}` to automatically suspend execution before sending any campaign outreach email.
* **Pre-dispatch Review**: This pauses the LangGraph pipeline, allowing operators or sales reps to inspect, modify, or approve the AI-generated HTML email content and subject before delivery.

---

## (Done) 8. Pipeline Optimization & Bug Fixes

We have resolved 12 critical, significant, and minor issues across the codebase to ensure production-grade stability, Odoo schema correctness, and performance optimization:

### 🔴 Critical Fixes
1. **Module-level Constants Refactoring**: Moved all constants (`TEST_MODE`, `DB_PATH`, `GMAIL_SMTP_*`, etc.) to the top of [tools.py](file:///d:/Win-Back%20Agent/tools.py) right after imports. Integrated them with `config.py` to prevent import-order errors and avoid hardcoded settings.
2. **Odoo 16/17 Blacklist Query Correction**: Removed direct lookups of `is_blacklisted` from `res.partner` queries in `get_inactive_partners` and `check_partner_status`. Replaced with dynamic, separate lookups in the Odoo `mail.blacklist` model, querying by email addresses.
3. **Customer Reply Matching**: Enhanced reply matching in `check_customer_replies` to lookup partner email addresses and filter by both `author_id == partner_id` and `email_from` containing the partner's email address (preventing replies from B2B contacts who reply via external mail servers without portal linkages from being ignored).
4. **Odoo chatter ID wrapping**: Modified the positional argument format in `log_campaign_note` message posts from a bare integer `[partner_id]` to `[[partner_id]]` to conform with Odoo's XML-RPC API expectation.

### 🟠 Significant Fixes
5. **Purchase Category Frequency Ranking**: Updated `get_customer_purchased_categories` to count category occurrences using `collections.Counter` and rank them descending by purchase frequency.
6. **Outreach checking across all models**: Removed the `model = 'res.partner'` filter in `check_recent_outreach` and replaced it with a `('partner_ids', 'in', [partner_id])` domain search to track outreach logged under CRM leads, sale orders, and invoices.
7. **Dynamic Activity Type Lookup**: Resolved hardcoded `activity_type_id = 4` in `schedule_partner_activity` by searching for activity types named `'To-Do'` or defaulting to the `'default'` category fallback at runtime.
8. **SQLite Schema Migration**: Added columns `is_blacklisted`, `suppressed`, and `suppression_reason` to the SQLite `campaign_leads` schema in `agent.py` and implemented dynamic `ALTER TABLE` schema migration at startup to safely upgrade existing databases.

### 🟡 Minor Fixes
9. **Odoo Client Caching**: Cached Odoo authentication `uid` in a global module-level variable in `tools.py` with a 1-hour TTL to avoid expensive authentication handshakes on every single tool call.
10. **SMTP Connection Safety**: Wrapped SMTP connection logic in `send_winback_email` within `try...finally` blocks to guarantee `server.quit()` execution.
11. **Structured Errors Propagation**: Replaced silent error swallowing (`return []` or `return False`) with structured exception returns/ToolException raising to inform orchestrators of Odoo server offline states or XML-RPC faults.
12. **Date Format Fallbacks**: Implemented `parse_odoo_date` and `format_date_for_odoo` datetime parsers supporting multiple formats (with/without timezones) to handle Odoo's dynamic `date_order` aggregations.

---

## (Done) 9. Code Review & Thread-Safety Fixes

We resolved 6 follow-up issues to ensure linter cleanliness, clean Odoo queries, dynamic config handling, and parallel execution safety:
1. **Dynamic Processing Limit**: Replaced the mutable `config.LIMIT` runtime property setting with a dynamic environment variable lookup `os.environ["WINBACK_LIMIT"]` accessed via `config.get_limit()`, preventing runtime module mutation alerts.
2. **Robust Campaign Lead Checking**: Refactored `get_campaign_lead` to return an empty dictionary `{}` instead of raising `ToolException` if the lead does not exist. Handled DB errors by catching `sqlite3.Error` specifically.
3. **Corrected Search Domain Builder**: Restructured domain building in `check_customer_replies` to conditionally add the `'|'` operator only when the partner email is present, avoiding malformed domains for email-less accounts.
4. **Indentation Standardization**: Fixed the indentation in the tag-scanning loop in `check_suppression_criteria` from 5 spaces to standard 4 spaces.
5. **Updated Memory retrieval docstring**: Updated the return type description in `get_customer_memories` to match the string return signature (raising `ToolException` on errors).
6. **Parallel XML-RPC Client Safety**: Reverted global `ServerProxy` caching to instantiate fresh proxies on demand inside `get_odoo_client()`. Since XML-RPC `ServerProxy` maintains state internally, sharing a single cached instance across parallel threads (during parallel tool execution in LangGraph) leads to connection/socket exceptions like `CannotSendRequest`. Creating fresh instances per call guarantees thread safety.

---

## 🗄️ 10. Dynamic State Tracking & JSON State Schema

To maintain campaign status and avoid double-processing customer contacts without local relational databases, the agent relies on Odoo's native data models (production) and a local flat JSON dictionary (testing):

### A. Dynamic State Reconstruction (Odoo Production)
- **chatter logs (`mail.message`)**: Queried to reconstruct prior email outreach dates and templates sent.
- **Sales Orders (`sale.order`)**: Queried dynamically since the last outreach event to detect Reactivations.
- **blacklist entries (`mail.blacklist`)**: Checked by email address to detect Opt-Out status.
- **Internal Notes (`comment`)**: Checked to query semantic memories of customer objections.

### B. Testing State (`campaign_test_state.json`)
Under `TEST_MODE=true`, the agent maintains campaign states locally in a single structured JSON dictionary:
- **Keys**: Odoo customer IDs (e.g. `"29199"`).
- **Structure**:
  ```json
  {
      "partner_id": 29199,
      "partner_name": "A.V.B. BV (nieuw)",
      "email": "info@avb-technieken.be",
      "salesperson_id": 916,
      "last_order_date": null,
      "campaign_stage": "none",
      "last_email_sent_date": null,
      "next_email_date": "2026-06-22T17:53:29.526970+00:00",
      "status": "active",
      "is_blacklisted": 0,
      "suppressed": 0,
      "suppression_reason": null,
      "memories": [
          {
              "memory_text": "...",
              "created_at": "..."
          }
      ]
  }
  ```
- **In-Memory Checklists**: Checklist tasks (`campaign_todos`) are tracked using a global process-level dictionary cache (`_todo_cache`) in memory during pipeline execution.

---

## 📅 11. Odoo Activity Scheduling & Environment Rules

When certain business milestones are reached, the agent schedules a Native Odoo Activity (`mail.activity`) assigned to the customer's salesperson to transfer manual control back to the sales rep.

### A. Reactivated Activity
* **Trigger**: A customer places a new sales order during the campaign.
* **Details**: A "To-Do" activity with summary `"Win-Back: Customer reactivated by placing order"`.
* **Purpose**: Alerts the sales rep to personally follow up and thank the customer.

### B. Customer Reply Activity
* **Trigger**: A customer replies to an outreach email (excluding opt-out requests).
* **Details**: A "To-Do" activity with summary `"Kindly review: Customer replied to Win-Back"` and an AI-generated summary of their message.
* **Purpose**: Alerts the sales rep to answer their questions or grievances since the automated drip has stopped.

### C. Cold Closure Activity
* **Trigger**: A customer fails to buy or reply after receiving all 3 campaign emails and the final 7-day wait.
* **Details**: A "To-Do" activity with summary `"Win-back campaign completed - customer moved to Cold"`.
* **Purpose**: Informs the salesperson so they can choose to archive the lead or attempt cold calling.

### Environment Separation:
* **Testing Mode (`TEST_MODE=true`)**: Activities are **bypassed** and printed to console logs to avoid cluttering Odoo.
* **Production Mode (`TEST_MODE=false`)**: Activities are written natively to Odoo (`mail.activity` model).

---

## 🚨 12. Critical Operational Guidelines & Development Gotchas

Below are the most critical engineering constraints, gotchas, and details that must be remembered when maintaining, deploying, or developing features for the Win-Back Campaign pipeline:

### 1. XML-RPC Proxy Thread Safety
* **Gotcha**: Sharing a single global `ServerProxy` instance across multiple LangGraph parallel nodes/threads causes socket collision errors (e.g. `CannotSendRequest`).
* **Rule**: Always instantiate a fresh XML-RPC server proxy on demand inside `get_odoo_client()` rather than caching the proxy object itself.

### 2. Odoo 16/17 Blacklist Query Format
* **Gotcha**: Querying `is_blacklisted` directly inside `res.partner` records will fail or return incorrect results depending on the version of Odoo.
* **Rule**: Always perform a separate, explicit XML-RPC check on the `mail.blacklist` model, filtering by the customer's email address (`[('email', '=', email), ('active', '=', True)]`).

### 3. Odoo message_post ID Wrapping
* **Gotcha**: Posting a comment to a partner record's chatter via Odoo's `message_post` expects the partner ID to be double-wrapped inside a list (e.g., `[[partner_id]]`) due to RPC array deserialization quirks.
* **Rule**: Do not pass a single list `[partner_id]` or a bare integer; it must be `[[partner_id]]`.

### 4. Non-Interactive (TTY) Stdin Blocking
* **Gotcha**: If the pipeline is run in a non-interactive environment (such as a cron job, a Docker entrypoint, or a background process) and standard input is left open, the HIL prompt `input()` statement will block the process indefinitely.
* **Rule**: Always redirect or close standard input (e.g., `$null | python main.py`) to force the code to use the file-based `approve.txt` polling loop.

### 5. Automated HIL File Cleanup
* **Gotcha**: The HIL review creates `edit_email.json` and checks `approve.txt` in the workspace root.
* **Rule**: The script will automatically delete `approve.txt` and `edit_email.json` immediately upon consumption or cancel. Do not leave hardcoded files in the directory; let the agent manage creation and deletion to prevent stale inputs.

### 6. Checklist Cache Reset per Run
* **Gotcha**: If checklist records are left marked as `"completed"` in-memory, subsequent evaluations for the customer will skip all verification checks thinking they are already done.
* **Rule**: `run_agent_for_lead` calls `clear_todo_list(partner_id)` at the start of every single execution to guarantee a fresh checklist is created in memory.






