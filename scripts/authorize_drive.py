"""One-time local authorization. Never print credentials."""
import argparse
import os
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("client_json", type=Path)
    parser.add_argument("--folder-id", default="1lEdKhHDGHkAcNMclIJPzQvjKbxAxsL-M")
    parser.add_argument("--output", type=Path, default=Path(".secrets/google-oauth.env"))
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; choose a new path")
    # Existing folder access needs drive scope unless selected through Google Picker.
    flow = InstalledAppFlow.from_client_secrets_file(str(args.client_json),
        scopes=["https://www.googleapis.com/auth/drive"], autogenerate_code_verifier=True)
    credentials = flow.run_local_server(host="127.0.0.1", port=0, access_type="offline",
        prompt="consent", timeout_seconds=300)
    if not credentials.refresh_token:
        raise RuntimeError("No refresh token received")
    service = build("drive", "v3", credentials=credentials, cache_discovery=False)
    user = service.about().get(fields="user(emailAddress)").execute()["user"]
    if user["emailAddress"].lower() != "nokhook5454@gmail.com":
        raise RuntimeError("Wrong Google account; expected nokhook5454@gmail.com")
    folder = service.files().get(fileId=args.folder_id,
        fields="id,mimeType,capabilities(canAddChildren)").execute()
    if folder["mimeType"] != "application/vnd.google-apps.folder" or not folder["capabilities"].get("canAddChildren"):
        raise RuntimeError("Target folder is not writable")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    values = {"GOOGLE_CLIENT_ID": credentials.client_id, "GOOGLE_CLIENT_SECRET": credentials.client_secret,
              "GOOGLE_REFRESH_TOKEN": credentials.refresh_token, "GOOGLE_DRIVE_FOLDER_ID": args.folder_id}
    with args.output.open("x", encoding="utf-8") as output:
        for key, value in values.items():
            output.write(key + "=" + value + "\n")
    if os.name != "nt":
        args.output.chmod(0o600)
    print("Account and folder verified. Credentials saved locally to", args.output)
    print("Enter these variables in Railway. Do not paste them in chat or Git.")


if __name__ == "__main__":
    main()
