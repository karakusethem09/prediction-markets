import json
import requests

URL = "https://gamma-api.polymarket.com/markets"

response = requests.get(URL, params={"closed":"true","limit":5},timeout =30)
response.raise_for_status()
markets = response.json()

print(f"Got {len(markets)} markets\n")

for m in markets:
    outcomes = json.loads(m["outcomes"])
    prices =json.loads(m["outcomePrices"])
    print("Question:", m["question"])
    print("Closes:  ", m.get("endDate"))
    print("Volume:  ", m.get("volumeNum"))
    print("Prices:  ",dict(zip(outcomes,prices)))
    
    print("-" * 50)