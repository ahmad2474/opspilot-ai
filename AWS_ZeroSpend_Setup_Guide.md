# AWS Setup Guide — Zero-Spend Plan for OpsPilot AI

Goal: build and demo the project without any charge ever landing on your card. This is achievable — AWS's current account structure has a real, built-in mechanism for this, not just discipline. Read Section 1 before creating anything.

---

## 1. The single most important decision: choose the "Free Plan," not "Paid Plan"

When you sign up, AWS makes you pick one of two plans. This choice is the actual safety net — everything else in this guide is secondary to it.

| | Free Plan | Paid Plan |
|---|---|---|
| Credits | $100 immediately, up to $200 with onboarding tasks | Same $100–$200 |
| What happens if you exceed credits | **Account closes automatically. You are never billed.** | You are billed standard rates for anything beyond credits |
| Duration | 6 months or until credits run out, whichever first | No time limit |
| Access | Most services, some restricted (Reserved Instances, Marketplace, Savings Plans — none of which this project needs) | Everything |

**Pick Free Plan.** On the Free Plan, AWS's own documentation is explicit: you won't be charged, period — the account just closes if you run out of credits or hit 6 months. That structurally satisfies "no card charges" better than any amount of manual vigilance on a Paid Plan.

**Traps that silently force you onto Paid Plan (avoid these for this project):**
- Joining an AWS Organization
- Setting up AWS Control Tower
- Joining AWS Partner Network
- Signing up for a Professional Services engagement

None of these are needed for OpsPilot. Don't touch them.

**Note on the card itself:** AWS requires a card at signup on both plans, purely for identity verification. It charges $1 and refunds it within days. On the Free Plan, that's the only amount that will ever touch your card.

---

## 1a. Continuing setup — you're here: root account created, root MFA on, admin IAM user created

Next steps in order. Do these before touching any of the 7 project services.

### Step 1 — Stop using root, sign in as your IAM admin user from now on
- Sign out of root
- Sign back in using the **IAM user sign-in URL** (Account → shown on IAM dashboard, format `https://<account-id>.signin.aws.amazon.com/console`)
- Enable MFA on this IAM admin user too (IAM → Users → your user → Security credentials → Assign MFA device) — same authenticator app, different entry
- From here on, root is only for the handful of things that require it (closing the account, changing support plan, and the billing-access toggle below) — never for daily work

### Step 1a — Activate IAM access to the Billing console (root-only, one-time)
This is a hard AWS restriction: **no IAM user, not even one with full AdministratorAccess, can see or use this setting.** Only root can flip it, once, ever.
- Sign in as root (briefly, just for this)
- Top-right → account name → **Account**
- Scroll to **"IAM User and Role Access to Billing Information"** → Edit → check **Activate IAM Access** → Update
- Sign back out of root immediately after
- Your admin IAM user (which already has `AdministratorAccess`) can now see Billing/Cost Management pages — no separate policy needed since `AdministratorAccess` already covers billing actions

### Step 2 — Set your budgets and alerts (before creating any resource)
- Billing and Cost Management → **Budgets** → Create budget
  - Budget 1: Cost budget, $1 threshold, alert at 100% actual spend
  - Budget 2: Cost budget, alert at 50% and 90% of your total credit balance
- Billing preferences → turn on **"Receive Free Tier usage alerts"**, enter your email
- Billing → **Cost Anomaly Detection** → create a monitor (free, one click)

### Step 3 — Pick your region and stick to it
- Use one region for everything (e.g. `us-east-1` or whichever is closest/cheapest for you) — top-right region selector in the console
- Running resources across multiple regions splits your free-tier allowances for no benefit here

### Step 4 — Create the scoped-down IAM user your application will actually use
Don't let your FastAPI backend use the admin credentials.
- IAM → Users → Create user → name it something like `opspilot-app`
- **No console access needed** — this user only needs programmatic access
- Skip attaching a managed policy; instead create a custom policy (IAM → Policies → Create policy → JSON tab) using the read-only JSON from Section 4 below, and attach it to this user
- IAM → Users → `opspilot-app` → Security credentials → Create access key → choose **"Application running outside AWS"** → save the Access Key ID and Secret Access Key immediately (secret is shown once)
- Put these in your project's `.env` (git-ignored), never in code:
  ```
  AWS_ACCESS_KEY_ID=...
  AWS_SECRET_ACCESS_KEY=...
  AWS_REGION=us-east-1
  ```

### Step 5 — Install and configure the AWS CLI locally (useful for quick checks outside your app)
- Install: `pip install awscli --break-system-packages` or your OS package manager
- Run `aws configure --profile opspilot` and enter the `opspilot-app` keys + region
- Test: `aws sts get-caller-identity --profile opspilot` — confirms the credentials work before you write any Python

