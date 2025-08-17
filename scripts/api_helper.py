import requests
import click
import base64
import gzip
import getpass
import keyring
import io
import zipfile
import sys

from pathlib import Path


KEYRING_APP_ID = "lazywombat"
DEFAULT_BASE_URL = "https://lazywombat.site"

@click.group()
def cli():
    pass

@cli.command()
def login(username: str = None, password: str = None) -> None:
    """
    Stores the username and password in the keyring for future use
    """
    
    username = input("Enter your username: ")
    if not password:
        password = getpass.getpass("Enter password: ")
    keyring.set_password(KEYRING_APP_ID, "username", username)
    keyring.set_password(KEYRING_APP_ID, "password", password)

@cli.command()
@click.argument("file", type=Path)
@click.option("--base-url", "base_url", type=str, default=DEFAULT_BASE_URL)
def upload(base_url: str, file: Path) -> None:
    """
    Sends compressed bas64 encoded data to the server
    """
    
    username = keyring.get_password(KEYRING_APP_ID, "username")
    password = keyring.get_password(KEYRING_APP_ID, "password")
    if username is None or password is None:
        print("No credentials found. Please login first using the 'login' command.")
        return

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
        "username": username,
        "password": password,
        "name": base_filename,
        "data": encoded_data
    }

    url = f"{base_url}/api/push"
    response = requests.post(url, json=payload)
    print(f"Response: {response.status_code} - {response.text}")

@cli.command()
@click.argument("file", type=str)
@click.option("--base-url", "base_url", type=str, default=DEFAULT_BASE_URL)
@click.option("--raw", is_flag=True, default=False, help="print to stdout instead of saving to a file")
def download(base_url: str, file: str, raw: bool) -> None:
    """
    Downloads a file from the server and decompresses it
    """
    
    username = keyring.get_password(KEYRING_APP_ID, "username")
    password = keyring.get_password(KEYRING_APP_ID, "password")
    if username is None or password is None:
        print("No credentials found. Please login first using the 'login' command.")
        return

    payload = {
        "username": username,
        "password": password,
        "name": file
    }

    url = f"{base_url}/api/pull"
    response = requests.post(url, json=payload)

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
@click.option("--base-url", "base_url", type=str, default=DEFAULT_BASE_URL)
def list_files(base_url: str) -> None:
    """
    Lists files available for the logged-in user
    """
    
    username = keyring.get_password(KEYRING_APP_ID, "username")
    password = keyring.get_password(KEYRING_APP_ID, "password")
    if username is None or password is None:
        print("No credentials found. Please login first using the 'login' command.")
        return

    payload = {
        "username": username,
        "password": password
    }

    url = f"{base_url}/api/list"
    response = requests.post(url, json=payload)

    if response.status_code == 200:
        files = response.json().get("files", [])
        print("Files available:")
        for file in files:
            print(f"- {file}")
    else:
        print(f"Failed to list files: {response.status_code} - {response.text}")

@cli.command()
@click.argument("file", type=str)
@click.option("--base-url", "base_url", type=str, default=DEFAULT_BASE_URL)
def delete_file(base_url: str, file: str) -> None:
    """
    Deletes a file from the server
    """
    
    username = keyring.get_password(KEYRING_APP_ID, "username")
    password = keyring.get_password(KEYRING_APP_ID, "password")
    if username is None or password is None:
        print("No credentials found. Please login first using the 'login' command.")
        return

    payload = {
        "username": username,
        "password": password,
        "name": file
    }

    url = f"{base_url}/api/delete"
    response = requests.post(url, json=payload)

    if response.status_code == 200:
        print(f"File {file} deleted successfully.")
    else:
        print(f"Failed to delete file: {response.status_code} - {response.text}")


if __name__ == "__main__":
    cli()