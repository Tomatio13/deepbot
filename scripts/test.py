import base64
import requests

def encode_image(image_path):
    """ローカル画像をBase64にエンコードする"""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

# 画像パスの設定
image_path = "/home/masato/Downloads/IMG_0981.jpeg"
base64_image = encode_image(image_path)

# APIリクエストの設定
headers = {
    "Content-Type": "application/json",
    "Authorization": "Bearer 7b8f8a4473bf473e9c8bac9fac656059.NBXhhQlGhMOeu3yR"
}

payload = {
    "model": "glm-4.7",
    "messages": [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "この画像には何が写っていますか？"
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"base64,{base64_image}"
                    }
                }
            ]
        }
    ]
}

response = requests.post("https://api.z.ai/api/coding/paas/v4/chat/completions" ,headers=headers, json=payload)

print(response.json())


