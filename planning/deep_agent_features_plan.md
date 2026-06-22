# Proposed Plan: Advanced Deep Agent Features for Win-Back Sales Agent

Following the setup of the basic Win-Back Sales Agent, we can migrate the architecture to the Deep Agents framework (`deepagents.create_deep_agent`). This will implement advanced capabilities to improve the intelligence, reliability, and personalization of the win-back campaign.

---

## Agent Architecture: Sub-Agents & Tools

To support this advanced setup, the Deep Agent will consist of the following agents and tools:

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
6. **`send_winback_email`**: Creates and dispatches a `mail.mail` record in Odoo (honors `TEST_MODE` / self-sending rules).
7. **`log_campaign_note`**: Logs updates and notes directly onto the customer's (`res.partner`) chatter thread.
8. **`check_recent_outreach`**: Enforces the frequency cap by checking for other outbound emails to the customer within the last X days.
9. **`check_suppression_criteria`**: Detects if the customer has suppression tags (e.g., VIP, "No Contact") or is in active pipeline negotiations.
10. **`schedule_partner_activity`**: Creates a `mail.activity` record in Odoo linked to the customer, assigned to their salesperson, with a custom summary and note for follow-up review.

---


## 1. Declarative Playbooks & Skills per Agent (`SKILL.md`)

### Objective
Instead of loading all instructions into a single massive playbook, we split the rules into three declarative, markdown-based skill files. Each agent loads only the specific skill it needs, keeping prompts concise, focused, and free from context pollution.

### Proposed Implementation
Create three specialized skill files in the `skills/` directory:
1. **`skills/orchestrator_playbook.md`**: Used by the main orchestrator agent. Contains:
   * Rules for checking inactivity and managing the campaign lifecycle stages.
   * Drip timing intervals (e.g., 7 days wait between steps, 7 days final wait).
2. **`skills/copywriter_playbook.md`**: Used by the `email_copywriter` sub-agent. Contains:
   * Tone guidelines (warm, low-pressure, supportive).
   * Templates and framing guidelines for Email 1, 2 (value-based recommendations), and 3 (final check-in).
3. **`skills/reply_analyst_playbook.md`**: Used by the `reply_analyst` sub-agent. Contains:
   * **Universal Rule:** *Any* incoming reply from the customer immediately halts the automated sequence (stop auto-emailing).
    * **Classification Categories & Actions:**
      * **Opt-Out Request:** ONLY trigger this if the customer explicitly says "don't email me back", "unsubscribe", "stop", or related phrases. Action: Halt campaign + directly block them by creating a record in Odoo's global `mail.blacklist`.
      * **General Reply / Inquiry:** Customer asks a question, requests info, or shows interest. Action: Halt campaign + create a scheduled activity (`mail.activity`) assigned to their salesperson with a summary like *"Kindly review: Customer replied to Win-Back"* and a brief summary of their inquiry.
      * **Feedback / Grievance:** Customer shares complaints. Action: Halt campaign + create a scheduled activity assigned to the salesperson/sales manager with summary *"Kindly review: Customer grievance"*.
      * **Out-of-Office Autoreply:** Auto-responses. Action: Halt campaign + create a scheduled activity assigned to the salesperson with summary *"Kindly review: Customer out of office (Campaign paused)"*.
      * **Alternative Contact:** Customer requests to contact someone else. Action: Halt campaign + create a scheduled activity assigned to the salesperson with summary *"Kindly review: Update contact email"*.

---

## 2. Semantic Memory Management (`langmem`)

### Objective
Provide the agent with persistent memory of past win-back attempts, customer preferences, and interaction notes (e.g., customer replied "I closed my business" or "we use another vendor now") to avoid repeatedly emailing customers who are permanently cold or have specific objections.

### Proposed Implementation
1. Setup a memory store (`InMemoryStore` or a persistent vector DB).
2. Instantiate memory tools using `langmem`:
   ```python
   from langgraph.store.memory import InMemoryStore
   from langmem import create_manage_memory_tool, create_search_memory_tool

   store = InMemoryStore()
   memory_tools = [
       create_manage_memory_tool(namespace=("winback_memory", partner_id)),
       create_search_memory_tool(namespace=("winback_memory", partner_id))
   ]
   ```
3. The agent searches memory before sending any campaign email to ensure there are no negative logs or reasons to skip the customer.

---

## 3. Human-in-the-Loop Approval (HIL)

### Objective
Ensure that email drafts (especially Value-Based Email 2 and Final Attempt Email 3) are reviewed and approved by a salesperson before being created and sent.

### Proposed Implementation
1. Add an interrupt rule when initializing the agent:
   ```python
   agent = create_deep_agent(
       model=llm,
       tools=tools,
       system_prompt=system_instruction,
       interrupt_on={"draft_winback_email": True}
   )
   ```
2. During execution, the graph will pause when the agent drafts a win-back email.
3. The sales rep can inspect the draft in Odoo or via CLI, make any necessary manual edits, and approve it to resume transmission.

---

## 4. Orchestrator Planning & TODO Management (`manage_todo_list`)

