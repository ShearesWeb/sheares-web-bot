import os
import boto3

sts_client = boto3.client('sts')
identity = sts_client.get_caller_identity()
account_id = identity['Account']
sqs = boto3.client('sqs')

SQS_URL = f"https://sqs.ap-southeast-1.amazonaws.com/{account_id}/preprod-sqs-shearesbot"

# Handles incoming messages to tele bot and places then in a SQS queue to be processed
def lambda_handler(event, context):
    try:
        # API Gateway passes the Telegram JSON as a string inside 'body'
        body = event.get('body')
        if not body:
            return {"statusCode": 200, "body": "Empty request"}
            
        # Throw the raw string directly into SQS
        sqs.send_message(
            QueueUrl=SQS_URL,
            MessageBody=body
        )
        
        # Acknowledge receipt to Telegram immediately in milliseconds
        return {"statusCode": 200, "body": "OK"}
        
    except Exception as e:
        print(f"Receiver Error: {e}")
        # Always return 200 to Telegram so it doesn't infinitely retry
        return {"statusCode": 200, "body": "Error acknowledged"}