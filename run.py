import time
import os


os.system('uvicorn main:app --reload --host 0.0.0.0 --port 8000 &')
time.sleep(2)
print("server is up")
os.system('firefox templates/index.html')
print("everything is working")

