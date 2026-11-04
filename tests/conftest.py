import os
import tempfile

# 必须在导入 app.main 之前设置
os.environ["WEBHOOK_STORAGE_DIR"] = tempfile.mkdtemp(prefix="webhook-api-test-")
os.environ["WEBHOOK_API_KEY"] = "test-key"
os.environ.pop("WEBHOOK_ALLOW_NO_AUTH", None)
os.environ["WEBHOOK_CHAT_TIMEOUT"] = "2"