### Step 6 — Launch the core resource for deep investigation: one EC2 instance
- EC2 → Launch instance → Amazon Linux 2023, instance type **t3.micro** (free-tier eligible)
- Create a new key pair, download the `.pem` file, keep it outside your repo
- Network: use the **default VPC**, a **public subnet** — do not create a NAT Gateway
- Security group: allow SSH (port 22) only from your own IP, not `0.0.0.0/0`
- Launch it, note the instance ID — this is what your agent will investigate
- **Stop it** (not terminate) whenever you're not actively developing/demoing

### Step 7 — Create the lightweight resources for your dashboard cards
Quick one-time setup, each takes a minute or two:
- **S3**: create one bucket (name must be globally unique) — gives your dashboard something to list
- **DynamoDB**: create one table (e.g. `opspilot-investigations`, partition key `id`) — on-demand billing mode, stays inside the 25GB Always Free allowance at this scale
- **SNS**: create one topic (e.g. `opspilot-alerts`) — optionally subscribe your own email to it, so a future "agent sends an alert" demo actually delivers
- **Lambda**: create one simple function (any runtime, even just the default "hello world" blueprint) — just needs to exist for your dashboard card to list it
- **CloudTrail**: check whether a trail already exists (Account → CloudTrail → Trails) — many accounts have one on by default; if not, create one, since management events are free for 90 days regardless
- **RDS**: create the smallest free-tier-eligible instance (`db.t3.micro` or `db.t4g.micro`), single-AZ, 20GB storage — **this is the one resource to stop/monitor most carefully**, since it draws from your credit balance, not Always Free

### Step 8 — Verify nothing is running that shouldn't be
- EC2 → Instances: confirm only your one t3.micro exists
- EC2 → Elastic IPs: confirm none are unattached
- VPC → NAT Gateways: confirm zero exist
- Billing → Bills: check current forecasted spend shows $0 projected beyond credits

Once these 8 steps are done, you have a working, scoped, budget-alerted AWS account ready for Phase 1 of the roadmap (FastAPI + EC2/CloudWatch tools).

---

## 2. Account creation checklist (do this before writing any code)

1. Go to `aws.amazon.com/free` → Create an AWS Account
2. Complete identity verification (card + phone verification)
3. **Select Free Plan** when prompted (Section 1)
4. Log in to the root account once, then immediately:
   - Enable MFA on the root user
   - Do not use root for anything else again — create an IAM user next
5. Create an IAM user for yourself with **AdministratorAccess** temporarily (just to finish setup), enable MFA on it too
6. Set your **budget and alerts** (Section 3) — do this before launching a single resource
7. Complete the 5 onboarding tasks if you want the extra $100 (launch+terminate EC2, configure RDS, deploy Lambda, test Bedrock, set a cost budget) — optional, but free money and each one doubles as a sanity check that your account works
8. Create the **scoped-down IAM user** the agent's AWS credentials will actually use (Section 4) — don't let your app use the admin user

---

## 3. Budget alerts — set these immediately, before any resource exists

Go to **Billing and Cost Management → Budgets → Create budget**.

- **Zero-spend budget**: $1 threshold, alert at 100% (actual spend). This is one of the tasks that pays you $20 in credit.
- **Credit-usage budget**: alert at 50% and 90% of your credit balance, so you have warning before the account auto-closes.
- Turn on **Free Tier usage alerts** (Billing preferences) — separate from Budgets, these specifically warn when you're approaching a monthly free-tier ceiling (e.g. EC2 750 hrs).
- Enable **Cost Anomaly Detection** (free, one click, Billing → Cost Anomaly Detection) — flags unusual spend same-day.

These alerts don't stop spending by themselves (see Section 6 for that), but combined with the Free Plan's hard ceiling, they mean you'll never be surprised.

---

## 4. IAM setup scoped to this project

Don't give your FastAPI backend admin credentials. Create a dedicated IAM user (or role, if you deploy to EC2 later) with a **custom read-only policy** matching exactly what your MVP touches — narrower than AWS's managed `ReadOnlyAccess`.

