# this is for AWS Lambda

import os
import json
import boto3
from botocore.exceptions import ClientError
from server import Backend


BUCKET_NAME = 'todoist2'

def load_from_s3():
    s3 = boto3.client('s3')
    save_file_name = os.path.basename(Backend.SAVE_FILE)
    try:
        s3.download_file(BUCKET_NAME, save_file_name, Backend.SAVE_FILE)
    except ClientError as e:
        print(f"could not download save file from s3: {e.what()}")

def save_to_s3():
    s3 = boto3.client('s3')
    save_file_name = os.path.basename(Backend.SAVE_FILE)
    try:
        s3.upload_file(Backend.SAVE_FILE, BUCKET_NAME, save_file_name)
    except ClientError as e:
        print(f"could not save file to s3: {e.what()}")    
    
def lambda_handler(event, context):
    load_from_s3()
    backend = Backend()
    
    request = json.loads(event['body'])
    req_type = request['request']
    body = ''
    if req_type == 'fetch_goals':
        body = backend.get_goals_json()
    elif req_type == 'toggle_goal_state':
        goal_id = request['goal_id']
        backend.toggle_goal_state(goal_id)
        save_to_s3()
    else:
        return {
            'statusCode': 400,
            'body': f"Invalid Request: {req_type}"
        }


    return {
        'statusCode': 200,
        'body': body
    }