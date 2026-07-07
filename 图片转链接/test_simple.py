import requests

r = requests.get('http://127.0.0.1:7777/api/records')
print(f"GET /api/records: {r.status_code}")
print(r.text[:200])
