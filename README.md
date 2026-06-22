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
* **Dual-Mode Semantic Memory**: Stores objections (e.g. "business closed", "switched to competitor") to skip future campaigns. Uses local SQLite in test mode and Odoo Internal Notes (`comment`) in production.
* **Self-Healing Rate Limit Retry**: Gracefully handles Gemini, Mistral, and Groq rate limits with exponential backoff.

---

## 🛠️ Tech Stack
* **Framework**: Python 3.10+, LangChain, LangGraph, Deep Agents
* **Models**: Mistral (Mistral-Large), Gemini (Gemini-1.5-Flash), Groq (Llama-3.3-70b)
* **Databases**: SQLite (local tracking), Odoo ERP (production database)

---

## 🗄️ SQLite Database Schema

The agent uses a local SQLite database (`win_back_agent.db`) to track campaign states and checklists:

### 1. `campaign_leads`
Tracks customer drip schedules and campaign outcome status:
```sql
CREATE TABLE IF NOT EXISTS campaign_leads (
    partner_id INTEGER PRIMARY KEY,
    partner_name TEXT,
    email TEXT,
    salesperson_id INTEGER,
    last_order_date TEXT,
    campaign_stage TEXT,       -- 'none', 'email_1_sent', 'email_2_sent', 'email_3_sent'
    last_email_sent_date TEXT,  -- ISO UTC Timestamp
    next_email_date TEXT,       -- ISO UTC Timestamp
    status TEXT,                -- 'active', 'reactivated', 'cold', 'opt_out'
    is_blacklisted INTEGER DEFAULT 0,
    suppressed INTEGER DEFAULT 0,
    suppression_reason TEXT
);
```

### 2. `campaign_todos`
Tracks the internal check progress for AI execution safety:
```sql
CREATE TABLE IF NOT EXISTS campaign_todos (
    partner_id INTEGER,
    task_name TEXT,
    status TEXT, -- 'pending', 'completed'
    updated_at TEXT,
    PRIMARY KEY (partner_id, task_name)
);
```

---

## ⚙️ Configuration & Environment Toggling

Operations are isolated between testing and production through the `.env` file:

| Feature / Behavior | Testing Mode (`TEST_MODE=true`) | Production Mode (`TEST_MODE=false`) |
|:---|:---|:---|
| **Local SQLite State** | Active. Tracks lead stages and checklist tasks. | **Active**. Continues tracking lead stages and checklists locally. |
| **Email Outreach** | Redirects outreach to `TEST_EMAIL_TO` via Gmail SMTP. | Sends to actual customer emails via Odoo `mail.mail`. |
| **Odoo Chatter Logs** | Bypassed (printed to console only). | Posted natively on Odoo partner chatter feeds. |
| **Salesperson Activities** | Bypassed (printed to console only). | Creates native Odoo `mail.activity` (To-Do) records. |
| **Semantic Memories** | Written/read from local `customer_memories` table. | Written/read from Odoo's native Internal Notes (`comment`). |
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
