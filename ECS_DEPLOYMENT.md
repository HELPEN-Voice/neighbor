# ECS Deployment Guide for Neighbor Intelligence Pipeline

## Overview

This application is a **batch processing script** that:
- Takes lat/lon coordinates as input
- Fetches data from Regrid API
- Performs AI-based research using OpenAI
- Generates JSON, HTML, and PDF reports
- Requires environment variables from `.env`

Current execution:
```bash
source .venv/bin/activate && cd /home/falcao/neighbor/src/neighbor/ && python test_live_regrid.py --lat "43.081409" --lon "-79.029438"
```

## ECS Deployment Options

### Option 1: ECS Fargate Task (Recommended)
Run as on-demand tasks triggered by events or API calls.

**Best for:**
- Sporadic workloads
- Cost efficiency (pay only when running)
- Your current workflow pattern

**Cost Estimate:** ~$0.04-0.08 per run (5-10 minutes)

### Option 2: ECS Service with API
Wrap in a FastAPI service that accepts coordinates via HTTP and runs the pipeline.

**Better for:**
- Regular, predictable usage
- Always-available API endpoint

## Implementation Steps

### 1. Create a Dockerfile

Create `Dockerfile` in the repository root:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Install ngrok (required for OpenAI webhooks - they require HTTPS)
RUN wget https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz && \
    tar -xvzf ngrok-v3-stable-linux-amd64.tgz && \
    mv ngrok /usr/local/bin/ && \
    rm ngrok-v3-stable-linux-amd64.tgz

# Install Playwright dependencies (required for PDF generation)
RUN apt-get update && apt-get install -y \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

# Copy dependencies first (for layer caching)
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .

# Install Playwright browsers
RUN playwright install chromium

# Copy application code
COPY src/ ./src/
COPY merge_new_orgs.py retrieve_response.py ./

WORKDIR /app/src/neighbor

# Set environment for Python
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "test_live_regrid.py"]
CMD ["--lat", "43.081409", "--lon", "-79.029438"]
```

### 2. Handle Output Files

Since the script generates files, you need persistent storage:

#### Option A: S3 (Recommended)
- Modify the script to upload PDFs/JSONs to S3
- Add `boto3` to dependencies in `pyproject.toml`
- Grant ECS task IAM role S3 write permissions

Example code modification:
```python
import boto3

s3_client = boto3.client('s3')
bucket_name = os.getenv('S3_BUCKET_NAME', 'neighbor-reports')

# Upload PDF to S3
s3_client.upload_file(
    str(pdf_path),
    bucket_name,
    f"reports/{timestamp}/neighbor_report.pdf"
)
```

#### Option B: EFS (Elastic File System)
- Mount EFS volume to ECS task
- More expensive but acts like local filesystem
- Configure in task definition

### 3. Environment Variables & Secrets

Store secrets in **AWS Secrets Manager** or **SSM Parameter Store**:

Required environment variables:
```
REGRID_API_KEY=<from-secrets-manager>
OPENAI_API_KEY=<from-secrets-manager>
AZURE_MAPS_API_KEY=<from-secrets-manager>
NGROK_AUTHTOKEN=<from-secrets-manager>
NGROK_DOMAIN=eminent-guided-silkworm.ngrok-free.app
OPENAI_WEBHOOK_URL=https://eminent-guided-silkworm.ngrok-free.app/webhooks/openai
DB_HOST=<database-host>
DB_PORT=5432
DB_USERNAME=<from-secrets-manager>
DB_PASSWORD=<from-secrets-manager>
DB_NAME=<database-name>
S3_BUCKET_NAME=<bucket-for-reports>
```

**Note**: The script will automatically start an ngrok tunnel (just like it does locally) to provide HTTPS access to your webhook server. OpenAI requires HTTPS for webhooks, which is why ngrok is used. The ngrok binary will automatically authenticate using the `NGROK_AUTHTOKEN` environment variable.

Create secrets in AWS Secrets Manager:
```bash
aws secretsmanager create-secret \
    --name neighbor/regrid-api-key \
    --secret-string "YOUR_REGRID_KEY"

aws secretsmanager create-secret \
    --name neighbor/openai-api-key \
    --secret-string "YOUR_OPENAI_KEY"

aws secretsmanager create-secret \
    --name neighbor/azure-maps-api-key \
    --secret-string "YOUR_AZURE_MAPS_KEY"

# Get your ngrok auth token from: https://dashboard.ngrok.com/get-started/your-authtoken
aws secretsmanager create-secret \
    --name neighbor/ngrok-authtoken \
    --secret-string "YOUR_NGROK_AUTHTOKEN"

aws secretsmanager create-secret \
    --name neighbor/db-password \
    --secret-string "YOUR_DB_PASSWORD"
