# Project Summary: Win-Back Sales Agent Pipeline

This document provides a detailed overview of the Win-Back Sales Agent project objectives, the completed implementation, and the dynamic Odoo-native architecture.

---

## 1. Project Objective

The goal of the **Win-Back Sales Agent** is to automatically identify inactive customers (who have not placed a confirmed sales order in 60+ days) and enroll them in a structured, low-pressure 3-step re-engagement sequence. 
* **If they buy:** Stop the automated sequence, set campaign stage to `'none'`, and set status to `'reactivated'` in Odoo.
* **If they reply:** Stop the automated sequence, set campaign status to `'replied'` in Odoo, and schedule a review/follow-up activity for their assigned salesperson.
* **If they request an opt-out:** Halt the campaign, set status to `'opt_out'`, and add them to Odoo's global email blacklist.
* **If they remain inactive:** After completing the 3-step sequence and a 7-day final wait, set campaign status to `'cold'` and notify the salesperson.

---

## 2. Current Implementation Status

We have successfully completed all phases of the pipeline, transitioning it to a fully Odoo-native model:

### ⚙️ Configuration File: [[.env](file:///d:/Win-Back%20Agent/.env)]
Contains all connection parameters and credentials:
* `ODOO_URL` (Odoo server URL)
* `ODOO_DB` (Odoo database name)
* `ODOO_USERNAME` (Odoo username)
* `ODOO_API_KEY` (Odoo API Key / password)
* `LLM_PROVIDER` (Selected LLM provider: `mistral`, `gemini`, or `groq`)
* `MISTRAL_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY` (Provider API keys)

### 🛠️ Custom Odoo Tools: [[tools.py](file:///d:/Win-Back%20Agent/tools.py)]
A module containing standard, reusable LangChain tool definitions decorated with `@tool` to allow easy integration into the Deep Agent:
1. **`get_inactive_partners`**: Queries Odoo for active, non-blacklisted partner records who have not placed a confirmed order in $X$ days.
2. **`check_partner_status`**: Queries Odoo `res.partner` and `mail.blacklist` to check if a customer is still active and not blacklisted.
3. **`check_new_orders`**: Scans Odoo `sale.order` for confirmed orders placed by the customer since a specific UTC date.
4. **`check_customer_replies`**: Scans Odoo `mail.message` logs for incoming email responses from the customer since the last campaign outreach.
5. **`send_winback_email`**: Crafts and logs/sends the win-back outreach email. If `AUTO_REPLY` is False (Manual Review), it writes the draft to Odoo and returns `sent=False` without sending.
6. **`log_campaign_note`**: Logs campaign updates and stage transitions to the customer's chatter log in Odoo.
7. **`schedule_partner_activity`**: Automatically creates a To-Do activity (`mail.activity`) in Odoo assigned to the customer's salesperson.

### 🚀 Main Pipeline Runner: [[main.py](file:///d:/Win-Back%20Agent/main.py)]
The orchestrating script that runs the discovery loop:
1. Validates environmental configurations.
2. Invokes `get_inactive_partners` using the threshold loaded dynamically from Odoo company settings to check inactive candidates.
3. Launches the LangGraph pipeline compiling the active leads queue.

---

## 3. Odoo Parameters & Schema Mapping

Through Odoo inspection, we verified and mapped the following native parameters:
* **`res.partner`**: Used to read `name`, `email`, `active`, `is_blacklisted` (global blacklist flag), and `user_id` (the assigned salesperson / sales rep). Notes are written directly to chatter using `message_post`. Draft emails are saved in `x_lisa_wb_email_html` and `x_lisa_wb_email_subject`.
* **`sale.order`**: Confirmed orders are identified by `state in ['sale', 'done']` and `date_order`.
* **`mail.message`**: Chatter logs are searched where `model = 'res.partner'` and `message_type = 'email'` to detect customer replies.
* **`mail.activity`**: Tasks are scheduled on partners for salespeople to review replies using `activity_type_id` (To-Do) and `user_id` (salesperson).
* **`mail.blacklist`**: Global unsubscribes are registered in this model by inserting the customer's email.
* **`winback.campaign`**: Stateful model containing the campaign tracking record for each partner (fields: `partner_id`, `stage`, `status`, `email_1_sent_date`, `email_2_sent_date`, `email_3_sent_date`, `suppression_reason`).

---

## 4. Deep Agent Architecture

The agent runs completely database-free locally. It utilizes a dynamic, state-driven LangGraph pipeline that aligns directly with the Cross-Sell Agent architecture:

