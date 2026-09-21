# Deploying OpsPilot AI on AWS

One piece: a single EC2 instance running both `opspilot-frontend` and `opspilot-backend` as Docker
containers via `docker-compose.prod.yml`, provisioned by the Terraform module in
[`infra/`](../infra/). Everything else — the DynamoDB tables, the read-only AWS scanning
credentials, LLM keys — is unchanged from local dev; deploy just copies the same `.env` files over.

**Cost:** a `t3.small` in `us-east-1` runs about $0.02/hr on-demand (or $0 if your account is still
within its first-12-months free tier). `terraform destroy` removes everything when you're not using
it — nothing bills while it's destroyed.

---

## 0. Prerequisites

- Terraform >= 1.5, and the AWS CLI.
- An IAM user with EC2 permissions, **separate** from the read-only `opspilot-readonly` user the
  app itself uses to scan your account (see `docs/iam-policy.json`) — this one provisions
  infrastructure, not the same job. Least-privilege policy:

  ```json
  {
    "Version": "2012-10-17",
    "Statement": [
      {
        "Sid": "OpsPilotDeployEC2",
        "Effect": "Allow",
        "Action": [
          "ec2:RunInstances",
          "ec2:TerminateInstances",
          "ec2:StartInstances",
          "ec2:StopInstances",
          "ec2:Describe*",
          "ec2:CreateSecurityGroup",
          "ec2:DeleteSecurityGroup",
          "ec2:AuthorizeSecurityGroupIngress",
          "ec2:RevokeSecurityGroupIngress",
          "ec2:AuthorizeSecurityGroupEgress",
          "ec2:RevokeSecurityGroupEgress",
          "ec2:CreateKeyPair",
          "ec2:ImportKeyPair",
          "ec2:DeleteKeyPair",
          "ec2:AllocateAddress",
          "ec2:ReleaseAddress",
          "ec2:AssociateAddress",
          "ec2:DisassociateAddress",
          "ec2:CreateTags",
          "ec2:DeleteTags"
        ],
        "Resource": "*"
      }
    ]
  }
  ```

  Create the user (IAM console → *Create user* → no console access → attach the policy above as a
  customer-managed policy → *Create access key*), then configure a local profile for it — this
  keeps it separate from any other AWS credentials already on your machine:

  ```bash
  aws configure --profile opspilot-deploy
  # paste the access key id / secret, region us-east-1
  ```

