import os
import re
from datetime import datetime, timezone
import subprocess
import json
import sys

f_regex = re.compile("^(\d+)(-(.+))?$")

def process_pr(pr_number):
    last_stamp = 0

    root_url = f"//c3i.jfrog.io/c3i/misc/logs/pr/{pr_number}"

    def iterate_folder(path):
        nonlocal  last_stamp
        try:
            with os.scandir(path) as it:
                for entry in it:
                    last_stamp = max(entry.stat().st_mtime, last_stamp)
                    if entry.name.startswith('.'):
                        continue
                    yield entry
        except FileNotFoundError:
            print(f"file not found {path}")

    build_number = 0
    configs = []
    for entry in iterate_folder(root_url):
        if not entry.is_dir():
            continue
        result = f_regex.match(entry.name)
        current_build_number = int(result.group(1))
        config = result.group(3)
        if current_build_number > build_number:
            build_number = current_build_number
            configs = []
        if current_build_number == build_number:
            configs.append(config)

    if not build_number:
        print(f"build of {pr_number} did not start yet")
        return

    status_dict = {}
    package_name = ""
    def process_config(path, config):
        nonlocal status_dict
        nonlocal package_name
        for p in iterate_folder(path):
            if not p.is_dir():
                continue
            package_name = p.name
            for v in iterate_folder(p.path):
                if not v.is_dir():
                    continue
                version = v.name
                if version not in status_dict:
                    status_dict[version] = {}

                status = "[in progress](https:%s)" % v.path.replace('\\', '/')
                n_profile = 0
                n_build = 0
                n_test = 0

                for f in iterate_folder(v.path):
                    if f.name.endswith("-profile.txt"):
                        n_profile += 1
                    if f.name.endswith("-build.txt"):
                        n_build += 1
                    if f.name.endswith("-test.txt"):
                        n_test += 1
                    if f.name == "summary.json":
                        status = "[finished](https://c3i.jfrog.io/c3i/misc/summary.html?json=https:%s)" % f.path.replace('\\', '/')

                descr = f"{status}"
                if n_profile:
                    descr += f", {n_profile}&nbsp;profiles"
                if n_build:
                    descr += f", {n_build}&nbsp;builds"
                if n_test:
                    descr += f", {n_test}&nbsp;tests"
                status_dict[version][config or "global"] = descr

    for config in configs:
        current_path = os.path.join(root_url, f"{build_number}" + (f"-{config}" if config else ""))
        if config == "configs":
            for entry in iterate_folder(current_path):
                if not entry.is_dir():
                    continue
                process_config(entry.path, entry.name)
        else:
            process_config(current_path, config)

    print(f"\n# {package_name}\n")
    print(f"[pr #{pr_number}](https://github.com/conan-io/conan-center-index/pull/{pr_number}) [build {build_number}]({root_url}). last update on ", end="")
    print(datetime.fromtimestamp(last_stamp,tz=timezone.utc))
    print("")
    configs = ["global", "linux-gcc", "linux-clang", "windows-visual_studio", "macos-clang", "macos-m1-clang"]
    print("| version |", end="")
    for config in configs:
        print(f" {config} |", end="")
    print("")

    print("| - |", end="")
    for config in configs:
        print(" - |", end="")
    print("")

    for version, i in status_dict.items():
        print(f"| {version} |", end="")
        for config in configs:
            print(f" {i.get(config, '')} |", end="")
        print("")
    print("")

if __name__ == '__main__':
    command = ["gh", "pr", "list", "--json", "number", "--repo", "conan-io/conan-center-index"]
    command.extend(sys.argv[1:])
    output = subprocess.check_output(command)
    prs = json.loads(output)
    for pr in prs:
        process_pr(pr["number"])