Example policy covering all 7 project services (EC2 + CloudWatch for deep investigation; Lambda, S3, DynamoDB, SNS, CloudTrail, RDS for dashboard cards):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ec2:Describe*",
        "cloudwatch:GetMetricData",
        "cloudwatch:GetMetricStatistics",
        "cloudwatch:ListMetrics",
        "cloudwatch:DescribeAlarms",
        "lambda:GetFunction",
        "lambda:ListFunctions",
        "s3:ListAllMyBuckets",
        "s3:GetBucketLocation",
        "s3:ListBucket",
        "dynamodb:ListTables",
        "dynamodb:DescribeTable",
        "sns:ListTopics",
        "sns:ListSubscriptions",
        "cloudtrail:LookupEvents",
        "rds:DescribeDBInstances"
      ],
      "Resource": "*"
    }
  ]
}
```

Keep this list explicit rather than using wildcards like `ec2:*` — it's also a good thing to point to in your README/ADR as evidence of least-privilege thinking, especially now that you're touching 7 services with one credential.

Generate an access key for this IAM user, store it in your `.env` (git-ignored), never in code.

---

## 5. What to actually build — mapped to free tier / credits

| Resource | Free tier / credit coverage | Notes for this project |
|---|---|---|
| **EC2** | 750 hrs/month t2.micro or t3.micro is the "Always Free"-adjacent allowance now drawn from your credit balance | Launch **one** t3.micro Linux instance as your "thing to investigate" for deep agent reasoning. Keep it stopped when not actively demoing. |
| **EBS** | 30 GB gp2/gp3 storage | Comes attached to the EC2 instance's root volume — don't add extra volumes you don't need. |
| **CloudWatch** | 10 custom metrics, 10 alarms, 1M API requests/month, 5 GB log ingestion — **Always Free**, doesn't draw from credits | Core to the deep investigation flow, paired with EC2. Safest service in the whole project. |
| **Lambda** | 1M requests + 400,000 GB-seconds/month — **Always Free** | Dashboard card only (function list + status) — near-zero cost regardless of plan. |
| **S3** | 5 GB storage, 20k GET / 2k PUT requests — **Always Free** | Dashboard card (bucket list, size). Also useful later for storing investigation logs if you want persistence. |
| **DynamoDB** | 25 GB storage, 25 RCU/WCU — **Always Free** | Dashboard card (table list, item count). Zero billing risk on either plan. |
| **SNS** | 1M requests/month — **Always Free** | Dashboard card (topic/subscription count). Also usable later for "agent sends an alert" demo. |
| **CloudTrail** | Last 90 days of management events — **free** on both plans | Dashboard card as a read-only audit feed of recent AWS activity — doubles as a trust signal ("see what the agent's environment has been doing"). |
| **RDS** | **Not Always Free** — draws from your $100–200 credit balance | Dashboard card only (instance status/engine/size). This is the one service to actually watch on your budget alerts — use the smallest free-tier-eligible instance class (db.t3.micro / db.t4g.micro) and stop it when not demoing. |

**Cost-risk ranking of your 7 services (safest to watch-carefully):** Lambda / S3 / DynamoDB / SNS / CloudTrail (Always Free, zero risk) → CloudWatch (Always Free, essentially zero risk) → EC2 (window-limited but generous, low risk if you stop/terminate) → **RDS (the only one drawing real credit, keep it small and off when idle)**.

---

## 6. Guardrails against ever spending anything

1. **Stay on Free Plan.** (Section 1 — this is the actual hard stop.)
2. **Terminate, don't stop**, EC2 instances when you're done for the day if you want to avoid any EBS accumulation — or just leave one instance stopped (compute charges stop, only small EBS storage cost remains, which the 30GB free allowance covers anyway).
3. **Never create a NAT Gateway.** Not covered by free tier at all (~$33/month). Keep your VPC simple — a default VPC with a public subnet is enough for a t3.micro demo instance.
4. **Delete unattached Elastic IPs.** Free while attached to a running instance; billed if idle/unattached.
5. **Set CloudWatch Logs retention** on any log group you create (7–14 days is plenty for a demo) — default is "never expire," which quietly accumulates storage cost.
6. **One region only.** The 750 EC2 hours are a global pool — running instances in two regions burns it twice as fast for no benefit here.
7. **End-of-session checklist** (5 minutes, do this every time you stop working):
   - EC2 → Instances → confirm nothing unnecessary is running
   - EC2 → Elastic IPs → release anything unattached
   - VPC → NAT Gateways → confirm none exist
   - CloudWatch → Log Groups → confirm retention is set

---

## 7. Timeline reality check

Your credits last **6 months or until spent, whichever first**. For a 3–4 week portfolio build with a t3.micro instance running only during active dev/demo sessions, you will not come close to exhausting $100–200 in credits — CloudWatch and Lambda usage in this project are Always-Free regardless. The 6-month window is the real constraint to plan around, not the dollar amount: finish and record your demo well inside that window, since the account can auto-close after.

---

## 8. Quick reference — what's genuinely free forever vs. free during the window

**Always Free (works even after account closes and you make a new one on a fresh AWS account, or after Paid Plan credits run out):**
- Lambda (1M requests/month)
- CloudWatch (10 metrics, 10 alarms, 1M API calls/month)
- DynamoDB (25 GB) — not used in v1 but relevant for your "Future Enhancements" section

**Free only within the 6-month / credit window (Free Plan):**
- EC2 (750 hrs/month t3.micro)
- EBS (30 GB)
- Any ECS compute running on EC2 under the hood
