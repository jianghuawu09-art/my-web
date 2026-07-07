import requests

url = 'http://127.0.0.1:7777/api/excel/upload'

data = {'test': 'value'}
r = requests.post(url, data=data)
print(f"POST with form data: {r.status_code}")
print(r.text[:300])
print()
