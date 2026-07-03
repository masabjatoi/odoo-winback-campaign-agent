# Proposed Plan: Advanced Deep Agent Features for Win-Back Sales Agent

This document outlines the advanced capabilities implemented under the Deep Agents framework to improve the intelligence, reliability, and personalization of the win-back campaign.

---

## Agent Architecture: Sub-Agents & Tools

To support this advanced setup, the Deep Agent consists of the following agents and tools:

### Specialized Agents
* **Main Orchestrator Agent (The Hub):** Manages the workflow checklist (`manage_todo_list`), queries database parameters, coordinates safety checks, and delegates creative reasoning tasks.
* **`reply_analyst` Sub-Agent (Spoke):** Evaluates incoming customer messages to classify responses (e.g. out-of-office, active interest, general question, opt-out request).
* **`email_copywriter` Sub-Agent (Spoke):** Custom-tailors outreach email copy (especially Email 2) using customer order history.

### Custom Odoo & Campaign State Tools
1. **`get_inactive_partners`**: Queries Odoo for active partners who haven't placed a confirmed order in `inactivity_threshold_days`.
2. **`get_customer_purchased_categories`**: Queries Odoo `sale.order.line` to fetch product categories bought historically by the customer.
3. **`check_partner_status`**: Checks if the partner is active and not globally blacklisted.
4. **`check_new_orders`**: Scans Odoo for confirmed orders placed after the last email date.
5. **`check_customer_replies`**: Scans Odoo `mail.message` chatter for incoming emails from the partner since the campaign began.
6. **`send_winback_email`**: Dispatches the campaign email. If manual review is enabled in Odoo (`AUTO_REPLY` is False), it writes the draft to Odoo custom fields and exits.
7. **`log_campaign_note`**: Logs updates and notes directly onto the customer's (`res.partner`) chatter thread.
8. **`check_recent_outreach`**: Enforces the frequency cap by checking for other outbound emails to the customer within the last X days.
9. **`check_suppression_criteria`**: Detects if the customer has suppression tags (e.g., VIP, "No Contact") or is in active pipeline negotiations.
10. **`schedule_partner_activity`**: Creates a `mail.activity` record in Odoo linked to the customer, assigned to their salesperson, with a custom summary and note for follow-up review.

---

## 1. Declarative Playbooks & Skills per Agent (`SKILL.md`)

### Objective
Instead of loading all instructions into a single massive playbook, we split the rules into three declarative, markdown-based skill files. Each agent loads only the specific skill it needs, keeping prompts concise, focused, and free from context pollution.

### Implementation
Three specialized skill files are created in the `skills/` directory:
1. **`skills/orchestrator_playbook.md`**: Used by the main orchestrator agent. Coordinates workflow checklists and timing/cadence rules.
2. **`skills/copywriter_playbook.md`**: Used by the `email_copywriter` sub-agent. Directs HTML email styling, tone, and purchased category injection.
3. **`skills/reply_analyst_playbook.md`**: Used by the `reply_analyst` sub-agent. Governs intent classification (OOO, Grievance, Inquiry, Opt-Out, Contact Change) and corresponding Odoo actions.

---

## 2. Odoo-Native Semantic Memory Management

### Objective
Provide the agent with persistent memory of past win-back attempts, customer preferences, and interaction notes (e.g., customer replied "I closed my business" or "we use another vendor now") to avoid repeatedly emailing customers who are permanently cold or have specific objections.

### Implementation
Memories are stored and queried natively on Odoo:
* **Storage Location**: Memories are appended to and retrieved from the Odoo native **Internal Notes (`comment`)** field on the customer's record (`res.partner`).
* **Workflow Automation**: Before any re-engagement outreach is drafted, the Orchestrator checks the customer's memories. If a permanent objection (e.g. out of business, closed down, switched to competitor) is found, the campaign is immediately halted, and the lead status is set to `'cold'` or `'opt_out'`.
* **Writing Memory**: When the `reply_analyst` classifies customer replies, it dynamically extracts key details and invokes `save_customer_memory` to store them in Odoo.

