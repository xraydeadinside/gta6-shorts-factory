"""One-time helper for the (disabled) YouTube auto-upload: prints a refresh token.

1. console.cloud.google.com -> new project -> enable "YouTube Data API v3".
2. OAuth consent screen -> External -> add yourself as a test user -> Publish app.
3. Credentials -> OAuth client ID -> Desktop app -> download as client_secret.json next to this script.
4. pip install google-auth-oauthlib && python scripts/get_youtube_token.py
Then put YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN into GitHub Secrets.
"""
from google_auth_oauthlib.flow import InstalledAppFlow

flow = InstalledAppFlow.from_client_secrets_file(
    "client_secret.json", scopes=["https://www.googleapis.com/auth/youtube.upload"])
creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
print("YT_REFRESH_TOKEN =", creds.refresh_token)
