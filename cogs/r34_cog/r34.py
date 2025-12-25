import requests
import os
from urllib.parse import urlencode
from dotenv import load_dotenv

load_dotenv()

RULE34_API_URL = os.getenv("RULE34_API_URL", "https://api.rule34.xxx/index.php")


class Rule34API:
    def __init__(self):
        self.api_url = RULE34_API_URL
        self.api_key = os.getenv("RULE34_API_KEY")

    def fetch_post(self, tags: str, page: int = 0, limit: int = 1):
        formatted_tags = tags.strip().replace(" ", "+") + "+-ai_generated"

        params = {
            "page": "dapi",
            "s": "post",
            "q": "index",
            "pid": page,
            "limit": limit,
            "json": 1,
            "tags": formatted_tags,
            "api_key": "8d1c32b413151fb363c5795030976c095f966dc183048ac2c57067f80e70d32431e5ccb0adcb38947fbc6fba96a3d9dabd1dabc7084843bd94859b006305f816",
            "user_id": "2703430",
        }

        url = f"{self.api_url}?{urlencode(params).replace('%2B', '+')}"

        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            raise Exception(f"API lỗi: {response.status_code}")

        data = response.json()
        print(data)
      
rule34_api = Rule34API()
rule34_api.fetch_post("trap", page=0, limit=1)

