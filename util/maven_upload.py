import hashlib
import io
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
import paramiko
import requests
import json
from datetime import datetime

host = os.getenv('SSH_HOST')
user = os.getenv('SSH_USER') or 'liukrast'
private_key = os.getenv('SSH_KEY')  # password login is disabled on the server

METADATA_FILE = "maven-metadata.xml"
CHECKSUMS = ("md5", "sha1", "sha256", "sha512")


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

def role_mention(env_name, default_id):
    role = (os.getenv(env_name) or "").strip() or default_id
    return "" if role.lower() == "none" else f"<@&{role}>"


def discordBroadcast(maven_url):
    webhook = os.getenv("DISCORD_WEBHOOK")
    name = os.getenv("MOD_NAME")
    version = os.getenv("MOD_VERSION")
    curse_id = os.getenv("CURSE_ID")

    is_major = os.getenv('MAJOR_VERSION', 'false').lower() == 'true'

    cf_data = get_cfwidget_data("mc-mods", curse_id)
    cf_url = os.getenv("CURSEFORGE_URL")
    cf_icon = cf_data.get("thumbnail", "")

    # Role IDs to ping; empty = liukrast's Discord roles, "none" = no ping
    roles = [role_mention("DISCORD_ROLE", "1495906919880327359")]
    if is_major:
        roles.append(role_mention("DISCORD_MAJOR_ROLE", "1371083417336414219"))
    roles = [r for r in roles if r]
    content_tag = f"-# {' '.join(roles)}" if roles else ""

    changelog = ""
    if os.path.exists("changelog.md"):
        with open("changelog.md", "r", encoding="utf-8") as f:
            changelog = f.read()

    payload = {
        "content": content_tag,
        "embeds": [
            {
                "title": f"{name} • v{version}",
                "url": cf_url,
                "color": 7049700,
                "description": (
                    f"**Minecraft Version:** `{mc_version}`\n\n"
                    f"<:clipboard:1483827195662303302> **Changelog:**\n"
                    f"{changelog[:3000]}"
                ),
                "fields": [
                    {
                        "name":"Download",
                        "value": (
                            f"<:curseforge:1467573317970952332> [CurseForge]({cf_url})\n"
                            f"<:modrinth:1467573288321548485> [Modrinth]({os.getenv('MODRINTH_URL')})"
                        ),
                        "inline": True
                    },
                    {
                        "name":"Developer stuff",
                        "value": f"<:maven:1467633150753505320> [Maven](https://maven.liukrast.net/{maven_url})",
                        "inline": True
                    }
                ],
                "footer": {
                    "text": f"{name} • Automatic Release System"
                },
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
        ]
    }

    response = requests.post(webhook, json=payload)
    if response.status_code != 204:
        print(f"⚠️ Discord webhook failed: {response.status_code} {response.text}")


def load_private_key(text):
    for key_class in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
        try:
            return key_class.from_private_key(io.StringIO(text))
        except paramiko.SSHException:
            continue
    sys.exit("SSH_KEY is not a supported private key.")


def connect():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username=user, pkey=load_private_key(private_key), look_for_keys=False, allow_agent=False)
    return client


def version_key(version):
    return [(0, int(part)) if part.isdigit() else (-1, part) for part in re.split(r"[.\-+]", version)]


def read_versions(xml_bytes):
    if not xml_bytes:
        return []
    root = ET.fromstring(xml_bytes)
    return [node.text for node in root.findall("./versioning/versions/version") if node.text]


def build_metadata(group, artifact, versions):
    newest = versions[-1]
    root = ET.Element("metadata")
    ET.SubElement(root, "groupId").text = group
    ET.SubElement(root, "artifactId").text = artifact
    versioning = ET.SubElement(root, "versioning")
    ET.SubElement(versioning, "latest").text = newest
    ET.SubElement(versioning, "release").text = newest
    versions_node = ET.SubElement(versioning, "versions")
    for version in versions:
        ET.SubElement(versions_node, "version").text = version
    ET.SubElement(versioning, "lastUpdated").text = time.strftime("%Y%m%d%H%M%S", time.gmtime())
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8") + b"\n"


def read_remote(sftp, path):
    try:
        with sftp.open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


def write_remote(sftp, path, data):
    with sftp.open(path, "wb") as f:
        f.write(data)
    for algorithm in CHECKSUMS:
        with sftp.open(f"{path}.{algorithm}", "w") as f:
            f.write(hashlib.new(algorithm, data).hexdigest())


def merge_metadata(sftp, local_artifact_dir, remote_artifact_dir, group, artifact):
    local_path = os.path.join(local_artifact_dir, METADATA_FILE)
    with open(local_path, "rb") as f:
        local_versions = read_versions(f.read())
    remote_versions = read_versions(read_remote(sftp, f"{remote_artifact_dir}/{METADATA_FILE}"))
    versions = sorted(set(remote_versions) | set(local_versions), key=version_key)
    write_remote(sftp, f"{remote_artifact_dir}/{METADATA_FILE}", build_metadata(group, artifact, versions))
    print(f"Merged {METADATA_FILE}: {len(remote_versions)} remote + {len(local_versions)} local -> {len(versions)} versions")


def is_metadata_file(name):
    return name == METADATA_FILE or any(name == f"{METADATA_FILE}.{algorithm}" for algorithm in CHECKSUMS)


if not host or not private_key:
    sys.exit("Missing required environment variables: SSH_HOST and SSH_KEY.")

props = {}
try:
    with open("gradle.properties", "r", encoding="utf-8", errors="ignore") as f:
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

    ssh = connect()
    sftp = ssh.open_sftp()

    current_path = ""
    for folder in remote_dir.strip("/").split("/"):
        current_path = f"/{folder}" if not current_path else f"{current_path}/{folder}"
        try:
            sftp.stat(current_path)
        except FileNotFoundError:
            sftp.mkdir(current_path)

    for root, dirs, files in os.walk(local_repo_root):
        for file in files:
            if is_metadata_file(file):
                continue
            local_file = os.path.join(root, file)
    
            relative = os.path.relpath(local_file, local_repo_root)
            remote_file = f"{remote_maven_root}/{relative}".replace("\\", "/")
    
            remote_folder = os.path.dirname(remote_file)
    
            # create remote dirs
            current = ""
            for folder in remote_folder.strip("/").split("/"):
                current = f"/{folder}" if not current else f"{current}/{folder}"
                try:
                    sftp.stat(current)
                except FileNotFoundError:
                    sftp.mkdir(current)
    
            sftp.put(local_file, remote_file)
            print(f"Uploaded: {relative}")

    artifact_relative = os.path.dirname(relative_path)
    merge_metadata(sftp, os.path.join(local_repo_root, artifact_relative), f"{remote_maven_root}/{artifact_relative}", group_id, artifact_id)

    sftp.close()

    cmd = "/usr/local/bin/maven-reload"
    _, stdout, stderr = ssh.exec_command(cmd)

    print(stdout.read().decode().strip())
    print(stderr.read().decode().strip())
    if stdout.channel.recv_exit_status() != 0:
        print("⚠️ Maven frontend reload failed, run it manually on the server.")

    ssh.close()

    discordBroadcast(relative_path)

except Exception as e:
    sys.exit(f"Error: {e}")
