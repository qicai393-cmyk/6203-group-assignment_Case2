"""Check API connectivity and list models without requesting completions."""
from pathlib import Path
import json
import sys
from dotenv import load_dotenv
from openai import OpenAI
import os

root = Path(__file__).resolve().parents[1]
load_dotenv(root / '.env')
key = os.getenv('OPENAI_COMPATIBLE_API_KEY')
base = os.getenv('OPENAI_COMPATIBLE_BASE_URL')
if not key or not base:
    print('Missing API key or base URL in local .env. No request sent.')
    sys.exit(1)
try:
    client = OpenAI(api_key=key, base_url=base, timeout=30, max_retries=0)
    models = sorted(item.id for item in client.models.list().data)
except Exception as error:
    print('Connection check failed:', type(error).__name__)
    status = getattr(error, 'status_code', None)
    if status:
        print('HTTP status:', status)
    print('Credentials and server error details are hidden.')
    sys.exit(1)
output = root / 'runs' / 'available_models.json'
output.parent.mkdir(exist_ok=True)
output.write_text(json.dumps(models, indent=2) + '\n')
print('Model-list connection succeeded. No completion was requested.')
print('This does not verify account credits or permission to use each model.')
print('OpenAI model IDs returned by the service:')
for name in models:
    if name.startswith('openai/'):
        print(' ', name)
print('Full model list saved to runs/available_models.json')
