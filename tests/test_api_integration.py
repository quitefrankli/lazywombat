
import requests


BASE_URL = "http://127.0.0.1:12345/api"
USERNAME = "admin"
PASSWORD = "admin"
FILENAME = "testfile.json"
DATA = '{"key": "value"}'

def get_auth_payload():
    return {
        "username": USERNAME,
        "password": PASSWORD
    }

def test_push_data():
    url = f"{BASE_URL}/push"
    payload = {
        **get_auth_payload(),
        "name": FILENAME,
        "data": DATA
    }
    response = requests.post(url, json=payload)
    assert response.status_code == 200
    assert response.json().get("success")

def test_pull_data():
    url = f"{BASE_URL}/pull"
    payload = {
        **get_auth_payload(),
        "name": FILENAME
    }
    response = requests.post(url, json=payload)
    assert response.status_code == 200
    assert response.json().get("success")
    assert response.json().get("data") == DATA

def test_list_files():
    url = f"{BASE_URL}/list"
    payload = get_auth_payload()
    response = requests.post(url, json=payload)
    assert response.status_code == 200
    files = response.json().get("files", [])
    assert FILENAME in files

def test_delete_data():
    url = f"{BASE_URL}/delete"
    payload = {
        **get_auth_payload(),
        "name": FILENAME
    }
    response = requests.post(url, json=payload)
    assert response.status_code == 200
    assert response.json().get("success")

    # Confirm deletion
    url = f"{BASE_URL}/list"
    payload = get_auth_payload()
    response = requests.post(url, json=payload)
    assert response.status_code == 200
    files = response.json().get("files", [])
    assert FILENAME not in files

def test_list_files_after_deletion():
    url = f"{BASE_URL}/list"
    payload = get_auth_payload()
    response = requests.post(url, json=payload)
    assert response.status_code == 200

def test_pull_data_after_deletion():
    url = f"{BASE_URL}/pull"
    payload = {
        **get_auth_payload(),
        "name": FILENAME
    }
    response = requests.post(url, json=payload)
    assert response.status_code == 404  # Expecting not found since file was deleted