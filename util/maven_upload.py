import os
import sys
import paramiko

host = os.getenv('SSH_HOST')
user = os.getenv('SSH_USER')
password = os.getenv('SSH_PASS')
local_repo_root = "repo"
remote_maven_root = "/var/www/maven"

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
    group_id = props.get('group') or props.get('project_group')
    mod_id = props['mod_id']
    mc_version = props['minecraft_version']
    version = props['mod_version']
    
    artifact_id = f"{mod_id}-{mc_version}"
    relative_path = os.path.join(group_id.replace('.', '/'), artifact_id, version)
    
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

    cmd = "cd /root && ./reload_maven.py"
    _, stdout, stderr = ssh.exec_command(cmd)
    
    print(stdout.read().decode().strip())
    print(stderr.read().decode().strip())

    ssh.close()

except Exception as e:
    sys.exit(f"Error: {e}")