from requests_oauthlib import OAuth2Session
import json

# === FILL THESE OF YOUR OWN ===
client_id = #fill here in double inverted commmas
client_secret = #fill here in double inverted commmas

# Use PRODUCTION OSM (we'll switch to dev later if needed)
authorization_base_url = "https://www.openstreetmap.org/oauth2/authorize"
token_url = "https://www.openstreetmap.org/oauth2/token"

redirect_uri = "https://localhost"

# Create OAuth session
oauth = OAuth2Session(
    client_id,
    redirect_uri=redirect_uri,
    scope=["write_api"]
)

# Step 1: Get authorization URL
authorization_url, state = oauth.authorization_url(authorization_base_url)

print("\nGo to this URL and authorize access:\n")
print(authorization_url)

# Step 2: After login, paste redirect URL here
authorization_response = input("\nPaste the FULL redirect URL here:\n")

# Step 3: Fetch access token
token = oauth.fetch_token(
    token_url,
    authorization_response=authorization_response,
    client_secret=client_secret,
)
with open("token.json", "w") as f:
    json.dump(token, f)

print("\nAccess token obtained successfully!")
print(token)
