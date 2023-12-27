# this is for AWS Lambda

import os
import json
import boto3

from botocore.exceptions import ClientError
from server import Backend
from goal import Goal


BUCKET_NAME = 'todoist2'

def load_from_s3():
    s3 = boto3.client('s3')
    save_file_name = os.path.basename(Backend.SAVE_FILE)
    try:
        s3.download_file(BUCKET_NAME, save_file_name, Backend.SAVE_FILE)
    except ClientError as e:
        print(f"could not download save file from s3: {e}")

def save_to_s3():
    s3 = boto3.client('s3')
    save_file_name = os.path.basename(Backend.SAVE_FILE)
    try:
        s3.upload_file(Backend.SAVE_FILE, BUCKET_NAME, save_file_name)
        # save backups
        try:
            for backup in os.listdir(Backend.BACKUP_DIR):
                save_file_name = f".todoist2_backup/{backup}"
                s3.upload_file(f"{Backend.BACKUP_DIR}/{backup}", BUCKET_NAME, save_file_name)
        except FileNotFoundError as e:
            print(f"Warning could not find backups: {e}")
    except ClientError as e:
        print(f"could not save file to s3: {e}")
    
def lambda_handler(event, context):
    load_from_s3()
    backend = Backend()
    
    request = json.loads(event['body'])
    print(request)
    req_type = request['request']
    body = ''
    if req_type == 'fetch_goals':
        body = backend.get_goals_json()
    elif req_type == 'toggle_goal_state':
        goal_id = request['goal_id']
        backend.toggle_goal_state(goal_id)
        save_to_s3()
    elif req_type == 'add_goal':
        backend.add_goal(Goal.from_dict(request['goal']))
        save_to_s3()
    elif req_type == 'delete_goal':
        backend.delete_goal(request['goal_id'])
        save_to_s3()
    elif req_type == 'backup_goals':
        backend.backup_goals()
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