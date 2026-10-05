import os
import boto3

lambda_client = boto3.client('lambda')

PROCESSOR_FUNCTION_NAME = os.environ["PROCESSOR_FUNCTION_NAME"]

# Handles incoming messages to tele bot and hands them to the processor asynchronously
def lambda_handler(event, context):
    try:
        # API Gateway passes the Telegram JSON as a string inside 'body'
        body = event.get('body')
        if not body:
            return {"statusCode": 200, "body": "Empty request"}
            
        # Async invoke returns as soon as Lambda queues the event, so the processor's
        # work doesn't hold up the webhook response.
        lambda_client.invoke(
            FunctionName=PROCESSOR_FUNCTION_NAME,
            InvocationType='Event',
            Payload=body
        )
        
        # Acknowledge receipt to Telegram immediately in milliseconds
        return {"statusCode": 200, "body": "OK"}
        
    except Exception as e:
        print(f"Receiver Error: {e}")
        # Always return 200 to Telegram so it doesn't infinitely retry
        return {"statusCode": 200, "body": "Error acknowledged"}