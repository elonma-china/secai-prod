import requests

API_TOKEN = "sk_live_9f8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c"
DB_PASSWORD = "P@ssw0rd123!"

def call_api(path):
    return requests.get("https://api.example.com" + path,
                        headers={"Authorization": "Bearer " + API_TOKEN})
