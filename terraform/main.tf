terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "ap-southeast-2"
}

resource "aws_budgets_budget" "monthly_cost_budget" {
  name              = "monthly-cost-budget"
  budget_type       = "COST"
  time_unit         = "MONTHLY"
  limit_amount      = "5"
  limit_unit        = "USD"
  time_period_start = "2025-08-27_00:00"

  notification {
    comparison_operator        = "GREATER_THAN"
    notification_type          = "ACTUAL"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    subscriber_email_addresses = ["quitefrankli@hotmail.com"]
  }
}

resource "aws_security_group" "lazy_wombat_sg" {
	# vpc_id = aws_vpc.main.id
    # id          = "sg-065a3ba867fb456ad"
    egress      = [
        {
			description = ""
            cidr_blocks      = [
                "0.0.0.0/0",
            ]
            from_port        = 0
            ipv6_cidr_blocks = []
            prefix_list_ids  = []
            protocol         = "-1"
            security_groups  = []
            self             = false
            to_port          = 0
        },
    ]
    ingress     = [
        {
			description = ""
            cidr_blocks      = [
                "0.0.0.0/0",
            ]
            from_port        = 22
            ipv6_cidr_blocks = []
            prefix_list_ids  = []
            protocol         = "tcp"
            security_groups  = []
            self             = false
            to_port          = 22
        },
        {
			description = ""
            cidr_blocks      = [
                "0.0.0.0/0",
            ]
            from_port        = 443
            ipv6_cidr_blocks = []
            prefix_list_ids  = []
            protocol         = "tcp"
            security_groups  = []
            self             = false
            to_port          = 443
        },
        {
			description = ""
            cidr_blocks      = [
                "0.0.0.0/0",
            ]
            from_port        = 80
            ipv6_cidr_blocks = []
            prefix_list_ids  = []
            protocol         = "tcp"
            security_groups  = []
            self             = false
            to_port          = 80
        },
    ]
	name        = "lazy_wombat_sg"
    # owner_id    = "857137613025"
    tags        = {}
    tags_all    = {}
    # vpc_id      = "vpc-0818f62ee9ff2713e"
}

resource "aws_instance" "lazy_wombat" {
#   ami = "ami-0f40683c0420d4b21" # Debian amd64
  ami = "ami-0279a86684f669718" # Ubuntu amd64
  # arn                                  = "arn:aws:ec2:ap-southeast-2:857137613025:instance/i-00f1c8d9927f577d7"
  associate_public_ip_address = true
  availability_zone           = "ap-southeast-2c"
  # cpu_core_count                       = 1
  # cpu_threads_per_core                 = 1
  disable_api_stop        = false
  disable_api_termination = false
  ebs_optimized           = false
  get_password_data       = false
  hibernation             = false
  # host_id                              = [90mnull[0m[0m
  # iam_instance_profile                 = [90mnull[0m[0m
  # id                                   = "i-00f1c8d9927f577d7"
  instance_initiated_shutdown_behavior = "stop"
  # instance_lifecycle                   = [90mnull[0m[0m
  # instance_state                       = "running"
  instance_type = "t2.micro"
  # ipv6_address_count                   = 0
  # ipv6_addresses                       = []
  key_name   = "lazy_wombat_key_pair"
  monitoring = false
  # outpost_arn                          = [90mnull[0m[0m
  # password_data                        = [90mnull[0m[0m
  # placement_group                      = [90mnull[0m[0m
#   placement_partition_number = 0
  # primary_network_interface_id         = "eni-0d450b49a02dd2d0c"
  # private_dns                          = "ip-172-31-18-225.ap-southeast-2.compute.internal"
  # private_ip                           = "172.31.18.225"
  # public_dns                           = "ec2-3-107-233-129.ap-southeast-2.compute.amazonaws.com"
  # public_ip                            = "3.107.233.129"
#   secondary_private_ips = []
#   security_groups = [
#     "lazy_wombat_sg"
#   ]
  vpc_security_group_ids = [aws_security_group.lazy_wombat_sg.id]
  source_dest_check = true
  # spot_instance_request_id             = [90mnull[0m[0m
  # subnet_id                            = "subnet-0eaaae209c852e84e"
  tags     = {
	Name = "lazy_wombat_server"
  }
  tags_all = {}
#   tenancy  = "default"
  # vpc_security_group_ids               = [
  #     "sg-065a3ba867fb456ad",
  # ]

#   capacity_reservation_specification {
#     capacity_reservation_preference = "open"
#   }

  # cpu_options {
  #     amd_sev_snp      = [90mnull[0m[0m
  #     core_count       = 1
  #     threads_per_core = 1
  # }

#   credit_specification {
#     cpu_credits = "standard"
#   }

#   enclave_options {
#     enabled = false
#   }

#   maintenance_options {
#     auto_recovery = "default"
#   }

#   metadata_options {
#     http_endpoint               = "enabled"
#     http_protocol_ipv6          = "disabled"
#     http_put_response_hop_limit = 2
#     http_tokens                 = "required"
#     instance_metadata_tags      = "disabled"
#   }

#   private_dns_name_options {
#     enable_resource_name_dns_a_record    = true
#     enable_resource_name_dns_aaaa_record = false
#     hostname_type                        = "ip-name"
#   }

  # root_block_device {
  #     delete_on_termination = true
  #     device_name           = "/dev/xvda"
  #     encrypted             = false
  #     iops                  = 3000
  #     kms_key_id            = [90mnull[0m[0m
  #     tags                  = {}
  #     tags_all              = {}
  #     throughput            = 125
  #     volume_id             = "vol-03a1ae9fc1169a3bc"
  #     volume_size           = 8
  #     volume_type           = "gp3"
  # }
}

resource "aws_key_pair" "lazy_wombat_key_pair" {
	key_name = "lazy_wombat_key_pair"
  	public_key = file("~/.ssh/id_rsa.pub")
}

resource "aws_eip" "lazy_wombat_eip" {
	tags = {
		Name = "lazy_wombat_eip"
	}
}

resource "aws_eip_association" "eip_assoc" {
	instance_id   = aws_instance.lazy_wombat.id
	allocation_id = aws_eip.lazy_wombat_eip.id
}

output "elastic_ip" {
  value = aws_eip.lazy_wombat_eip.public_ip
}