```

### 4. ECS Task Definition

Create `task-definition.json`:

```json
{
  "family": "neighbor-pipeline",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "2048",
  "memory": "4096",
  "executionRoleArn": "arn:aws:iam::ACCOUNT_ID:role/ecsTaskExecutionRole",
  "taskRoleArn": "arn:aws:iam::ACCOUNT_ID:role/neighborTaskRole",
  "containerDefinitions": [{
    "name": "neighbor-app",
    "image": "ACCOUNT_ID.dkr.ecr.us-east-2.amazonaws.com/neighbor:latest",
    "command": ["--lat", "43.081409", "--lon", "-79.029438"],
    "secrets": [
      {
        "name": "REGRID_API_KEY",
        "valueFrom": "arn:aws:secretsmanager:us-east-2:ACCOUNT_ID:secret:neighbor/regrid-api-key"
      },
      {
        "name": "OPENAI_API_KEY",
        "valueFrom": "arn:aws:secretsmanager:us-east-2:ACCOUNT_ID:secret:neighbor/openai-api-key"
      },
      {
        "name": "AZURE_MAPS_API_KEY",
        "valueFrom": "arn:aws:secretsmanager:us-east-2:ACCOUNT_ID:secret:neighbor/azure-maps-api-key"
      },
      {
        "name": "NGROK_AUTHTOKEN",
        "valueFrom": "arn:aws:secretsmanager:us-east-2:ACCOUNT_ID:secret:neighbor/ngrok-authtoken"
      },
      {
        "name": "DB_PASSWORD",
        "valueFrom": "arn:aws:secretsmanager:us-east-2:ACCOUNT_ID:secret:neighbor/db-password"
      }
    ],
    "environment": [
      {"name": "NGROK_DOMAIN", "value": "eminent-guided-silkworm.ngrok-free.app"},
      {"name": "OPENAI_WEBHOOK_URL", "value": "https://eminent-guided-silkworm.ngrok-free.app/webhooks/openai"},
      {"name": "DB_HOST", "value": "your-db-host.rds.amazonaws.com"},
      {"name": "DB_PORT", "value": "5432"},
      {"name": "DB_USERNAME", "value": "your_db_user"},
      {"name": "DB_NAME", "value": "your_db_name"},
      {"name": "S3_BUCKET_NAME", "value": "neighbor-reports"}
    ],
    "logConfiguration": {
      "logDriver": "awslogs",
      "options": {
        "awslogs-group": "/ecs/neighbor-pipeline",
        "awslogs-region": "us-east-2",
        "awslogs-stream-prefix": "ecs",
        "awslogs-create-group": "true"
      }
    }
  }]
}
```

### 5. Create IAM Roles

#### Execution Role (for ECS to pull images and secrets)
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ecr:GetAuthorizationToken",
        "ecr:BatchCheckLayerAvailability",
        "ecr:GetDownloadUrlForLayer",
        "ecr:BatchGetImage",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "secretsmanager:GetSecretValue"
      ],
      "Resource": "*"
    }
  ]
}
```

