# DynamoDB Terraform module (optional)

Provisions the same 3 DynamoDB tables `scripts/provision_tables.py` does — same schema, same
billing mode, same table names by default. Two paths to the same result; pick whichever fits your
workflow, both are fully supported. `scripts/setup.py` uses the Python path by default.

## Usage

```bash
cd scripts/terraform
terraform init
terraform apply
```

Set `aws_region` (and the table-name variables, if you've overridden the corresponding
`OPSPILOT_*_TABLE` env vars in `opspilot-backend/.env`) via `-var` flags, a `terraform.tfvars`
file, or `TF_VAR_*` environment variables — see `variables.tf` for every option and its default.

Reads AWS credentials the standard Terraform/AWS-provider way (environment variables, `~/.aws/credentials`,
etc.) — same credentials you'd put in `opspilot-backend/.env` work here too, since table creation
only needs `dynamodb:CreateTable`/`dynamodb:DescribeTable`, a one-time setup permission separate
from the app's own runtime IAM policy (`docs/iam-policy.json`, deliberately read-only forever).

**Not validated with the Terraform CLI** (not installed in the environment this was written in) —
syntax reviewed by hand against the AWS provider's documented `aws_dynamodb_table` resource shape,
but run `terraform validate` yourself before `apply` in a real account.

## What this does *not* do

Only the 3 DynamoDB tables. It doesn't create the IAM user/policy for the app's own AWS access —
`scripts/setup.py`'s boto3 path or the manual IAM console click-through (see the main README's
quickstart) both already cover that, and there's no reason to maintain that logic a third time.
