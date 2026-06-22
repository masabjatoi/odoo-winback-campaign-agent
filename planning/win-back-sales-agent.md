# Win-Back Sales Agent

**Win-back:** Detects customers who haven't purchased for a defined period and sends a 3-step re-engagement sequence, then updates the lead/customer status based on response.

# **Win-back After Inactivity**

### Goal

Automatically reconnect with customers who have stopped purchasing and encourage them to return through a structured email sequence rather than repeated promotional messages.


---

## Trigger

The workflow starts when:

`Current Date - Last Purchase Date >= inactivity_threshold_days `

Example:

`Last Order Date: 01-Jan-2026 Threshold: 60 days  If today is 02-Mar-2026 or later → Win-back campaign starts `


---

## Workflow

### Step 1 — Email 1 (Friendly Reminder)

**When:** Customer reaches inactivity threshold.

**Purpose:** Re-establish contact without selling.

Example:

> Hi John,
>
> It's been a while since we last connected.
>
> We hope everything is going well.
>
> We're still here whenever you need us.

Actions:

* Send Email 1
* Record activity in Odoo
* Set campaign stage = Email 1 Sent


---

### Step 2 — Email 2 (Value-Based Re-engagement)

**When:** After `winback_interval_days`

**Purpose:** Give a meaningful reason to return.

Possible content:

* New product launch
* Seasonal collection
* Educational content
* Discount code

Example:

> Hi John,
>
> We recently added several new products that match your previous purchases.
>
> Use code:
>
> **WELCOME10**
>
> for your next order.

Actions:

* Send Email 2
* Log activity
* Update campaign stage


---

### Step 3 — Email 3 (Final Attempt)

**When:** After another `winback_interval_days`

**Purpose:** One final low-pressure message.

Example:

> Hi John,
>
> This will be our last reminder.
>
> If you're still interested, we'd love to help.
>
> Otherwise, we'll stop reaching out for now.

Actions:

* Send Email 3
* Mark final outreach completed


---

## Customer Responds?

Response can be:

* Places an order
* Replies to email
* Clicks campaign CTA (optional)

If response detected (places an order):

`Stop workflow Mark customer as reactivated Notify sales rep `

Status:

`Lead State = Reactivated `

If response detected (replies to email):

`Stop workflow Mark customer as replied Schedule follow-up activity for salesperson `

Status:

`Lead State = Replied `


---

## No Response?

After the final email:

`No purchase No engagement `

Actions:

`Lead Status = Cold Notify assigned sales representative Close win-back campaign `

Notification example:

> Customer John Smith did not respond to the win-back campaign and has been moved to the Cold segment.


---

## Configuration Parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| inactivity_threshold_days | Number of days without purchase before campaign starts | 60      |
| winback_interval_days | Days between emails | 7       |
| winback_offer_email2 | Offer, discount code, or promotional text for Email 2 | Optional |
| max_winback_emails | Maximum emails in sequence | 3       |
| segment_by_category | Only recommend products from previously purchased categories | Yes/No  |

## **Architecture Diagram**

 ![](attachments/71f13be0-929d-4a5f-971d-5c585c1c7b0f.png " =1440x2200")

### ✅ Features Already Present in Odoo

#### Win-Back After Inactivity

| Feature | Odoo Module | Notes |
|---------|-------------|-------|
| Customer purchase history & last order date | **Sales / Purchase** | `order_date`, `partner_id`, order status all stored natively |
| CRM lead/customer records | **CRM**     | Lead states, assigned sales reps, stage management |
| Email sending | **Email Marketing / Discuss** | Can send emails to customers |
| Activity logging on records | **CRM / Discuss** | `Log Note`, `Schedule Activity` on leads/partners |
| Scheduled actions (cron jobs) | **Technical > Automation** | Can trigger logic based on date conditions |
| Customer segmentation | **Email Marketing** | Basic segmentation by tags, last purchase, etc. |

### ❌ Features NOT Present in Odoo (Needs Custom Build)

#### Win-Back Agent — Missing Pieces

| Required Feature | Gap |
|------------------|-----|
| **Inactivity threshold logic** (`inactivity_threshold_days`) | No native "days since last purchase → trigger campaign" rule out of the box in Marketing Automation |
| **3-step drip sequence with configurable intervals** (`winback_interval_days`) | Odoo Marketing Automation supports sequences, but the specific multi-step logic tied to response detection needs custom setup |
| **Response detection** (order placed, email replied, CTA clicked → stop campaign) | Email click/reply tracking exists in Email Marketing, but **auto-stopping a sequence on response** is not built-in |
| **Automatic lead status update** (`Reactivated` / `Cold`) | No native automation that changes CRM lead state based on campaign response |
| **Sales rep notification on reactivation or cold status** | Needs a custom automated action or webhook |
| **Frequency cap** (don't send if emailed in last X days) | Not enforced natively across modules |
| **Suppression lists** (VIP, active negotiations, "no contact") | Partial via opt-out; VIP/negotiation suppression needs custom logic | 