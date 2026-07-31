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
import json
import os

from git import Repo
from pathlib import Path
from yt_dlp.cookies import extract_cookies_from_browser
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


KEYRING_APP_ID = "nabicat"
DEFAULT_BASE_URL = "https://nabicat.site"


def hybrid_encrypt(data: bytes, public_key_pem: str, session_id: str) -> dict:
    """
    Encrypt data using hybrid encryption (RSA + AES-GCM).
    
    1. Generate random 256-bit AES key
    2. Compress and encrypt data with AES-GCM
    3. Encrypt AES key with RSA public key
    4. Return payload dict for server
    """
    # Generate random 256-bit AES key
    aes_key = AESGCM.generate_key(bit_length=256)
    aesgcm = AESGCM(aes_key)
    
    # Compress data
    compressed_data = gzip.compress(data)
    
    # Encrypt with AES-GCM (nonce is auto-generated)
    nonce = os.urandom(12)  # 96-bit nonce for GCM
    encrypted_data = aesgcm.encrypt(nonce, compressed_data, None)
    
    # Load RSA public key
    public_key = serialization.load_pem_public_key(public_key_pem.encode('utf-8'))
    
    # Encrypt AES key with RSA-OAEP
    encrypted_key = public_key.encrypt(
        aes_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None
        )
    )
    
    return {
        "session_id": session_id,
        "encrypted_key": base64.b64encode(encrypted_key).decode('utf-8'),
        "encrypted_data": base64.b64encode(encrypted_data).decode('utf-8'),
        "nonce": base64.b64encode(nonce).decode('utf-8')
    }


def do_handshake() -> tuple[str, str]:
    """
    Perform handshake with server to get ephemeral RSA public key.
    
    Returns:
        tuple: (session_id, public_key_pem)
    """
    url = f"{get_base_url()}/api/handshake"
    
    response = requests.post(url, timeout=10)
    
    if response.status_code != 200:
        raise ConnectionError(f"Handshake failed: {response.status_code} - {response.text}")
    
    data = response.json()
    if not data.get("success"):
        raise ConnectionError(f"Handshake failed: {data.get('error', 'Unknown error')}")
    
    return data["session_id"], data["public_key"]

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

def _send_request_internal(endpoint: str, payload: dict = dict(), require_cred: bool = True) -> requests.Response:
    url = f"{get_base_url()}/{endpoint}"
    if require_cred:
        payload = generate_cred_payload() | payload

    # Step 1: Handshake to get ephemeral public key
    session_id, public_key = do_handshake()
    
    # Step 2: Encrypt payload with hybrid encryption
    unencrypted_payload = json.dumps(payload).encode('utf-8')
    encrypted_payload = hybrid_encrypt(unencrypted_payload, public_key, session_id)
    
    # Calculate size for confirmation
    payload_json = json.dumps(encrypted_payload)
    if len(payload_json) > 1e3:
        size_kb = len(payload_json) / 1e3
        if input(f"Payload size is {size_kb:.2f} kB. Do you want to continue? (y/n): ").strip().lower() != 'y':
            print("Request cancelled.")
            sys.exit(0)

    return requests.post(url, json={"req": encrypted_payload})


def send_request(endpoint: str, payload: dict = dict(), require_cred: bool = True) -> requests.Response:
    try:
        return _send_request_internal(endpoint, payload, require_cred)
    except ConnectionError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

@click.group(help="cli helper for interacting with backend all data (including credentials) are encrypted")
def cli():
    pass

@cli.command()
def login() -> None:
    """
    Stores the username and password in the keyring for future use.
    Optionally, you can specify the base URL for the API.
    """
    
    username = input("Enter your username: ")
    password = getpass.getpass("Enter password: ")
    base_url = input(f"Enter base URL (default: {DEFAULT_BASE_URL}): ") or DEFAULT_BASE_URL
    keyring.set_password(KEYRING_APP_ID, "username", username)
    keyring.set_password(KEYRING_APP_ID, "password", password)
    keyring.set_password(KEYRING_APP_ID, "base_url", base_url)
    print("Login credentials saved successfully.")

@cli.command()
@click.argument("file", type=click.Path(exists=True))
def upload(file: str) -> None:
    file = Path(file)
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
    response = send_request("api/backup")
    if response.status_code == 200:
        print("Backup completed successfully.")
    else:
        print(f"Failed to create backup: {response.status_code} - {response.text}")

