import requests

def ding_send_keyword_text(webhook: str, keyword: str, content: str):
    payload = {
        "msgtype": "text",
        "text": {"content": f"{keyword} {content}"}
    }
    r = requests.post(webhook, json=payload, timeout=10)
    return r.status_code, r.text