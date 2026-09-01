import httpx
try:
    res = httpx.get("https://huggingface.co/api/models?search=Qwen2.5%20GGUF&filter=gguf&limit=8&sort=downloads&direction=-1")
    print(len(res.json()))
except Exception as e:
    print("ERROR:", str(e))
