import os
import sys
import paramiko
import requests
import json

host = os.getenv('SSH_HOST')
user = os.getenv('SSH_USER')
password = os.getenv('SSH_PASS')


local_repo_root = "repo"
remote_maven_root = "/var/www/maven"


def get_cfwidget_data(path, mod_id):
    url = f"https://api.cfwidget.com/minecraft/{path}/{mod_id}"
    try:
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"⚠️ CFWidget error: {e}")
    return {}

def discordBroadcast(maven_url):
    webhook = os.getenv("DISCORD_WEBHOOK")
    name = os.getenv("MOD_NAME")
    version = os.getenv("MOD_VERSION")
    curse_id = os.getenv("CURSE_ID")

    cf_data = get_cfwidget_data("mc-mods", curse_id)
    cf_icon = cf_data.get("thumbnail", "")

    changelog = ""
    if os.path.exists("changelog.md"):
        with open("changelog.md", "r", encoding="utf-8") as f:
            changelog = f.read()

    payload = {
        "content": "-# <@&1371083417336414219>",
        "embeds": [
            {
                "description": f"**Changelog:**\n{changelog[:4096]}",
                "color": 7049700,
                "fields": [
                    {
                        "name": "<:curseforge:1467573317970952332> CurseForge",
                        "value": f"[Download on CurseForge]({os.getenv("CURSEFORGE_URL")})"
                    },
                    {
                        "name": "<:modrinth:1467573288321548485> Modrinth",
                        "value": f"[Download on Modrinth](https://modrinth.com/mod/{os.getenv("MODRINTH_URL")})",
                        "inline": True
                    },
                    {
                        "name": "🔗 Maven",
                        "value": f"[Find Maven files](https://maven.liukrast.net/{maven_url})"
                    }
                ],
                "author": {
                    "name": f"{name} v{version} [Click to Download]",
                    "url": cf_url,
                    "icon_url": cf_icon
                }
            }
        ],
        "attachments": []
    }

    response = requests.post(webhook, json=payload)
    if response.status_code != 204:
        print(f"⚠️ Discord webhook failed: {response.status_code} {response.text}")



if not all([host, user, password]):
    sys.exit("Missing required environment variables.")

props = {}
try:
    with open("gradle.properties", "r") as f:
        for line in f:
            if "=" in line and not line.strip().startswith("#"):
                key, val = line.strip().split("=", 1)
                props[key.strip()] = val.strip()
except FileNotFoundError:
    sys.exit("gradle.properties not found.")

try:
    group_id = props.get('mod_group_id')
    mod_id = props['mod_id']
    mc_version = props['minecraft_version']
    version = props['mod_version']
    
    artifact_id = f"{mod_id}-{mc_version}"
    relative_path = os.path.join(group_id.replace('.', '/'), artifact_id, version)
    relative_path = relative_path.replace('\\', '/')

    
    local_dir = os.path.join(local_repo_root, relative_path)
    remote_dir = f"{remote_maven_root}/{relative_path}"

    if not os.path.exists(local_dir):
        sys.exit(f"Local artifacts not found at {local_dir}")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, username=user, password=password)
    sftp = ssh.open_sftp()

    current_path = ""
    for folder in remote_dir.strip("/").split("/"):
        current_path = f"/{folder}" if not current_path else f"{current_path}/{folder}"
        try:
            sftp.stat(current_path)
        except FileNotFoundError:
            sftp.mkdir(current_path)

    for filename in os.listdir(local_dir):
        local_file = os.path.join(local_dir, filename)
        remote_file = f"{remote_dir}/{filename}"
        if os.path.isfile(local_file):
            sftp.put(local_file, remote_file)
            print(f"Uploaded: {filename}")
    
    sftp.close()

    cmd = "cd /opt/maven_server && python3 reload.py"
    _, stdout, stderr = ssh.exec_command(cmd)
    
    print(stdout.read().decode().strip())
    print(stderr.read().decode().strip())

    ssh.close()

    discordBroadcast(relative_path)

except Exception as e:
    sys.exit(f"Error: {e}")

