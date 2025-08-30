import requests
import click
import base64
import gzip
import getpass
import keyring
import io
import sys
import zipfile

from git import Repo
from pathlib import Path


KEYRING_APP_ID = "lazywombat"
DEFAULT_BASE_URL = "https://lazywombat.site"

def generate_cred_payload() -> dict:
    username = keyring.get_password(KEYRING_APP_ID, "username")
    password = keyring.get_password(KEYRING_APP_ID, "password")
    if username is None or password is None:
        raise ValueError("No credentials found. Please login first using the 'login' command.")
    return {
        "username": username,
        "password": password
    }

def get_base_url() -> str:
    base_url = keyring.get_password(KEYRING_APP_ID, "base_url")
    return base_url if base_url else DEFAULT_BASE_URL

def send_request(endpoint: str, payload: dict = dict(), require_cred: bool = True) -> requests.Response:
    url = f"{get_base_url()}/{endpoint}"
    if require_cred:
        payload = generate_cred_payload() | payload
    response = requests.post(url, json=payload)
    return response

def compress_and_encode(data: bytes) -> str:
    compressed_data = gzip.compress(data)
    encoded_data = base64.b64encode(compressed_data).decode('utf-8')
    return encoded_data

@click.group()
def cli():
    pass

@cli.command()
def login() -> None:
    """
    Stores the username and password in the keyring for future use
    Optionally, you can specify the base URL for the API
    """
    
    username = input("Enter your username: ")
    password = getpass.getpass("Enter password: ")
    base_url = input(f"Enter base URL (default: {DEFAULT_BASE_URL}): ") or DEFAULT_BASE_URL
    keyring.set_password(KEYRING_APP_ID, "username", username)
    keyring.set_password(KEYRING_APP_ID, "password", password)
    keyring.set_password(KEYRING_APP_ID, "base_url", base_url)

@cli.command()
@click.argument("file", type=click.Path(exists=True))
def upload(file: Path) -> None:
    """
    Sends compressed bas64 encoded data to the server
    """
    
    if file.is_dir():
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file_obj:
            for path in file.rglob('*'):
                if path.is_file():
                    zip_file_obj.write(path, arcname=path.relative_to(file))
        zip_buffer.seek(0)
        data = zip_buffer.read()
        mb = len(data) / (1024 * 1024)
        should_send = input(f"Confirm upload of {file} ({mb:.2f} MB) (y/n): ").strip().lower()
        if should_send != 'y':
            print("Upload cancelled.")
            return
        base_filename = file.with_suffix('.zip').name
    else:
        if not file.is_file():
            raise ValueError(f"Invalid file: {file}")
        with open(file, 'rb') as f:
            data = f.read()
        base_filename = file.name

    compressed_data = gzip.compress(data)
    encoded_data = base64.b64encode(compressed_data).decode('utf-8')
    payload = {
        "name": base_filename,
        "data": encoded_data
    }
    response = send_request("api/push", payload)
    print(f"Response: {response.status_code} - {response.text}")

@cli.command()
@click.argument("file", type=str)
@click.option("--raw", is_flag=True, default=False, help="print to stdout instead of saving to a file")
def download(file: str, raw: bool) -> None:
    """
    Downloads a file from the server and decompresses it
    """
    
    response = send_request("api/pull", { "name": file })

    if response.status_code == 200:
        compressed_data = response.json().get("data")
        if compressed_data:
            decoded_data = base64.b64decode(compressed_data)
            decompressed_data = gzip.decompress(decoded_data)
            if raw:
                sys.stdout.buffer.write(decompressed_data)
                return
            with open(file, 'wb') as f:
                f.write(decompressed_data)
            print(f"File {file} downloaded and decompressed successfully.")
        else:
            print("No data received.")
    else:
        print(f"Failed to download file: {response.status_code} - {response.text}")

@cli.command()
def list_files() -> None:
    """
    Lists files available for the logged-in user
    """
    
    response = send_request("api/list")

    if response.status_code == 200:
        files = response.json().get("files", [])
        print("Files available:")
        for file in files:
            print(f"- {file}")
    else:
        print(f"Failed to list files: {response.status_code} - {response.text}")

@cli.command()
def backup() -> None:
    """
    Creates a backup of the server data
    """
    
    response = send_request("api/backup")

    if response.status_code == 200:
        print("Backup completed successfully.")
    else:
        print(f"Failed to create backup: {response.status_code} - {response.text}")

@cli.command()
def update() -> None:
    """
    Updates the server with a patch
    """
    
    repo = Repo(".")
    if repo.is_dirty(untracked_files=True):
        print("Repository has uncommitted changes. Please commit or stash them before updating.")
        return

    origin = repo.remotes.origin
    origin.fetch()
    local_commit = repo.head.commit
    remote_commit = origin.refs.main.commit

    ahead_commits = list(repo.iter_commits(f'{remote_commit.hexsha}..{local_commit.hexsha}'))
    if len(ahead_commits) <= 0:
        print("Invalid git state detected, aborting update")
        return

    patch_data = repo.git.format_patch(f"{remote_commit.hexsha}..{local_commit.hexsha}", stdout=True)
    compressed_patch = compress_and_encode(patch_data.encode('utf-8'))

    if input(f"Apply update with {len(ahead_commits)} commits and patch size {len(compressed_patch)/1e3}kB ? (y/n): ").strip().lower() != 'y':
        print("Update cancelled.")
        return

    response = send_request("api/update", {"patch": compressed_patch})

    if response.status_code == 200:
        print("Update applied successfully.")
        print("Please restart the server to apply changes.")
    else:
        print(f"Failed to apply update: {response.status_code} - {response.text}")

@cli.command()
@click.argument("file", type=str)
def delete_file(file: str) -> None:
    """
    Deletes a file from the server
    """
    
    response = send_request("api/delete", {"name": file})

    if response.status_code == 200:
        print(f"File {file} deleted successfully.")
    else:
        print(f"Failed to delete file: {response.status_code} - {response.text}")

@cli.command()
@click.argument("cookies", type=click.Path(exists=True))
def upload_cookies(cookies: Path) -> None:
    """
    Uploads cookies to the server
    """
    
    with open(cookies, 'rb') as f:
        cookie_data = compress_and_encode(f.read())

    response = send_request("api/push_cookie", {"cookie": cookie_data})

    if response.status_code == 200:
        print("Cookies uploaded successfully.")
    else:
        print(f"Failed to upload cookies: {response.status_code} - {response.text}")

if __name__ == "__main__":
    cli()