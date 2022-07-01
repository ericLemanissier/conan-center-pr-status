import re
from datetime import datetime
import subprocess
import json
import os
import xml.etree.ElementTree as ET
import requests

f_regex = re.compile("^(\d+)(-(.+))?$")

session = requests.Session()

def process_pr(pr_number):
    last_stamp = None

    root_url = f"https://c3i.jfrog.io/c3i/misc/logs/pr/{pr_number}"


    def iterate_folder(path, depth = 1):
        nonlocal  last_stamp
        global session
        r = session.request("PROPFIND", path, headers={"Depth" : str(depth)})
        r.raise_for_status()
        root = ET.fromstring(r.text)
        base_path = None
        for e in root:
            href_el = e[0]
            assert href_el.tag == "{DAV:}href"


            class Entry:
                is_dir: bool 
                name: str
                path: str

            res = Entry()

            cur_path = href_el.text
            if not base_path:
                base_path = cur_path
                continue
            assert cur_path.startswith(base_path)
            res.name = cur_path[len(base_path) + 1:]
            res.path = f"{path}/{res.name}"

            propstat_el = e[1]
            assert propstat_el.tag == "{DAV:}propstat"

            res.is_dir = False
            props_el = propstat_el[0]
            assert props_el.tag == "{DAV:}prop"
            for prop in props_el:
                if prop.tag == "{DAV:}resourcetype":
                    res.is_dir = any(type.tag == "{DAV:}collection" for type in prop)
                if prop.tag == "{DAV:}creationdate":
                    creationdate= datetime.fromisoformat(prop.text[:-1])
                    if not last_stamp or creationdate > last_stamp:
                        last_stamp = creationdate

            yield res

    build_number = 0
    configs = []
    for entry in iterate_folder(root_url):
        if not entry.is_dir:
            continue
        result = f_regex.match(entry.name)
        current_build_number = int(result.group(1))
        config = result.group(3)
        if current_build_number > build_number:
            build_number = current_build_number
            configs = []
        if current_build_number == build_number:
            configs.append(config)

    status_dict = {}
    package_name = ""
    def process_config(path, config):
        nonlocal status_dict
        nonlocal package_name
        for p in iterate_folder(path, depth=2):
            if not p.is_dir:
                continue
            v = p.name.split("/")
            if len(v) < 2:
                    continue
            else:
                package_name = v[0]
                version = v[1]
                if version not in status_dict:
                    status_dict[version] = {}

            status = f"[in progress]({p.path})"
                n_profile = 0
                n_build = 0
                n_test = 0

            for f in iterate_folder(p.path):
                    if f.name.endswith("-profile.txt"):
                        n_profile += 1
                    if f.name.endswith("-build.txt"):
                        n_build += 1
                    if f.name.endswith("-test.txt"):
                        n_test += 1
                    if f.name == "summary.json":
                        status = f"[finished](https://c3i.jfrog.io/c3i/misc/summary.html?json={f.path})"

                descr = f"{status}"
                if n_profile:
                    descr += f", {n_profile}&nbsp;profiles"
                if n_build:
                    descr += f", {n_build}&nbsp;builds"
                if n_test:
                    descr += f", {n_test}&nbsp;tests"
                status_dict[version][config or "global"] = descr

    if not build_number:
        md = f"build of {pr_number} did not start yet\n"
    else:
        for config in configs:
            current_path = f"{root_url}/{build_number}" + (f"-{config}" if config else "")
            if config == "configs":
                for entry in iterate_folder(current_path):
                    if not entry.is_dir:
                        continue
                    process_config(entry.path, entry.name)
            else:
                process_config(current_path, config)

        md = f"\n# {package_name} [#{pr_number}](https://github.com/conan-io/conan-center-index/pull/{pr_number})\n\n"
        md += f"[build {build_number}]({root_url}). last update on {last_stamp}\n"
        configs = ["global", "linux-gcc", "linux-clang", "windows-visual_studio", "macos-clang", "macos-m1-clang"]
        md += "\n| version |"
        for config in configs:
            md += f" {config} |"
        md += "\n"

        md += "| - |"
        for config in configs:
            md += " - |"
        md += "\n"

        for version, i in status_dict.items():
            md += f"| {version} |"
            for config in configs:
                md += f" {i.get(config, '')} |"
            md += "\n"
        md += "\n"
    return md

if __name__ == '__main__':
    command = ["gh", "pr", "list", "--json", "number,author,labels,statusCheckRollup", "--repo", "conan-io/conan-center-index", "--limit", "200"]
    output = subprocess.check_output(command)
    prs = json.loads(output)
    os.mkdir("pr")
    os.mkdir("author")
    for pr in prs:
        md = process_pr(pr["number"])
        
        print(md)
        with open(f"pr/{pr['number']}.md", "w") as text_file:
            text_file.write(md)
        with open(f"index.md", "a") as text_file:
            text_file.write(md)
        with open(f"author/{pr['author']['login']}.md", "a") as text_file:
            text_file.write(md)
        if  all(label["name"] not in ["Failed",  "User-approval pending", "Unexpected Error"] for label in pr['labels']) and \
            all(check.get("context", "") != "continuous-integration/jenkins/pr-merge" or check.get("state","") not in ["ERROR", "SUCCESS"] for check in pr["statusCheckRollup"]):
                with open(f"in_progress.md", "a") as text_file:
                    text_file.write(md)
