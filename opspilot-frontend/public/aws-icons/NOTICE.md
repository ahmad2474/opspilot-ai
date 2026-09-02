# AWS icon assets

The SVGs in this directory are unmodified extracts from AWS's official
**Architecture Icons** package (release `07312026`), downloaded from
https://aws.amazon.com/architecture/icons/.

| File | Source (within the AWS package) |
|---|---|
| ec2.svg | Architecture-Service-Icons / Arch_Compute / Arch_Amazon-EC2 |
| lambda.svg | Architecture-Service-Icons / Arch_Compute / Arch_AWS-Lambda |
| sagemaker.svg | Architecture-Service-Icons / Arch_Artificial-Intelligence / Arch_Amazon-SageMaker-AI |
| ebs.svg | Architecture-Service-Icons / Arch_Storage / Arch_Amazon-Elastic-Block-Store |
| rds.svg | Architecture-Service-Icons / Arch_Databases / Arch_Amazon-RDS |
| dynamodb.svg | Architecture-Service-Icons / Arch_Databases / Arch_Amazon-DynamoDB |
| elasticache.svg | Architecture-Service-Icons / Arch_Databases / Arch_Amazon-ElastiCache |
| redshift.svg | Architecture-Service-Icons / Arch_Analytics / Arch_Amazon-Redshift |
| opensearch.svg | Architecture-Service-Icons / Arch_Analytics / Arch_Amazon-OpenSearch-Service |
| elb.svg | Architecture-Service-Icons / Arch_Networking-Content-Delivery / Arch_Elastic-Load-Balancing |
| nat_gateway.svg | Resource-Icons / Res_Networking-Content-Delivery / Res_Amazon-VPC_NAT-Gateway |
| eip.svg | Resource-Icons / Res_Compute / Res_Amazon-EC2_Elastic-IP-Address |
| api_gateway.svg | Architecture-Service-Icons / Arch_Networking-Content-Delivery / Arch_Amazon-API-Gateway |
| cloudfront.svg | Architecture-Service-Icons / Arch_Networking-Content-Delivery / Arch_Amazon-CloudFront |
| kinesis.svg | Architecture-Service-Icons / Arch_Analytics / Arch_Amazon-Kinesis |
| subnet.svg | Architecture-Group-Icons / Private-subnet |
| vpc.svg | Architecture-Service-Icons / Arch_Networking-Content-Delivery / Arch_Amazon-Virtual-Private-Cloud |
| iam_role.svg | Resource-Icons / Res_Security-Identity / Res_AWS-Identity-Access-Management_Role |

**No icon exists for `security_group`** — AWS's current icon package has no
standalone Security Group icon. That type falls back to a generic
placeholder glyph in the app (see `NoIconGlyph` in `components/GalaxyView.tsx`),
not a fabricated one.

## Usage terms (read before adding more icons or changing how they're used)

AWS's icons page (as of this writing) explicitly authorizes use in
architecture diagrams, whitepapers, presentations, data sheets, and
posters. It does not explicitly address embedding icons as live UI
iconography in a third-party product, and AWS's trademark guidelines
(https://aws.amazon.com/trademark-guidelines/) separately require, among
other things, not altering an icon's proportions/color and not
misrepresenting a relationship with AWS.

**Decision (2026-09-02, user choice):** ship the icons unmodified in the
live dashboard, and carry an explicit "not affiliated with, endorsed by,
or sponsored by AWS" disclosure at the point of use (the galaxy view's
legend) and in the README. This is a reasonable-faith reading of the
permitted use cases, not an explicit AWS blessing — re-check AWS's terms
before any wider redistribution of this project (e.g. a hosted SaaS
version) and reconsider if AWS ever publishes guidance that contradicts it.
