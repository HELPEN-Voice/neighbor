#!/bin/bash
set -e

# Usage: ./run-ecs-task.sh <lat> <lon> [subnet-id] [security-group-id]
# Example: ./run-ecs-task.sh 43.081409 -79.029438

LAT=${1:-43.081409}
LON=${2:--79.029438}
SUBNET=${3:-subnet-84f5faec}
SECURITY_GROUP=${4:-sg-6c42b90c}

# Load environment variables from .env
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
else
    echo "Error: .env file not found"
    exit 1
fi

# Check required env vars
if [ -z "$NGROK_AUTHTOKEN" ]; then
    echo "Error: NGROK_AUTHTOKEN not set in .env"
    exit 1
fi

echo "Running neighbor pipeline for coordinates: $LAT, $LON"
echo "Using subnet: $SUBNET"
echo "Using security group: $SECURITY_GROUP"

aws ecs run-task \
  --cluster neighbor-cluster \
  --task-definition neighbor-pipeline \
  --launch-type FARGATE \
  --region us-east-2 \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNET],securityGroups=[$SECURITY_GROUP],assignPublicIp=ENABLED}" \
  --overrides "{
    \"containerOverrides\": [{
      \"name\": \"neighbor-app\",
      \"command\": [\"--lat\", \"$LAT\", \"--lon\", \"$LON\"],
      \"environment\": [
        {\"name\": \"REGRID_API_KEY\", \"value\": \"$REGRID_API_KEY\"},
        {\"name\": \"OPENAI_API_KEY\", \"value\": \"$OPENAI_API_KEY\"},
        {\"name\": \"AZURE_MAPS_API_KEY\", \"value\": \"$AZURE_MAPS_API_KEY\"},
        {\"name\": \"NGROK_AUTHTOKEN\", \"value\": \"$NGROK_AUTHTOKEN\"},
        {\"name\": \"NGROK_DOMAIN\", \"value\": \"$NGROK_DOMAIN\"},
        {\"name\": \"OPENAI_WEBHOOK_URL\", \"value\": \"$OPENAI_WEBHOOK_URL\"},
        {\"name\": \"WEBHOOK_WS_URL\", \"value\": \"$WEBHOOK_WS_URL_ECS\"},
        {\"name\": \"DB_HOST\", \"value\": \"$DB_HOST\"},
        {\"name\": \"DB_PORT\", \"value\": \"$DB_PORT\"},
        {\"name\": \"DB_USER\", \"value\": \"$DB_USER\"},
        {\"name\": \"DB_PASSWORD\", \"value\": \"$DB_PASSWORD\"},
        {\"name\": \"DB_NAME\", \"value\": \"$DB_NAME\"},
        {\"name\": \"DELETE_AFTER_UPLOAD\", \"value\": \"true\"}
      ]
    }]
  }"

echo ""
echo "Task started! Check logs with:"
echo "aws logs tail /ecs/neighbor-pipeline --follow --region us-east-2"
