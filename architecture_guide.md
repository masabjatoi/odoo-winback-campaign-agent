# Win-Back Sales Campaign Agent: High-Level Architecture

This document provides a simple, high-level overview of how the Win-Back Sales Campaign Agent works. It explains the flow from discovering inactive customers to sending emails and handling customer responses.

---

## High-Level Workflow Diagram

![High-Level Workflow Diagram](architecture_diagram.png)

```mermaid
graph TD
    %% Discovery Phase
    Start([1. Discovery & Enrollment]) --> |Fetch Inactive Candidates| Candidates{Reconstruct State}
    Candidates --> |Determine Active Leads| Constraints{2. Automated Checks}

    %% Automated Checks Phase
    Constraints -->|Not Due Yet| Skip[Skip Customer]
    Constraints -->|Archived / Blacklisted| HaltCold[Set Status to Cold/Opt-Out]
    Constraints -->|VIP / suppressed| HaltSuppressed[Set Status to Opt-Out]
    Constraints -->|Objections in Memories| HaltMemory[Set Status to Cold/Opt-Out]
    Constraints -->|New Order Placed| Reactivated[Reactivated: Halt & Notify Sales]
    Constraints -->|Customer Replied| Reply[Replied: Halt & Classify Reply]
    Constraints -->|Due & Eligible| AICopywriter[3. AI Copywriter]

    %% Sending & Review Phase
    AICopywriter -->|Drafts Personalized HTML Email| Route{4. Auto-Reply Mode?}
    Route -->|Yes: AUTO_REPLY=True| SendEmail[5. Send Email & Log]
    Route -->|No: AUTO_REPLY=False| SaveDraft[5. Save Draft to Odoo Chatter]

    %% Sending & Output Phase
    SendEmail --> |Email Sent| UpdateState[6. Update State]
    SaveDraft --> |Draft Saved| UpdateState
    UpdateState --> |Log Chatter / Native Note| OdooChatter[Odoo CRM / Chatter]

    %% Styles
    classDef main fill:#3c3489,stroke:#7b6fed,color:#fff;
    classDef check fill:#e26210,stroke:#f0997b,color:#fff;
    classDef success fill:#085041,stroke:#5dcaa5,color:#fff;
    classDef skipped fill:#444441,stroke:#b4b2a9,color:#fff;
    
    class Start,AICopywriter,SendEmail main;
    class Constraints check;
    class Reactivated,Reply success;
    class Skip,HaltCold,HaltSuppressed,HaltMemory skipped;
```

---

## Phase Explanations

### 1. Discovery & Enrollment
The system queries Odoo to find customers who haven't placed an order in **60+ days**. It dynamically reconstructs each candidate's campaign state (from chatter logs in production, or from a test JSON state file in testing) and enqueues them if they are active in the campaign.

### 2. Fast Constraint Checks
Before any heavy AI calculations occur, the system runs through a checklist:
* **Timing**: Is it time to send the next email? (Drips are spaced 7 days apart).
* **Odoo Status**: Is the customer still active and not blacklisted?
* **Suppression**: Is the customer tagged as a VIP, or in an active sales negotiation?
* **Reactivation**: Has the customer placed a new order since we last reached out? If yes, stop the campaign and alert the salesperson.
* **Replies**: Has the customer replied to us? If yes, stop the campaign and notify the salesperson.

### 3. AI Email Drafting (The Spoke)
If a customer passes all checks and is due for an email, the system wakes up the **AI Copywriter**. The AI looks at what product categories the customer previously bought to draft a highly personalized, low-pressure email signature and re-engagement copy.

### 4. Review & Dispatch Mode (Odoo Integration)
The system routes the email based on the Odoo configuration:
* **Auto-Reply Mode (`AUTO_REPLY` is True)**: The email is sent automatically to the customer without any manual intervention.
* **Manual Review Mode (`AUTO_REPLY` is False)**: The email is saved as a draft on the Odoo customer record (`res.partner`) and posted to the Chatter log, allowing sales representatives to review, edit, and send it natively from inside the Odoo user interface.

### 5. Send & Log (Odoo Integration)
* **Outreach**: The email is sent to the customer (via Gmail in test mode, or via Odoo in production).
* **Logging**: A record of the email is written to the customer's Odoo chatter history so the salesperson can see the full communications timeline.
* **Scheduler**: The next check is scheduled for 7 days in the future by relying on the chatter log message timestamp.

