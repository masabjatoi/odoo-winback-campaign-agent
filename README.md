# Win-Back Sales Campaign AI Agent

An advanced, production-ready B2B customer re-engagement AI agent built on **LangGraph** and the **Deep Agents** framework. The agent automatically identifies inactive customers in **Odoo** (no confirmed order in 60+ days) and enrolls them in a personalized, low-pressure 3-step drip campaign.

---

## 🚀 Key Features

* **Odoo Integration (XML-RPC)**: Discovers inactive partners, writes re-engagement logs directly to Odoo chatter, blacklists opt-outs globally, and schedules follow-up activities.
* **Declarative Skills & Playbooks**: Split responsibilities among focused, markdown-based agent roles:
  * **Main Orchestrator**: Coordinates workflow checklists and validates constraints.
  * **Email Copywriter**: Tailors outreach copy using customer purchase history.
  * **Reply Analyst**: Evaluates incoming replies, classifies customer intent (OOO, Grievance, Inquiry, Opt-Out), and triggers corresponding workflows.
* **Pre-Agent Optimization**: Directly checks eligibility (active/blacklist), timing, suppression tags, new orders, and replies using Python prior to executing the LLM agent to ensure zero redundant LLM API costs.
* **Human-in-the-Loop (HIL) Verification**: Pauses the pipeline before dispatching emails, allowing operators to Approve (`A`), Edit (`E`), Rewrite/Regenerate (`W`), or Reject (`R`) drafts with a built-in `"Go Back"` safety loop.
* **Dual-Mode Semantic Memory**: Stores objections (e.g. "business closed", "switched to competitor") to skip future campaigns. Uses local JSON test state in test mode and Odoo Internal Notes (`comment`) in production.
* **Self-Healing Rate Limit Retry**: Gracefully handles Gemini, Mistral, and Groq rate limits with exponential backoff.

---

## 🛠️ Tech Stack
* **Framework**: Python 3.10+, LangChain, LangGraph, Deep Agents
* **Models**: Mistral (Mistral-Large), Gemini (Gemini-1.5-Flash), Groq (Llama-3.3-70b)
* **State Persistence**: Stateless (derived dynamically from Odoo chatter in production; uses a lightweight `campaign_test_state.json` file in testing)

---

## 🗄️ Stateless & Dynamic State Architecture

The agent runs completely **database-free** in production. It dynamically reconstructs and tracks campaign stages and schedules:

1. **Campaign Stage Detection**: Queries the customer's Odoo chatter logs (`mail.message` records) to check for sent campaign emails (Friendly Reminder, Value-Based Re-engagement, or Final Attempt). The count of sent templates defines the current campaign stage (`none`, `email_1_sent`, `email_2_sent`, `email_3_sent`).
2. **Timing & Cadence**: Computes the scheduling delay (7-day interval) by checking the timestamp of the last sent campaign email in Odoo.
3. **Safety Checklist**: The checklist (`campaign_todos`) is tracked in-memory during the pipeline execution run, guaranteeing safety checks are completed without database reads/writes.
4. **Reactivation & Replies**: Scans Odoo Sales Orders and incoming chatter messages since the last campaign event to verify if they ordered or replied.

---

## ⚙️ Configuration & Environment Toggling

Operations are isolated between testing and production through the `.env` file:

| Feature / Behavior | Testing Mode (`TEST_MODE=true`) | Production Mode (`TEST_MODE=false`) |
|:---|:---|:---|
| **Local State Tracking** | Persistent flat JSON file (`campaign_test_state.json`) tracks simulated campaign stages. | **Stateless**. No local files or databases. State is dynamically derived from Odoo. |
| **Email Outreach** | Redirects outreach to `TEST_EMAIL_TO` via Gmail SMTP. | Sends to actual customer emails via Odoo `mail.mail`. |
| **Odoo Chatter Logs** | Bypassed (printed to console only). | Posted natively on Odoo partner chatter feeds. |
| **Salesperson Activities** | Bypassed (printed to console only). | Creates native Odoo `mail.activity` (To-Do) records. |
| **Semantic Memories** | Written/read from local `campaign_test_state.json` memories block. | Written/read from Odoo's native Internal Notes (`comment`). |
| **Global Blacklist** | Bypassed (printed to console only). | Real entries are written to Odoo's global `mail.blacklist`. |

---

## 🚀 Setup & Execution

### 1. Clone & Install Dependencies
```bash
git clone https://github.com/masabjatoi/odoo-winback-campaign-agent.git
cd odoo-winback-campaign-agent
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure Credentials
Create a `.env` file in the root directory:
```ini
ODOO_URL=https://your-odoo-instance.com
ODOO_DB=your-db-name
ODOO_USERNAME=your-username
ODOO_API_KEY=your-xmlrpc-api-key

LLM_PROVIDER=mistral # or gemini, groq
MISTRAL_API_KEY=your-mistral-key
GEMINI_API_KEY=your-gemini-key

TEST_MODE=true
GMAIL_SMTP_USER=test-sender@gmail.com
GMAIL_SMTP_APP_PASSWORD=gmail-app-password
TEST_EMAIL_TO=test-receiver@gmail.com
```

### 3. Run the Pipeline
To execute the pipeline locally:
```bash
python main.py
```
You can also pass a processing limit for dry runs:
```bash
python main.py --limit 15
```

---

## 🚨 Critical Operational Guidelines
1. **XML-RPC Thread Safety**: Never share a global `ServerProxy` instance across graph nodes. A fresh connection is instantiated per RPC request in `get_odoo_client()` to prevent socket collisions.
2. **Odoo 16/17 Blacklist Format**: Avoid direct checks of `is_blacklisted` in `res.partner`. Verify blacklist status directly on `mail.blacklist` querying by email address.
3. **Non-Interactive (TTY) Execution**: In automated cron environments, standard input must be closed (e.g. `python main.py < /dev/null` or `$null | python main.py`) to prevent the HIL prompt blocking indefinitely.
