import subprocess
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
def upload_commit_patches() -> None:
    """
    Generates and uploads commit patches for all commits between the tracking branch and current branch (assuming current is ahead)
    """
    
    repo = Repo(".")
    if repo.is_dirty(untracked_files=True):
        print("Repository has uncommitted changes. Please commit or stash them before generating patches.")
        return

    origin = repo.remotes.origin
    origin.fetch()
    
    current_branch = repo.active_branch
    tracking_branch = current_branch.tracking_branch()
    
    if tracking_branch is None:
        print(f"Current branch '{current_branch.name}' has no tracking branch set.")
        return

    local_commit = repo.head.commit
    remote_commit = tracking_branch.commit

    ahead_commits = list(repo.iter_commits(f'{remote_commit.hexsha}..{local_commit.hexsha}'))
    if len(ahead_commits) <= 0:
        print("No commits ahead of tracking branch. Nothing to generate.")
        return

    # Generate patches in memory
    print(f"Generating {len(ahead_commits)} patches in memory...")
    
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file_obj:
        # Iterate through commits in reverse chronological order
        for i, commit in enumerate(reversed(ahead_commits)):
            patch_data = repo.git.format_patch('-1', commit.hexsha, stdout=True)
            # Name patches with leading zeros (0001-msg, 0002-msg, etc.)
            patch_filename = f"{i+1:04d}-{commit.hexsha[:7]}.patch"
            zip_file_obj.writestr(patch_filename, patch_data)
    
    zip_buffer.seek(0)
    zip_data = zip_buffer.read()
    total_size = len(zip_data) / 1024
    
    print(f"Generated {len(ahead_commits)} patches ({total_size:.2f} KB)")
    
    # Confirm upload
    if input(f"Upload {len(ahead_commits)} patches? (y/n): ").strip().lower() != 'y':
        print("Upload cancelled.")
        return
    
    # Compress and encode
    compressed_patches = compress_and_encode(zip_data)
    
    # Send via API
    response = send_request("api/push", {
        "name": "_commit_patches.zip",
        "data": compressed_patches
    })
    
    if response.status_code == 200:
        print("Patches uploaded successfully.")
    else:
        print(f"Failed to upload patches: {response.status_code} - {response.text}")

@cli.command()
def apply_remote_patches() -> None:
    """
    Downloads patch file from the server and applies it to the current repo
    """
    
    repo = Repo(".")
    if repo.is_dirty(untracked_files=True):
        print("Repository has uncommitted changes. Please commit or stash them before applying patches.")
        return

    # Download the patch file from server
    response = send_request("api/pull", { "name": "_commit_patches.zip" })

    if response.status_code != 200:
        print(f"Failed to download patches: {response.status_code} - {response.text}")
        return

    compressed_data = response.json().get("data")
    if not compressed_data:
        print("No patch data received.")
        return

    # Decompress and decode
    decoded_data = base64.b64decode(compressed_data)
    decompressed_data = gzip.decompress(decoded_data)

    # Extract patches from zip
    zip_buffer = io.BytesIO(decompressed_data)
    patch_files = []
    try:
        with zipfile.ZipFile(zip_buffer, 'r') as zip_file_obj:
            patch_files = sorted(zip_file_obj.namelist())
            print(f"Found {len(patch_files)} patches to apply.")
            
            if len(patch_files) == 0:
                print("No patches found in archive.")
                return
            
            # Confirm before applying
            if input(f"Apply {len(patch_files)} patches? (y/n): ").strip().lower() != 'y':
                print("Apply cancelled.")
                return
            
            # Apply each patch
            for patch_filename in patch_files:
                patch_data = zip_file_obj.read(patch_filename).decode('utf-8')
                try:
                    run_result = subprocess.run(['git', 'am'], 
                                   input=patch_data, 
                                   check=True, 
                                   capture_output=True,
                                   text=True)
                    print(run_result.stderr, run_result.stdout)
                    print(f"✓ Applied {patch_filename}")
                except Exception as e:
                    print(f"✗ Failed to apply {patch_filename}: {e}")
                    # Optionally abort the am process on first failure
                    try:
                        repo.git.am(abort=True)
                    except:
                        pass
                    return
            
            print(f"Successfully applied all {len(patch_files)} patches.")
    except zipfile.BadZipFile:
        print("Failed to extract patch archive: Invalid zip file.")
        return
    except Exception as e:
        print(f"Error processing patches: {e}")
        return

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