### Objective
Ensure the win-back agent systematically processes each customer through the pipeline (Checking Opt-Out -> Checking Orders -> Checking Replies -> Sequencing/Drafting -> Logging Notes) rather than trying to do everything in one unguided step.

### Proposed Implementation
1. Expose a `manage_todo_list` tool to create, check, and update tasks on a checklist.
2. The orchestrator agent updates this list dynamically for each customer run, ensuring all safety checks (Blacklist/Purchase checks) are successfully completed before dispatching emails.

---

## 5. Specialized Sub-Agent Delegation (Hub-and-Spoke)

### Objective
Isolate responsibilities. Instead of a single model attempting to run queries, write emails, and check replies, we split the tasks among specialized sub-agents, loading each with its own designated playbook.

### Proposed Implementation
1. **Register the specialized sub-agents:**
   * **`reply_analyst` sub-agent:**
     * **Skill playbook:** `skills/reply_analyst_playbook.md`
     * **Purpose:** Reads incoming chatter email content and classifies responses into categories (e.g. Opt-Out, Out-of-Office, Buying Inquiry, Complaint, Contact Change).
     * **Universal Halt Rule:** *Any* reply received immediately stops the automated win-back sequence (auto-emailing ceases).
     * **Auto-Blacklist Flow (Explicit Opt-Out):** If the customer explicitly requests to stop receiving emails (e.g. "don't email me back", "unsubscribe", "stop"), the agent calls the tool to write to `mail.blacklist` to block them globally in Odoo.
     * **Scheduled Activity Flow (Other Replies):** For all other replies, the agent calls `schedule_partner_activity` to create a `mail.activity` (To-Do task) in Odoo assigned to the partner's salesperson, with a summary (e.g., *"Kindly review: Customer replied to Win-Back"*) and a short AI-compiled summary of the email's content/reason for review.
   * **`email_copywriter` sub-agent:**
     * **Skill playbook:** `skills/copywriter_playbook.md`
     * **Purpose:** Formats campaign emails (HTML) and queries product lines to inject relevant category recommendations into Email 2.
2. **Setup the Orchestrator:**
   * **Skill playbook:** `skills/orchestrator_playbook.md`
    * **Tools loaded:** Campaign state and Odoo interaction tools (Tools 1-10).
   * **Execution:** The orchestrator performs the checks and uses the native `task` tool to delegate reply analysis and email drafting tasks to the corresponding sub-agents.


---

## 6. Rate Limit Self-Healing Retry Loop

### Objective
Handle LLM rate limits gracefully during batch execution across hundreds of inactive customers.

### Proposed Implementation
1. Wrap LLM calls in a retry wrapper (`invoke_llm_with_retry`) that catches rate-limiting exceptions (e.g. from Gemini or Mistral APIs).
2. Sleep the execution thread (capped at 90 seconds) with exponential backoff before retrying.

---

## 7. Intelligent Segment Recommendations

### Objective
For Email 2 (Value-Based Re-engagement), dynamically analyze the customer's historical order lines in Odoo to recommend products or categories that they previously purchased, rather than sending a generic discount code.

### Proposed Implementation
1. Query Odoo `sale.order.line` for the customer's past orders.
2. Extract the most purchased product categories.
3. Feed these categories to the `email_copywriter` sub-agent to draft highly personalized recommendations (e.g. "We recently added several new products in your favorite category...").

---

## 8. Error Isolation per Partner

### Objective
Ensure that a network failure, database error, or LLM timeout for one customer's campaign processing does not halt the entire pipeline execution.

### Proposed Implementation
1. Wrap each partner's processing loop in a `try-except` block.
2. If an exception occurs, write the error details to the logs, mark the customer status as `failed` for that run, and continue to the next inactive partner.

---

## 9. Rule-Based Fallback Engine

### Objective
Ensure that if the external LLM provider goes offline, the agent can fallback to a basic rule-based template engine so that the drip campaign sequence remains active and emails are still sent on time.

### Proposed Implementation
1. Wrap the LLM-based copywriter and reply analyst in a fallback handler.
2. If the LLM is unreachable, use pre-defined static text templates and simple keyword-based reply checking (e.g., looking for "unsubscribe", "stop", or "order") as a fallback.

---

## 10. Direct Self-Sending Test Mode

### Objective
Allow developers to test the campaign end-to-end and receive actual emails on a developer's address without emailing real customers.

### Proposed Implementation
1. Expose a `TEST_MODE` configuration flag in the environment.
2. If `TEST_MODE` is enabled, direct all outgoing emails (`mail.mail`) to `ODOO_USERNAME` (the developer's email) and append a footer identifying the intended customer recipient.

---

## 11. Frequency Capping & Advanced Suppression

### Objective
Prevent spamming customers who have recently received other communications, and automatically suppress active deals, VIPs, or manual "no contact" lists as specified in the win-back plan.

### Proposed Implementation
1. **Frequency Cap:** Build a tool to check Odoo `mail.message` for outgoing messages sent to the contact across all marketing/sales models within the last X days. If any recent email exists, pause the campaign step.
2. **Suppression Filters:** Check customer tags (e.g. "VIP", "No Contact") and check `crm.lead` for any active opportunity in negotiation stages. Skip campaign processing if active.

