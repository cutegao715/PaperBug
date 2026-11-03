import uvicorn


def main():
    uvicorn.run("paperagent_cloud.app:app", host="127.0.0.1", port=8765)
