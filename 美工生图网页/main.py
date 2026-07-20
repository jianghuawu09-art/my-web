import os
import sys
from flask import Flask, request, jsonify, send_from_directory
from datetime import datetime
import requests
import json
import re
import time
import base64

app = Flask(__name__, static_folder='static')


def _log(msg):
    """直接写 stderr，不受 Flask reloader 影响"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    sys.stderr.write(f"[{now}] {msg}\n")
    sys.stderr.flush()


def get_client_ip():
    """获取客户端真实IP地址（支持反向代理环境）"""
    if 'X-Forwarded-For' in request.headers:
        return request.headers['X-Forwarded-For'].split(',')[0].strip()
    elif 'X-Real-IP' in request.headers:
        return request.headers['X-Real-IP'].strip()
    else:
        return request.remote_addr or '未知'


@app.before_request
def log_every_request():
    """每次请求自动打印日志：时间 + IP + 路径"""
    path = request.path
    if path.startswith('/static/'):
        return
    ip = get_client_ip()
    method = request.method
    _log(f">>> [访问] IP: {ip} | {method} {path}")


def log_request(action):
    """打印操作日志：时间 + IP + 操作"""
    ip = get_client_ip()
    _log(f">>> [操作] IP: {ip} | {action}")

# ======================【配置 - 请在这里修改】======================
COMFLY_API_KEY = "sk-0IkV1qLCBb12pKgjVJdysuzdmjdRPUVjhZLdP5TSvtA2qFqc"
COMFLY_CHAT_URL = "https://ai.comfly.chat/v1/chat/completions"
COMFLY_IMAGES_URL = "https://ai.comfly.chat/v1/images/generations"

MODELS_TEXT_TO_IMAGE = [
    "nano-banana",
]

MODEL_IMAGE_TO_IMAGE = "gemini-3.1-flash-image-preview-4k"

# ======================【工具函数】======================
def _http_session():
    s = requests.Session()
    s.trust_env = False
    return s

def _strip_backticks(s):
    return (s or "").strip().strip("`").strip()

def _extract_download_url(text):
    if not text:
        return None
    m = re.search(r"\[下载\d*\]\((https?://[^)]+)\)", text)
    if m:
        return m.group(1).strip()
    m = re.search(r"(https?://\S+)", text)
    if m:
        return m.group(1).strip().rstrip(").,]}")
    return None

# ======================【文生图API】======================
@app.route('/api/text-to-image', methods=['POST'])
def text_to_image():
    prompt = request.form.get('prompt', '').strip()
    if not prompt:
        return jsonify({"error": "提示词不能为空"}), 400

    log_request(f"文生图 | 提示词: {prompt[:50]}{'...' if len(prompt) > 50 else ''}")
    
    results = []
    for model in MODELS_TEXT_TO_IMAGE:
        url = _strip_backticks(COMFLY_CHAT_URL)
        api_key = (COMFLY_API_KEY or "").strip()
        if not api_key:
            results.append({"model": model, "error": "COMFLY_API_KEY 为空"})
            continue
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "stream": False,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
        }
        
        try:
            s = _http_session()
            r = s.post(url, headers=headers, json=payload, timeout=90, verify=False)
            
            if r.status_code != 200:
                results.append({"model": model, "error": f"调用失败（HTTP {r.status_code}）"})
                continue
            
            data = r.json()
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            link = _extract_download_url(content)
            
            if link:
                results.append({"model": model, "success": True, "url": link})
            else:
                results.append({"model": model, "success": False, "error": "未解析到下载链接"})
                
        except Exception as e:
            results.append({"model": model, "success": False, "error": str(e)})
    
    return jsonify({"prompt": prompt, "results": results})

# ======================【图生组图API】======================
@app.route('/api/image-to-images', methods=['POST'])
def image_to_images():
    base_prompt = request.form.get('base_prompt', '').strip()
    scenes = request.form.get('scenes', '')

    if not base_prompt:
        return jsonify({"error": "基础要求不能为空"}), 400
    
    if 'image' not in request.files:
        return jsonify({"error": "请上传参考图片"}), 400
    
    image_file = request.files['image']
    
    try:
        scenes_list = [s.strip() for s in scenes.split("\n") if s.strip()]
    except:
        scenes_list = []
    
    if not scenes_list:
        scenes_list = ["生成一张图片"]

    log_request(f"图生组图 | 基础要求: {base_prompt[:30]}... | 场景数: {len(scenes_list)}")
    
    try:
        image_bytes = image_file.read()
        ref_image_base64 = f"data:image/jpeg;base64,{base64.b64encode(image_bytes).decode('utf-8')}"
    except Exception as e:
        return jsonify({"error": f"图片解析失败: {str(e)}"}), 400
    
    results = []
    model = MODEL_IMAGE_TO_IMAGE
    
    headers = {
        "Authorization": f"Bearer {COMFLY_API_KEY}",
        "Content-Type": "application/json"
    }
    
    for idx, scene in enumerate(scenes_list, 1):
        full_prompt = f"""{base_prompt}

【场景要求】{scene}

【输出要求】生成一张高质量的图片，严格遵循参考图中的衣服。"""
        
        payload = {
            "prompt": full_prompt,
            "model": model,
            "temperature": 0.7,
            "aspect_ratio": "1:1",
            "image_size": "4K",
            "image": ref_image_base64
        }
        
        try:
            s = _http_session()
            response = s.post(COMFLY_IMAGES_URL, headers=headers, json=payload, timeout=(30, 180), verify=False)
            
            if response.status_code != 200:
                results.append({"scene": scene, "success": False, "error": f"API错误: {response.status_code}"})
                continue
            
            result = response.json()
            
            if "data" in result and len(result["data"]) > 0:
                img_data = result["data"][0]
                
                if "url" in img_data and img_data["url"]:
                    results.append({"scene": scene, "success": True, "url": img_data["url"]})
                elif "b64_json" in img_data and img_data["b64_json"]:
                    b64_data = img_data["b64_json"]
                    if not b64_data.startswith("data:image"):
                        b64_data = f"data:image/jpeg;base64,{b64_data}"
                    results.append({"scene": scene, "success": True, "url": b64_data})
                else:
                    results.append({"scene": scene, "success": False, "error": "响应格式异常"})
            else:
                results.append({"scene": scene, "success": False, "error": "未生成图片"})
                
        except Exception as e:
            results.append({"scene": scene, "success": False, "error": str(e)})

        status = "✅成功" if results[-1].get("success") else "❌失败"
        ip = get_client_ip()
        _log(f"  >>> IP: {ip} | 场景 {idx}/{len(scenes_list)} {status} - {scene[:30]}")

        time.sleep(3)
    
    success_count = sum(1 for r in results if r["success"])
    return jsonify({
        "base_prompt": base_prompt,
        "scenes": scenes_list,
        "model": model,
        "success_count": success_count,
        "total_count": len(scenes_list),
        "results": results
    })

# ======================【静态文件服务】======================
@app.route('/')
def index():
    log_request("访问主页")
    return send_from_directory('static', 'index.html')

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8001))
    _log("=" * 60)
    _log("服务启动成功！")
    _log(f"  本机访问: http://127.0.0.1:{port}")
    _log(f"  局域网访问: http://192.168.1.121:{port}")
    _log("  等待访问中... 有人使用时会打印 IP 日志")
    _log("=" * 60)
    app.run(host="0.0.0.0", port=port, debug=True)