@cli.command()
def update() -> None:
    """
    Updates the server with a patch

    Make sure you are on main branch and strictly ahead of origin/main
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

    raw_patch: str = repo.git.format_patch(f"{remote_commit.hexsha}..{local_commit.hexsha}", stdout=True)
    
    if input(f"Apply update with {len(ahead_commits)} commits ahead? (y/n): ").strip().lower() != 'y':
        print("Update cancelled.")
        return
    
    response = send_request("api/update", {"patch": raw_patch})

    if response.status_code == 200:
        print("Update applied successfully.")
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

    if input(f"Upload {len(ahead_commits)} patches? (y/n): ").strip().lower() != 'y':
        print("Upload cancelled.")
        return
    
    response = send_request("api/push", {
        "name": "_commit_patches.zip",
        "data": zip_data.decode('utf-8')
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

@cli.command()
@click.argument("file", type=str)
def delete_file(file: str) -> None:
    response = send_request("api/delete", {"name": file})

    if response.status_code == 200:
        print(f"File {file} deleted successfully.")
    else:
        print(f"Failed to delete file: {response.status_code} - {response.text}")

@cli.command()
@click.option(
    "--browser",
    default="chrome",
    show_default=True,
    help="Browser name (chrome, firefox, edge, etc.)",
)
@click.option(
    "--profile",
    default="Profile 1",
    show_default=True,
    help="Browser profile name",
)
def sync_cookies(browser: str, profile: str | None) -> None:
    """
    Export YouTube cookies from the specified browser and upload to the server.
    """

    YOUTUBE_DOMAINS = {
        ".youtube.com",
        "youtube.com",
        ".google.com",
        "google.com",
        ".googlevideo.com",
    }

    def cookie_to_netscape(cookie) -> str:
        secure = "TRUE" if cookie.secure else "FALSE"
        domain_initial_dot = "TRUE" if cookie.domain.startswith(".") else "FALSE"
        expires = str(int(cookie.expires)) if cookie.expires else "0"
        return f"{cookie.domain}\t{domain_initial_dot}\t{cookie.path}\t{secure}\t{expires}\t{cookie.name}\t{cookie.value}"

    def export_yt_cookies_from_browser(browser: str, profile: str | None) -> tuple[str, int]:
        jar = extract_cookies_from_browser(browser, profile=profile)
        lines = [
            "# Netscape HTTP Cookie File",
            "# https://curl.haxx.se/docs/http-cookies.html",
            "# This file was generated by api_helper.py",
            "",
        ]
        filtered_count = 0

        for cookie in jar:
            domain = cookie.domain.lower()
            if any(domain.endswith(yt) or domain == yt.lstrip(".") for yt in YOUTUBE_DOMAINS):
                lines.append(cookie_to_netscape(cookie))
                filtered_count += 1

        return "\n".join(lines) + "\n", filtered_count

    
    cookie_text, filtered_count = export_yt_cookies_from_browser(browser, profile)

    if filtered_count <= 0:
        print("No YouTube cookies found to export.", file=sys.stderr)
        return

    response = send_request("api/push_cookie", {"cookie": cookie_text})

    if response.status_code == 200:
        print(f"Exported {filtered_count} YouTube cookies and uploaded to server.")
    else:
        print(
            f"Exported {filtered_count} YouTube cookies but upload failed: "
            f"{response.status_code} - {response.text}"
        )

@cli.command()
@click.argument("post_folder", type=click.Path(exists=True))
@click.option("--project", required=True, help="Project name for the post")
@click.option("--name", "post_name", default=None, help="Post name (defaults to folder name)")
@click.option("--date", default=None, help="Override post date (ISO format, e.g. 2024-03-15T10:00:00)")
def upload_post(post_folder: str, project: str, post_name: str | None, date: str | None) -> None:
    post_path = Path(post_folder)
    if not post_path.is_dir():
        print(f"Error: {post_folder} is not a directory", file=sys.stderr)
        sys.exit(1)

    if post_name is None:
        post_name = post_path.name

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for path in post_path.rglob('*'):
            if path.is_file():
                zf.write(path, arcname=path.relative_to(post_path))
    zip_buffer.seek(0)
    data = zip_buffer.read()

    compressed_data = gzip.compress(data)
    encoded_data = base64.b64encode(compressed_data).decode('utf-8')

    mb = len(data) / (1024 * 1024)
    print(f"Uploading post '{post_name}' to project '{project}' ({mb:.2f} MB)")

    payload = {"project": project, "post_name": post_name, "data": encoded_data}
    if date:
        payload["date"] = date
    response = send_request("loft/api/upload_post", payload)
    print(f"Response: {response.status_code} - {response.text}")

@cli.command()
def test_handshake() -> None:
    """
    Test the encryption handshake with the server.
    """
    try:
        session_id, public_key = do_handshake()
        print(f"Handshake successful!")
        print(f"Session ID: {session_id}")
        print(f"Public Key (first 100 chars): {public_key[:100]}...")
    except ConnectionError as e:
        print(f"Handshake failed:\n{e}")


@cli.command(name="sync-data-from-prod")
@click.option("--dest", default=str(Path.home()), show_default=True,
              help="Local destination directory (receives .nabicat/)")
@click.option("--dry-run", is_flag=True, default=False,
              help="Show what would be transferred without copying")
def sync_data_from_prod(dest: str, dry_run: bool) -> None:
    """
    Rsync ~/.nabicat from nabicat.site to a local directory, excluding backups/.
    """
    cmd = ["rsync", "-azhP", "--exclude=backups/"]
    if dry_run:
        cmd.append("--dry-run")
    cmd += ["nabicat.site:~/.nabicat", dest]
    print("$", " ".join(cmd))
    result = subprocess.run(cmd)
    sys.exit(result.returncode)


@cli.command(name="config")
def show_config() -> None:
    """
    Show current configuration (base URL, username).
    """
    base_url = keyring.get_password(KEYRING_APP_ID, "base_url")
    username = keyring.get_password(KEYRING_APP_ID, "username")
    
    print(f"Current configuration:")
    print(f"  Base URL: {base_url or '(not set)'}")
    print(f"  Username: {username or '(not set)'}")
    
    # Test connection
    print(f"\nTesting connection to {base_url or DEFAULT_BASE_URL}...")
    try:
        session_id, public_key = do_handshake()
        print(f"  ✓ Server is reachable")
        print(f"  ✓ Handshake successful (session: {session_id[:8]}...)")
    except ConnectionError as e:
        print(f"  ✗ {e}")

if __name__ == "__main__":
    cli()