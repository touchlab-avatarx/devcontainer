'''Use devcontainers outside of VSCode and Codespaces.

I love the idea of devcontainers, but I don't always want to use them in VSCode or a Codespace. I was inspired by https://blog.wille-zone.de/post/run-devcontainer-outside-of-visual-studio-code/ and https://github.com/BorisWilhelms/devcontainer, where a shell script was used to parse the devcontainer.json file, build and then run a Dockerfile. I ran into some limitations with it, and decided to write a Python version.

 This project mimics as much as I could figure out how to run a devcontainer locally using as many of the devcontainer.json settings as we can. 

In your project that uses a devcontainer, you simply run this command:

> devcontainer

It will launch the Docker image, or build the Dockerfile and launch it.


This assumes you are running Docker.
'''

import argparse
import os
import jstyleson
import subprocess
import sys
import hashlib
import re
import signal
import asyncio

container_name = ''


def substitute_env(val):
    # Format variables
    val = val.replace("${localEnv:", "{")
    # Replace defined variables
    for key, value in dict(os.environ).items():
        if val.find("{"+key+"}") >= 0:
            val = val.replace("{"+key+"}", value)
    # Remove undefined variables
    val = re.sub(r"\{[^}]*\}", "", val)
    return val


def stop():
    global container_name
    print("stopping")
    subprocess.Popen(['docker', 'container', 'kill', f'{container_name}'])


def devcontainer():
    asyncio.run(main())

def strip_latest(tag):
    return tag if not tag.endswith(":latest") else tag.split(":")[0]

async def main():
    global container_name
    '''Launch the devcontainer in current directory.'''

    pid = os.getpid()

    signal.signal(signal.SIGINT, lambda signal, frame: stop())
    signal.signal(signal.SIGTERM, lambda signal, frame: stop())
    signal.signal(signal.SIGQUIT, lambda signal, frame: stop())

    if not os.path.isdir('.devcontainer'):
        raise Exception('No .devcontainer found')

    if not os.path.exists('.devcontainer/devcontainer.json'):
        raise Exception('No .devcontainer/devcontainer.json found')

    # Load the json file
    with open('.devcontainer/devcontainer.json') as f:
        # devcontainer.json is usually not valid json; it has comments and
        # allows trailing commas. jstyleson is able to handle these, so we use
        # it here.
        devcontainer = jstyleson.loads(f.read())

    parser = argparse.ArgumentParser(description='devcontainer')
    parser.add_argument('entrypoint', nargs='?',
                        help='Optional entrypoint')

    args = parser.parse_args()

    if REMOTE_USER := devcontainer.get('remoteUser', []):
        REMOTE_USER = ['-u', REMOTE_USER]

    PORTS = []
    for port in devcontainer.get('forwardPorts', []):
        PORTS += ['-p']
        PORTS += [f'{port}:{port}']

    ENVS = []
    for key, val in devcontainer.get('containerEnv', {}).items():
        ENVS += ['-e']
        ENVS += [f'{key}={substitute_env(val)}']

    ENTRYPOINT = args.entrypoint or '/bin/bash'

    # local directory
    WORKSPACE = os.getcwd()
    # where to mount it remotely
    WORK_DIR = devcontainer.get(
        'workspaceFolder', f'/{os.path.split(WORKSPACE)[-1]}')

    if ws := devcontainer.get('workspaceMount', None):
        ws = ws.replace('${localWorkspaceFolder}', WORKSPACE)
        MOUNT = ['--mount', ws]
    else:
        MOUNT = ['--mount', f'type=bind,source={WORKSPACE},target={WORK_DIR}']

    # This is a path relative to .devcontainer I think.
    DOCKERFILE = devcontainer.get('dockerFile', None)

    if DOCKERFILE is None:
        DOCKERIMAGE = devcontainer.get('image', None)

    ARGS = ' '.join([f'--build-arg {key}="{val}"' for key,
                    val in devcontainer.get('build', {}).get('args', {}).items()])

    run_args = devcontainer.get('runArgs', [])
    for i in range(len(run_args)):
        if run_args[i].find(" ") >= 0:
            run_args[i-1] = f"{run_args[i-1]}={run_args[i]}"
            run_args[i] = ""

    # Substitute env vars
    for i in range(len(run_args)):
        run_args[i] = substitute_env(run_args[i])

    RUNARGS = run_args
    container_name = f"devcontainer-{pid}"

    if DOCKERFILE:
        # Build it. we use the md5 hash of the dockerfile as a name.
        TAG = hashlib.md5(
            open(f'.devcontainer/{DOCKERFILE}', 'rb').read()).hexdigest()

        # Check if the image with the computed TAG already exists
        existing_images = subprocess.check_output(
            "docker images --format '{{.Repository}}:{{.Tag}}'", shell=True).decode().splitlines()

        existing_images = [strip_latest(image) for image in existing_images]

        if TAG not in existing_images:
            print(f"Image with TAG {TAG} not found, building the image...")
            subprocess.check_output(
                f'docker build -t {TAG} -f .devcontainer/{DOCKERFILE} {ARGS} .devcontainer',
                shell=True, stderr=subprocess.PIPE)
        else:
            print(f"Using existing image with TAG {TAG}")

        # filter out empty strings
        cmd = [arg for arg in
               ['/usr/bin/docker', 'docker', 'run', '-it', '--rm', "--pid=host", "--stop-signal=SIGKILL", '--name', container_name,
                *REMOTE_USER, *PORTS, *ENVS, *RUNARGS, *MOUNT, '-w', WORK_DIR,
                TAG, "/bin/bash", "-i", "-c", f"source ~/.bashrc;{ENTRYPOINT}"] if arg]

        print(
            f'Mounting local {WORKSPACE} in {WORK_DIR} in "{container_name}"')
        process = await asyncio.create_subprocess_exec(cmd[1], *cmd[2:])
        await process.wait()

    elif DOCKERIMAGE:
        cmd = [arg for arg in ['/usr/bin/docker', 'docker', 'run', '-it', '--rm', "--pid=host", "--stop-signal=SIGTERM", '--name', container_name,
                               *REMOTE_USER, *PORTS, *ENVS, *RUNARGS, *MOUNT, '-w', WORK_DIR,
                               DOCKERIMAGE, "/bin/bash", "-i", "-c", f"source ~/.bashrc;{ENTRYPOINT}"] if arg]
        print(
            f'Mounting local {WORKSPACE} in {WORK_DIR} in "{container_name}"')
        process = await asyncio.create_subprocess_exec(cmd[1], *cmd[2:])
        await process.wait()

    else:
        raise Exception('No Docker image or file found.')
