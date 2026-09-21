variable "aws_region" {
  description = "Region to deploy the demo instance into."
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "Local AWS CLI profile with the OpsPilotDeployPolicy permissions."
  type        = string
  default     = "opspilot-deploy"
}

variable "instance_type" {
  description = "EC2 instance type. t3.small (2GB RAM) so `next build` doesn't OOM."
  type        = string
  default     = "t3.small"
}

variable "project_name" {
  description = "Prefix used to tag and name every resource this config creates."
  type        = string
  default     = "opspilot-demo"
}

variable "root_volume_gb" {
  description = "Root EBS volume size — needs room for two Docker build contexts + images."
  type        = number
  default     = 20
}