- Your local `opspilot-backend/.env` and `opspilot-frontend/.env.local` already populated (via
  `scripts/setup.py` — see [Running it locally](../README.md#running-it-locally)). Deploy reuses
  those values as-is, only overriding the three URL-dependent fields in step 4.

---

## 1. Provision the instance

```bash
cd infra
terraform init
AWS_PROFILE=opspilot-deploy terraform plan -out=tfplan
AWS_PROFILE=opspilot-deploy terraform apply tfplan
```

This creates one `t3.small` Ubuntu instance (Docker installed via `cloud-init` on first boot), a
security group (SSH from your current IP only, ports 3000/8000 open to the world for the app), an
Elastic IP, and an SSH key pair — the private key is written to `infra/opspilot-demo-key.pem`
(gitignored, never commit it).

Note the `public_ip` in the output — every command below uses it.

```bash
chmod 600 infra/opspilot-demo-key.pem
```

---

## 2. Wait for Docker (cloud-init)

```bash
IP=<public_ip from step 1>   # plain value, no angle brackets
for i in $(seq 1 20); do
  ssh -i infra/opspilot-demo-key.pem -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 ubuntu@$IP \
    'test -f /home/ubuntu/cloud-init-done && echo READY' 2>/dev/null | grep -q READY && break
  sleep 10
done
```

---

## 3. Push the code

```bash
ssh -i infra/opspilot-demo-key.pem ubuntu@$IP 'mkdir -p ~/opspilot-ai'
rsync -az --delete \
  --exclude='.git' --exclude='node_modules' --exclude='.next' --exclude='.venv' \
  --exclude='__pycache__' --exclude='.pytest_cache' --exclude='.ruff_cache' \
  --exclude='infra' --exclude='*.pem' --exclude='.DS_Store' --exclude='tsconfig.tsbuildinfo' \
  -e "ssh -i infra/opspilot-demo-key.pem -o StrictHostKeyChecking=accept-new" \
  ./ ubuntu@$IP:~/opspilot-ai/
```

---

## 4. Point the app at its public IP

The three values that have to match the instance's actual address — everything else in your
`.env` files carries over unchanged:

```bash
KEY=infra/opspilot-demo-key.pem
ssh -i $KEY ubuntu@$IP "
sed -i 's#^OPSPILOT_CORS_ORIGINS=.*#OPSPILOT_CORS_ORIGINS=http://$IP:3000#' ~/opspilot-ai/opspilot-backend/.env
sed -i 's#^NEXT_PUBLIC_API_BASE_URL=.*#NEXT_PUBLIC_API_BASE_URL=http://$IP:8000#' ~/opspilot-ai/opspilot-frontend/.env.local
sed -i 's#^NEXTAUTH_URL=.*#NEXTAUTH_URL=http://$IP:3000#' ~/opspilot-ai/opspilot-frontend/.env.local
echo 'NEXT_PUBLIC_API_BASE_URL=http://$IP:8000' > ~/opspilot-ai/.env
"
```

---

## 5. Build and start

```bash
ssh -i $KEY ubuntu@$IP "cd ~/opspilot-ai && sudo docker compose -f docker-compose.prod.yml up -d --build"
```

First build takes a few minutes (installs Python + Node dependencies fresh on the new box).
Subsequent rebuilds on the same instance are faster (Docker layer cache).

---

## 6. Smoke test

```bash
curl http://$IP:8000/health   # -> {"status":"ok"}
curl -I http://$IP:3000/      # -> 200, or a redirect to /login
```

Then open `http://$IP:3000` in a browser, sign in, run a galaxy scan, and ask the chat "what's
running in us-east-1".

### If something's off

| Symptom | Cause |
|---|---|
| `ssh` hangs or refuses | Cloud-init is still installing Docker — retry step 2. Or your IP changed since `terraform apply` (the security group only allows the IP you applied from) — `terraform apply` again to refresh it. |
| Every API call 401s after login | `AUTH_SHARED_SECRET` or `ADMIN_EMAIL` differ between the backend and frontend `.env` files. |
| Browser console shows CORS errors | `OPSPILOT_CORS_ORIGINS` doesn't exactly match the frontend origin (scheme + host + port, no trailing slash). |
| Scan returns empty / AccessDenied | The AWS *scanning* keys (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` in `opspilot-backend/.env`) are wrong, or the policy from `docs/iam-policy.json` isn't attached to that user — this is unrelated to the `opspilot-deploy` user from step 0, which only provisions infrastructure. |
| SSH refuses a *new* instance's IP with a host-key warning | A previous instance used to own that IP in your `~/.ssh/known_hosts`. `-o StrictHostKeyChecking=accept-new` (already in the commands above) handles this automatically. |

---

## Destroy and recreate

```bash
cd infra
AWS_PROFILE=opspilot-deploy terraform destroy
```

Removes the instance, Elastic IP, security group, and key pair in one shot — nothing left billing.
To stand it back up, repeat steps 1–6; you'll get a **new IP** each time (Elastic IPs aren't
preserved across destroy/create), and `opspilot-demo-key.pem` is regenerated at the same path,
overwriting the old one.

To pause without destroying (keeps the same IP, no rebuild needed next time):
`sudo docker compose -f docker-compose.prod.yml stop` over SSH, then `... up -d` again later — the
stopped instance only bills for its EBS volume (a few cents/month), not compute.

---

## `infra/` vs `scripts/terraform/`

Same tool, two unrelated jobs — don't confuse them:

- **[`infra/`](../infra/)** — provisions *where the app runs*: the EC2 instance, its security
  group, its Elastic IP. This doc.
- **[`scripts/terraform/`](../scripts/terraform/)** — provisions *app data*: the 3 DynamoDB tables
  (`opspilot-investigations`, `opspilot-mcp-tokens`, `opspilot-audit-log`). See
  [Running it locally](../README.md#running-it-locally).

They keep separate Terraform state — applying or destroying one never touches the other.
