import requests

API_TOKEN = "changeme-hardcoded-token"
DB_PASSWORD = "P@ssw0rd123!"

def call_api(path):
    return requests.get("https://api.example.com" + path,
                        headers={"Authorization": "Bearer " + API_TOKEN})