#### Task Role (for application to access AWS services)
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::neighbor-reports/*",
        "arn:aws:s3:::neighbor-reports"
      ]
    }
  ]
}
```

### 6. Build and Push Docker Image

```bash
# Authenticate to ECR
aws ecr get-login-password --region us-east-2 | docker login --username AWS --password-stdin ACCOUNT_ID.dkr.ecr.us-east-2.amazonaws.com

# Create ECR repository
aws ecr create-repository --repository-name neighbor --region us-east-2

# Build image
docker build -t neighbor .

# Tag image
docker tag neighbor:latest ACCOUNT_ID.dkr.ecr.us-east-2.amazonaws.com/neighbor:latest

# Push to ECR
docker push ACCOUNT_ID.dkr.ecr.us-east-2.amazonaws.com/neighbor:latest
```

### 7. Create ECS Cluster

```bash
aws ecs create-cluster --cluster-name neighbor-cluster --region us-east-2
```

### 8. Register Task Definition

```bash
aws ecs register-task-definition --cli-input-json file://task-definition.json
```

## Running Tasks

### Option 1: Manual Execution

Run individual tasks with custom coordinates:

```bash
aws ecs run-task \
  --cluster neighbor-cluster \
  --task-definition neighbor-pipeline \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-xxx],securityGroups=[sg-xxx],assignPublicIp=ENABLED}" \
  --overrides '{"containerOverrides":[{"name":"neighbor-app","command":["--lat","43.081409","--lon","-79.029438"]}]}'
```

### Option 2: Lambda Trigger

Create a Lambda function to trigger tasks programmatically:

```python
import boto3
import json

def lambda_handler(event, context):
    ecs = boto3.client('ecs')

    lat = event.get('lat', '43.081409')
    lon = event.get('lon', '-79.029438')

    response = ecs.run_task(
        cluster='neighbor-cluster',
        taskDefinition='neighbor-pipeline',
        launchType='FARGATE',
        networkConfiguration={
            'awsvpcConfiguration': {
                'subnets': ['subnet-xxx'],
                'securityGroups': ['sg-xxx'],
                'assignPublicIp': 'ENABLED'
            }
        },
        overrides={
            'containerOverrides': [{
                'name': 'neighbor-app',
                'command': ['--lat', str(lat), '--lon', str(lon)]
            }]
        }
    )

    return {
        'statusCode': 200,
        'body': json.dumps({
            'taskArn': response['tasks'][0]['taskArn'],
            'status': 'STARTED'
        })
    }
```

### Option 3: EventBridge Schedule

Run on a schedule using EventBridge:

```json
{
  "ScheduleExpression": "cron(0 9 * * ? *)",
  "Target": {
    "Arn": "arn:aws:ecs:us-east-2:ACCOUNT_ID:cluster/neighbor-cluster",
    "RoleArn": "arn:aws:iam::ACCOUNT_ID:role/ecsEventsRole",
    "EcsParameters": {
      "TaskDefinitionArn": "arn:aws:ecs:us-east-2:ACCOUNT_ID:task-definition/neighbor-pipeline",
      "LaunchType": "FARGATE",
      "NetworkConfiguration": {
        "awsvpcConfiguration": {
          "Subnets": ["subnet-xxx"],
          "SecurityGroups": ["sg-xxx"],
          "AssignPublicIp": "ENABLED"
        }
      }
    }
  }
}
```

## Key Considerations

### 1. Ngrok Tunneling
The script automatically starts an ngrok tunnel at the beginning of each run (just like it does locally) and stops it at the end. This works identically in ECS as it does on your laptop:

- **Why ngrok?** OpenAI requires HTTPS for webhooks. Your ALB (`webhook-server-alb-1412407138.us-east-2.elb.amazonaws.com`) is HTTP-only.
- **How it works**: The ngrok tunnel provides an HTTPS endpoint that forwards to your ALB
- **Cost**: You're already paying for the ngrok static domain (`eminent-guided-silkworm.ngrok-free.app`)
- **No changes needed**: The existing code works as-is in ECS

**Alternative**: If you want to eliminate ngrok, you'd need to add an HTTPS listener to your ALB with an SSL certificate (via AWS Certificate Manager) and a custom domain.

### 2. Database Access
Ensure ECS tasks can reach your PostgreSQL database:
- Configure VPC subnets (use private subnets if database is in VPC)
- Set up security groups to allow ECS -> RDS traffic on port 5432
- Use RDS endpoint as `DB_HOST`

### 3. Networking
- **Public Subnet + Public IP**: If tasks need internet access for APIs (Regrid, OpenAI)
- **Private Subnet + NAT Gateway**: More secure, but requires NAT for outbound internet
- **Security Groups**: Allow outbound HTTPS (443), PostgreSQL (5432)

### 4. Monitoring & Logging
- CloudWatch Logs: Configured in task definition
- CloudWatch Alarms: Set up for task failures
- X-Ray: Optional, for distributed tracing

View logs:
```bash
aws logs tail /ecs/neighbor-pipeline --follow
```

### 5. Cost Optimization
- Use Fargate Spot for non-critical workloads (70% discount)
- Right-size CPU/memory based on actual usage
- Set CloudWatch log retention period (e.g., 7 days)

### 6. Timeout Configuration
- Default ECS task timeout is unlimited
- Set `stopTimeout` in container definition if needed
- Monitor long-running tasks in CloudWatch

## Testing

### 1. Local Docker Test
```bash
# Build locally
docker build -t neighbor .

# Run with env file
docker run --env-file .env neighbor --lat "43.081409" --lon "-79.029438"
```

### 2. Test in ECS
```bash
# Run one-off task
aws ecs run-task \
  --cluster neighbor-cluster \
  --task-definition neighbor-pipeline \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-xxx],securityGroups=[sg-xxx],assignPublicIp=ENABLED}"

# Check task status
aws ecs describe-tasks --cluster neighbor-cluster --tasks TASK_ARN

# View logs
aws logs tail /ecs/neighbor-pipeline --follow
```

## Next Steps

1. **Create Dockerfile** (provided above)
2. **Modify code to upload outputs to S3** instead of local filesystem (optional - for persistent storage)
3. **Set up AWS infrastructure**:
   - ECR repository
   - Secrets Manager secrets (including ngrok auth token)
   - IAM roles
   - ECS cluster
   - VPC/subnet/security group configuration
4. **Build and push Docker image**
5. **Register task definition**
6. **Test run a task**
7. **Set up Lambda or EventBridge trigger** for automation (optional)

## Troubleshooting

### Task Fails Immediately
- Check CloudWatch logs: `aws logs tail /ecs/neighbor-pipeline --follow`
- Verify secrets are accessible (IAM permissions)
- Check security groups allow outbound internet access

### Cannot Connect to Database
- Verify security group rules
- Check VPC configuration
- Ensure correct DB credentials in Secrets Manager

### Out of Memory
- Increase memory in task definition (currently 4096 MB)
- Monitor actual usage in CloudWatch Container Insights

### Image Pull Errors
- Verify ECR permissions in execution role
- Check image exists: `aws ecr describe-images --repository-name neighbor`
- Ensure correct region in task definition

### Ngrok Tunnel Fails
- Verify `NGROK_AUTHTOKEN` is correctly set in Secrets Manager
- Check `NGROK_DOMAIN` matches your ngrok account domain
- Ensure ECS task has outbound internet access (public IP or NAT gateway)
- Check CloudWatch logs for ngrok error messages
- Verify your ngrok domain is active and not expired