### 📁 Codebase Organization
- **[main.py](file:///d:/Win-Back%20Agent/main.py)**: The entry point script that validates configurations, compiles the LangGraph workflow, and runs it.
- **[config.py](file:///d:/Win-Back%20Agent/config.py)**: Centralizes environment parameters, Odoo configs, and performs credential validation.
- **[prompt.py](file:///d:/Win-Back%20Agent/prompt.py)**: Stores prompt templates and instructions for B2B summary reporting.
- **[graph.py](file:///d:/Win-Back%20Agent/graph.py)**: Defines the workflow's State (`PipelineState`), wires the nodes and transitions, and maps conditional routing edges.
- **[agent.py](file:///d:/Win-Back%20Agent/agent.py)**: Implements the graph node executors:
  * `discovery_node`: Scans candidates and builds the queue.
  * `process_lead_node`: Runs the orchestrator Deep Agent (with sub-agents nested inline) on a single lead.
  * `summary_node`: Uses LLM to draft a run execution report.
- **[tools.py](file:///d:/Win-Back%20Agent/tools.py)**: Module containing all custom Odoo tools.

### 🤖 Agent Roles & Playbooks
1. **Main Orchestrator Agent (The Hub):**
    * **Playbook:** [orchestrator_playbook.md](file:///d:/Win-Back%20Agent/skills/orchestrator_playbook.md)
    * **Function:** Sequentially executes lead checkups, checks suppression, checks timing intervals, and delegates creative reasoning.
2. **`reply_analyst` Sub-Agent (Spoke):**
    * **Playbook:** [reply_analyst_playbook.md](file:///d:/Win-Back%20Agent/skills/reply_analyst_playbook.md)
    * **Function:** Evaluates incoming customer emails, classifies intent, and automatically invokes blacklist or activity tools.
3. **`email_copywriter` Sub-Agent (Spoke):**
    * **Playbook:** [copywriter_playbook.md](file:///d:/Win-Back%20Agent/skills/copywriter_playbook.md)
    * **Function:** Custom-tailors HTML outreach emails, querying historical Odoo purchase categories for Email 2 recommendations.

---

## 5. Persistent Memory Management

We have successfully implemented persistent semantic memory to record and check customer preferences, objections, or status updates:
* **Storage Location**: Memories are appended to and retrieved from the Odoo native **Internal Notes (`comment`)** field on the customer's record (`res.partner`).
* **Checking Memory**: Before any re-engagement outreach is drafted, the Orchestrator checks the customer's memories. If a permanent objection (e.g. out of business, closed down, switched to competitor) is found, the campaign is immediately halted, and the lead status is set to `'cold'` or `'opt_out'`.
* **Writing Memory**: When the `reply_analyst` classifies customer replies, it dynamically extracts key details and invokes `save_customer_memory` to store them in Odoo.

---

## 6. Dynamic Non-blocking HIL Approval

We have successfully implemented a dynamic Human-in-the-Loop (HIL) verification flow:
* **Automated Cron Mode**: If `AUTO_REPLY` is True, the agent sends emails automatically through Odoo without requiring any manual terminal inputs or approvals, ensuring seamless execution in background cron jobs.

---

## 7. Pipeline Optimization & Bug Fixes

We have resolved several critical issues across the codebase to ensure production-grade stability, Odoo schema correctness, and performance optimization:

1. **Odoo 16/17 Blacklist Query Correction**: Removed direct lookups of `is_blacklisted` from `res.partner` queries. Replaced with dynamic, separate lookups in the Odoo `mail.blacklist` model, querying by email addresses.
2. **Customer Reply Matching**: Enhanced reply matching in `check_customer_replies` to lookup partner email addresses and filter by both `author_id == partner_id` and `email_from` containing the partner's email address.
3. **Odoo chatter ID wrapping**: Modified the positional argument format in `log_campaign_note` message posts from a bare integer `[partner_id]` to `[[partner_id]]` to conform with Odoo's XML-RPC API expectation.
4. **Purchase Category Frequency Ranking**: Updated `get_customer_purchased_categories` to count category occurrences using `collections.Counter` and rank them descending by purchase frequency.
5. **Outreach checking across all models**: Removed the `model = 'res.partner'` filter in `check_recent_outreach` and replaced it with a `('partner_ids', 'in', [partner_id])` domain search to track outreach logged under sale orders or invoices.
6. **Parallel XML-RPC Client Safety**: Reverted global `ServerProxy` caching to instantiate fresh proxies on demand inside `get_odoo_client()`. Since XML-RPC `ServerProxy` maintains state internally, sharing a single cached instance across parallel threads (during parallel tool execution in LangGraph) leads to connection/socket exceptions. Creating fresh instances per call guarantees thread safety.

---

## 8. Odoo Activity Scheduling

When certain business milestones are reached, the agent schedules a Native Odoo Activity (`mail.activity`) assigned to the customer's salesperson to transfer manual control back to the sales rep.

* **Reactivated Activity**: A customer places a new sales order during the campaign. A "To-Do" activity with summary `"Win-Back: Customer reactivated by placing order"` is created.
* **Customer Reply Activity**: A customer replies to an outreach email (excluding opt-out requests). A "To-Do" activity with summary `"Kindly review: Customer replied to Win-Back"` is created.
* **Cold Closure Activity**: A customer fails to buy or reply after receiving all 3 campaign emails and the final 7-day wait. A "To-Do" activity with summary `"Win-back campaign completed - customer moved to Cold"` is created.

---

## 9. Critical Operational Guidelines & Development Gotchas

* **XML-RPC Proxy Thread Safety**: Always instantiate a fresh XML-RPC server proxy on demand inside `get_odoo_client()` rather than caching the proxy object itself to prevent connection collision during parallel node executions.
* **Odoo message_post ID Wrapping**: Posting a comment to a partner record's chatter via Odoo's `message_post` expects the partner ID to be double-wrapped inside a list (e.g., `[[partner_id]]`) due to RPC array deserialization quirks.
* **Checklist Cache Reset per Run**: `run_agent_for_lead` calls `clear_todo_list(partner_id)` at the start of every single execution to guarantee a fresh checklist is created in memory and no tasks are skipped.

---

## 10. Updates & Additions (2026-07-07)

### 10.1 Token Cost Metrics Logging
- **What:** Every LLM call is intercepted by a `ToolLoggingCallbackHandler` in `agent.py` that accumulates `input_tokens` and `output_tokens` across the full pipeline run. At the end of each run, `export_metrics()` is called from `main.py`.
- **Output file:** `data/run_metrics.json` (excluded from Git, persisted locally or via Docker volume).
- **Example entry:**
  ```json
  { "timestamp": "2026-07-07T12:00:00Z", "input_tokens": 260274, "output_tokens": 2070, "cost_usd": 0.532968 }
  ```
- **Cost model:** Mistral Large — $0.003/1K input tokens, $0.009/1K output tokens.

---

### 10.2 AI Quality Testing with DeepEval (`tests/`)
- **What:** A full AI evaluation test suite added under `tests/` using the [DeepEval](https://github.com/confident-ai/deepeval) framework and G-Eval metrics.
- **Files:**
  - `tests/conftest.py` — Loads `.env` before pytest; falls back to mock credentials for offline/CI runs.
  - `tests/test_graph.py` — Unit tests verifying LangGraph state transitions and node structure without invoking the LLM.
  - `tests/test_ai_quality.py` — Live G-Eval evaluation: sends real email drafts to Mistral Large acting as an LLM judge. Scores on B2B tone, empathy, no opt-out language, and overall email quality.
- **How to run:**
  ```bash
  deepeval test run tests/test_ai_quality.py
  ```
- **Dependency:** `deepeval` added to `requirements.txt`.

---

### 10.3 Confident AI Dashboard Integration
- **What:** Test run results are automatically posted to [Confident AI](https://app.confident-ai.com/) after each `deepeval test run`.
- **How:** DeepEval reads `CONFIDENT_API_KEY` from `.env.local` (excluded from Git) and uploads results to the project dashboard.
- **Dashboard:** `https://app.confident-ai.com/project/cmraog1wj001pog13p8pa1tg8/`
- **Note:** If the API key is absent, DeepEval silently skips the upload — no error is thrown.

---

### 10.4 Prompt Formatting Guardrails
Two strict rules added to all LLM system prompts (`prompt.py`, `skills/orchestrator_playbook.md`, `skills/copywriter_playbook.md`):
1. **No em dashes** (`—` or `–`) — use plain hyphens (`-`) instead.
2. **No campaign naming** — the words "win-back", "win back", "cross-sell", "cross sell" are explicitly forbidden in all generated email copy and operator reports.
- **Playbook renames:** The orchestrator persona was renamed from "Win-Back Orchestrator" to "Orchestrator Sales Agent". The copywriter playbook removed "Win-Back Sales Agent" from its persona description. The "Cross-Sell agent" reference in the spacing rule was also removed.

---

### 10.5 Terminal Progress Bar Removed
- **What removed:** The ASCII `[====----] 100%` progress bar and `[Agent] [Progress] ...` print statements were removed from `agent.py`.
- **Why:** Terminal-only output with no value in Docker or cron environments.
- **What remains:** `progress.json` continues to be written at each step with `status`, `total`, `processed`, `percentage`, `current_lead`, and `last_update` fields for programmatic consumption.

---

### 10.6 `progress.json` Removed from Git
- `progress.json` was previously tracked in Git. It has been untracked (`git rm --cached`) and added to `.gitignore`.
- This file changes on every run and contains live runtime state — it must not be committed to version control.

---

### 10.7 Repository Cleanup
- **`.gitignore`** updated to exclude: `.env.local`, `.deepeval/`, `.pytest_cache/`, `data/`, `progress.json`.
- **`requirements.txt`** updated to include `deepeval`.
- **Status: Fully Operational.**

