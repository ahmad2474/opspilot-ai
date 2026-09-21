output "public_ip" {
  description = "Elastic IP — frontend at http://<ip>:3000, backend at http://<ip>:8000"
  value       = aws_eip.this.public_ip
}

output "instance_id" {
  value = aws_instance.this.id
}

output "ssh_command" {
  value = "ssh -i ${local_sensitive_file.private_key.filename} ubuntu@${aws_eip.this.public_ip}"
}
