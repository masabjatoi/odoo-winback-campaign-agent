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
* **Non-blocking Review & Automation**:
  * **Odoo-Native Review**: If `AUTO_REPLY` is disabled (False) in Odoo company config, re-engagement email drafts are saved directly to `x_lisa_wb_email_html` and `x_lisa_wb_email_subject` on the customer record. The agent exits cleanly and successfully, allowing sales reps to review and send them natively from Odoo.
  * **Cron & Background Ready**: If `AUTO_REPLY` is enabled (True) in Odoo company config, the agent sends emails automatically through Odoo without requiring any manual terminal inputs or approvals.
* **Semantic Memory**: Stores objections (e.g. "business closed", "switched to competitor") in Odoo's native Internal Notes (`comment`) field to permanently skip future re-engagement campaigns.
* **Self-Healing Rate Limit Retry**: Gracefully handles Gemini, Mistral, and Groq rate limits with exponential backoff.

---

## 🔄 Recent Updates & Optimizations

* **Contact Email Deduplication:** Added duplicate contact card filtering based on email address during candidate discovery, preventing duplicate drip campaigns from targeting identical contact emails.
* **Windows Connection Host Resolution Fix:** Updated connection configurations to `127.0.0.1` to prevent local DNS IPv6/IPv4 mismatch socket connection failures on Windows hosts.
* **Conditional Chatter Log Privacy:** Suppressed redundant chatter messages when `AUTO_REPLY` is ON to avoid cluttering the chatter feed with duplicate email bodies. Chatter notes are only posted when auto-reply is OFF (manual review mode).
* **Odoo Addon Custom Branding:** Upgraded the custom Odoo addon view definitions with custom branding icons/logos linked to Odoo's main home dashboard icons via `web_icon` settings.

---

## 🛠️ Tech Stack
* **Framework**: Python 3.10+, LangChain, LangGraph, Deep Agents
* **Models**: Mistral (Mistral-Large), Gemini (Gemini-1.5-Flash), Groq (Llama-3.3-70b)
* **State Persistence**: State is dynamically derived and managed natively in Odoo's `winback.campaign` table and partner records (`res.partner`).

---

## 🗄️ Stateful Odoo-Native Architecture

The agent runs completely database-free locally. It synchronizes and tracks campaign stages and schedules directly in Odoo:

1. **Campaign State Tracking**: The agent reads and updates the `winback.campaign` table in Odoo to track campaign stage (`none`, `email_1_sent`, `email_2_sent`, `email_3_sent`) and status (`active`, `draft`, `cold`, `reactivated`, `opt_out`).
2. **Timing & Cadence**: Computes scheduling delays (7-day interval) by checking the timestamp of the last sent campaign email in Odoo.
3. **Safety Checklist**: The checklist (`campaign_todos`) is tracked in-memory during the pipeline execution run, guaranteeing safety checks are completed without local file writes.
4. **Reactivation & Replies**: Scans Odoo Sales Orders and incoming chatter messages since the last campaign event to verify if they ordered or replied.

---

## 🚀 Setup & Execution

### ⚙️ Step 0 (One-Time): Install the Odoo Custom Addon

This agent requires a companion Odoo addon (`lisa_win_back_agent`) to be installed in your Odoo instance. It adds:
- Custom fields on `res.partner` for storing draft email content, campaign stage, and status
- A dedicated **Lisa AI Win-Back** configuration tab in `Settings > General Settings`
- Custom views and settings for the Win-Back Campaign Drip Campaign

**Addon repo:** 👉 [github.com/masabjatoi/Win-Back-Agent-odoo](https://github.com/masabjatoi/Win-Back-Agent-odoo)

```bash
# 1. Clone the addon into your Odoo addons path
git clone https://github.com/masabjatoi/Win-Back-Agent-odoo.git /path/to/odoo/addons/lisa_win_back_agent

# 2. Restart Odoo and update the module list
#    Settings > Technical > Update Modules List

# 3. Install the module
#    Settings > Apps > Search "lisa_win_back_agent" > Install
```

> Once installed, all campaign parameters (inactivity threshold, interval days, email count, etc.) will appear under:
> `Settings → General Settings → Lisa AI Win-Back`

---

### 1. Clone & Install Dependencies
```bash
git clone https://github.com/masabjatoi/odoo-winback-campaign-agent.git
cd odoo-winback-campaign-agent
python -m venv .venv
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # macOS / Linux
pip install -r requirements.txt
```

### 2. Configure Credentials
Copy `.env.example` to `.env` and fill in your values:
```bash
cp .env.example .env
```

The only required values are:
```ini
ODOO_URL=https://your-odoo-instance.com
ODOO_DB=your-db-name
ODOO_USERNAME=your-username
ODOO_API_KEY=your-xmlrpc-api-key

LLM_PROVIDER=mistral
MISTRAL_API_KEY=your-mistral-key
```

> **All campaign behavior settings** (inactivity threshold, email interval, max emails, promo codes, auto-send vs. manual review, recipient override) are configured directly inside Odoo:
> `Settings → General Settings → Lisa AI Win-Back`

### 3. Run the Odoo Setup Script (first time only)
This creates the `winback.campaign` table and all required custom fields on `res.company` and `res.partner` in your Odoo instance:
```bash
python setup_odoo.py
```

### 4. Run the Pipeline
```bash
python main.py
```

Optionally limit how many leads are processed (useful for testing):
```bash
python main.py --limit 5
```

---

## 🚨 Critical Operational Guidelines
1. **XML-RPC Thread Safety**: Never share a global `ServerProxy` instance across graph nodes. A fresh connection is instantiated per RPC request in `get_odoo_client()` to prevent socket collisions.
2. **Odoo 16/17 Blacklist Format**: Avoid direct checks of `is_blacklisted` in `res.partner`. Verify blacklist status directly on `mail.blacklist` querying by email address.
3. **Non-Interactive (TTY) Execution**: In automated cron environments, when manual review is enabled in Odoo (`AUTO_REPLY=False`), the agent runs in non-blocking mode, writing email drafts to Odoo Chatter. When `AUTO_REPLY=True` is enabled, the agent executes fully non-interactively without prompting or requiring any standard input redirection.