---

## 3. Dynamic Non-Blocking HIL Approval

### Objective
Ensure that email drafts can be reviewed by a salesperson when Odoo is configured in manual review mode, while running completely hands-free in automated cron mode.

### Implementation
* **Odoo-Native HIL (Non-blocking)**: If `AUTO_REPLY` is False in Odoo settings, the agent compiles the outreach copy, writes it to Odoo fields (`x_lisa_wb_email_html` and `x_lisa_wb_email_subject`), updates the campaign status to `'draft'`, and exits cleanly without CLI prompts or polling blocks.
* **CLI HIL (Interactive)**: If `AUTO_REPLY` is True and `AUTO_APPROVE` is False, the pipeline pauses during interactive terminal runs to prompt the user to Approve (`A`), Edit (`E`), Rewrite (`W`), or Reject (`R`) the email before dispatch.

---

## 4. Orchestrator Planning & TODO Management (`manage_todo_list`)

### Objective
Ensure the win-back agent systematically processes each customer through the pipeline (Checking Opt-Out -> Checking Orders -> Checking Replies -> Sequencing/Drafting -> Logging Notes) rather than trying to do everything in one unguided step.

### Implementation
1. Expose a `manage_todo_list` tool to create, check, and update tasks on a checklist.
2. The orchestrator agent updates this list dynamically for each customer run, ensuring all safety checks (Blacklist/Purchase checks) are successfully completed before dispatching emails.

---

## 5. Specialized Sub-Agent Delegation (Hub-and-Spoke)

### Objective
Isolate responsibilities. Instead of a single model attempting to run queries, write emails, and check replies, we split the tasks among specialized sub-agents, loading each with its own designated playbook.

### Implementation
1. **Register the specialized sub-agents:**
   * **`reply_analyst` sub-agent:** Evaluates incoming chatter email content and classifies responses.
   * **`email_copywriter` sub-agent:** Formats campaign emails (HTML) and queries product lines to inject relevant category recommendations into Email 2.
2. **Setup the Orchestrator:** The orchestrator performs the checks and uses the native `task` tool to delegate reply analysis and email drafting tasks to the corresponding sub-agents.

---

## 6. Rate Limit Self-Healing Retry Loop

### Objective
Handle LLM rate limits gracefully during batch execution across hundreds of inactive customers.

### Implementation
1. Wrap LLM calls in a retry wrapper (`invoke_llm_with_retry`) that catches rate-limiting exceptions (e.g. from Gemini or Mistral APIs).
2. Sleep the execution thread (capped at 90 seconds) with exponential backoff before retrying.

---

## 7. Intelligent Segment Recommendations

### Objective
For Email 2 (Value-Based Re-engagement), dynamically analyze the customer's historical order lines in Odoo to recommend products or categories that they previously purchased, rather than sending a generic discount code.

### Implementation
1. Query Odoo `sale.order.line` for the customer's past orders.
2. Extract the most purchased product categories.
3. Feed these categories to the `email_copywriter` sub-agent to draft highly personalized recommendations.

---

## 8. Error Isolation per Partner

### Objective
Ensure that a network failure, database error, or LLM timeout for one customer's campaign processing does not halt the entire pipeline execution.

### Implementation
1. Wrap each partner's processing loop in a `try-except` block.
2. If an exception occurs, write the error details to the logs, mark the customer status as `failed` for that run, and continue to the next inactive partner.

---

## 9. Frequency Capping & Advanced Suppression

### Objective
Prevent spamming customers who have recently received other communications, and automatically suppress active deals, VIPs, or manual "no contact" lists as specified in the win-back plan.

### Implementation
1. **Frequency Cap:** Build a tool to check Odoo `mail.message` for outgoing messages sent to the contact across all marketing/sales models within the last X days. If any recent email exists, pause the campaign step.
2. **Suppression Filters:** Check customer tags (e.g. "VIP", "No Contact") and check `crm.lead` for any active opportunity in negotiation stages. Skip campaign processing if active.


