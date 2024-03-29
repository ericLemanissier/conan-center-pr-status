# pylint: disable = invalid-name,too-many-branches, too-many-locals, too-many-statements, too-few-public-methods

import re
from datetime import datetime
import subprocess
import json
import os
import logging
from typing import Dict, Any, List, Tuple, Generator
import xml.etree.ElementTree
import requests
from tqdm import tqdm
from yaml import safe_load
from html_table import html_table

f_regex = re.compile(r"^(\d+)(-(.+))?$")

session = requests.Session()

with open('../conan-center-index/.c3i/config_v1.yml', encoding='utf-8') as config_file:
    config_v1 = safe_load(config_file)


class Entry:
    is_dir: bool
    name: str
    path: str
    date: datetime

    def __init__(self):
        self.is_dir = None
        self.name = None
        self.path = None
        self.date = None


def process_pr(pr: Dict[str, Any]) -> Tuple[str, List[List[str]]]:  # noqa: MC0001
    pr_number = pr["number"]
    last_stamp = None

    table: List[List[str]] = []

    root_url = f"{config_v1['artifactory']['url']}/{config_v1['artifactory']['logs_repo']}/logs/pr/{pr_number}"

    def iterate_folder(path: str, depth: int = 1) -> Generator[Entry, None, None]:
        nonlocal last_stamp
        r = session.request("PROPFIND", path, headers={"Depth": str(depth)})
        r.raise_for_status()
        root = xml.etree.ElementTree.fromstring(r.text)
        base_path = ""
        for e in root:
            href_el = e[0]
            assert href_el.tag == "{DAV:}href"

            res = Entry()

            cur_path = href_el.text
            if not base_path:
                if cur_path:
                    base_path = cur_path
                continue
            assert cur_path
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
                    assert prop.text
                    creationdate = datetime.fromisoformat(prop.text[:-1])
                    res.date = creationdate
                    if not last_stamp or creationdate > last_stamp:
                        last_stamp = creationdate

            yield res

    build_number = 0
    configs: List[str] = []
    for entry in iterate_folder(root_url):
        if not entry.is_dir:
            continue
        result = f_regex.match(entry.name)
        assert result
        current_build_number = int(result.group(1))
        config = result.group(3)
        if current_build_number > build_number:
            build_number = current_build_number
            configs = []
        if current_build_number == build_number:
            configs.append(config)

    status_dict: Dict[str, Dict[str, str]] = {}
    package_name = ""

    def process_config(path: str, config: str) -> None:
        nonlocal status_dict
        nonlocal package_name
        for p in iterate_folder(path, depth=2):
            if not p.is_dir:
                continue
            v = p.name.split("/")
            if len(v) < 2:
                continue
            package_name = v[0]
            version = v[1]
            if version not in status_dict:
                status_dict[version] = {}

            status = f"[in progress]({p.path})"
            n_profile = 0
            n_build = 0
            n_test = 0

            builds: Dict[str, Dict[str, Entry]] = {}

            for f in iterate_folder(p.path):
                if f.name.endswith("-profile.txt"):
                    packageid = f.name[0:-12]
                    if packageid not in builds:
                        builds[packageid] = {}
                    builds[packageid]["profile"] = f
                    n_profile += 1
                if f.name.endswith("-build.txt"):
                    packageid = f.name[0:-10]
                    if packageid not in builds:
                        builds[packageid] = {}
                    builds[packageid]["build"] = f
                    n_build += 1
                if f.name.endswith("-test.txt"):
                    packageid = f.name[0:-9]
                    if packageid not in builds:
                        builds[packageid] = {}
                    builds[packageid]["test"] = f
                    n_test += 1
                if f.name == "summary.json":
                    status = f"[finished](https://c3i.jfrog.io/c3i/misc/summary.html?json={f.path})"
            for packageid, build in builds.items():
                date = max(f.date for f in build.values())
                table.append([
                    f"<a href='{pr['url']}'>#{pr_number}</a>",
                    f"{pr['author']['login']}",
                    f"{package_name}/{version}",
                    f"<a href='{root_url}'>{build_number}</a>",
                    f"{config}",
                    # f"{packageid}",
                    f"<a href=\"{build['profile'].path}\">link</a>" if "profile" in build else "",
                    f"<a href=\"{build['build'].path}\">link</a>" if "build" in build else "",
                    f"<a href=\"{build['test'].path}\">link</a>" if "test" in build else "",
                    f"{date}"])
            descr = f"{status}"
            if n_profile:
                descr += f", {n_profile}&nbsp;profiles"
            if n_build:
                descr += f", {n_build}&nbsp;builds"
            if n_test:
                descr += f", {n_test}&nbsp;tests"
            status_dict[version][config or "global"] = descr

    tags_list: List[str] = []
    for tag in pr["labels"]:
        if tag["description"]:
            tags_list.append(f'[`{tag["name"]}`](# "{tag["description"]}")')
        else:
            tags_list.append(f'`{tag["name"]}`')

    tags: str = ", ".join(tags_list)

    status = "NOT YET STARTED"
    for check in pr["statusCheckRollup"] or []:
        if check.get("context", "") == "continuous-integration/jenkins/pr-merge":
            status = check.get("state", "UNDEFINED")

    if not build_number:
        md = f"\n# [#{pr_number}]({pr['url']}): {status}\n\n"
        if tags:
            md += f"labels: {tags}\n\n"
        md += "build did not start yet\n"
    else:
        for config in configs:
            current_path = f"{root_url}/{build_number}" + (f"-{config}" if config else "")
            process_config(current_path, config)

        md = f"\n# {package_name} [#{pr_number}]({pr['url']}): {status}\n\n"
        if tags:
            md += f"labels: {tags}\n\n"
        md += f"[build {build_number}]({root_url}). last update on {last_stamp}\n"
        configs = ["global"] + [c['id'] for c in config_v1['configurations']]
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
    return md, table


def append_to_file(content: str, filename: str) -> None:
    file_exists = os.path.isfile(filename)
    try:
        with open(filename, "at", encoding="latin_1") as text_file:
            if not file_exists:
                text_file.write("page generated on {{ site.time | date_to_xmlschema }}\n\n")
            text_file.write(content)
    except OSError as err:
        logging.error("Error appending to file %s: %s", filename, err)


def main() -> None:  # noqa: MC0001
    output = subprocess.check_output(["gh", "api", "rate_limit"])
    rate_limit = json.loads(output)
    if rate_limit["resources"]["graphql"]["remaining"] < 1:
        logging.error("github API rate limit reached: %s", rate_limit["resources"]["graphql"])
        with open(os.getenv("GITHUB_OUTPUT", "GITHUB_OUTPUT"), "at", encoding="utf-8") as file:
            file.write("API_REMAINING=0")
        return
    command = ["gh", "pr", "list", "--json", "number", "--repo", "conan-io/conan-center-index", "--limit", "2000",
               "--search", "-label:\"User-approval pending\" -author:conan-center-bot -label:\"C3I config\" -label:Docs -label:stale"]
    output = subprocess.check_output(command)
    prs = json.loads(output)
    output = subprocess.check_output(["gh", "api", "rate_limit"])
    rate_limit = json.loads(output)
    if rate_limit["resources"]["graphql"]["remaining"] < len(prs):
        logging.error("github API rate limit reached for %s prs: %s", len(prs), rate_limit["resources"]["graphql"])
        with open(os.getenv("GITHUB_OUTPUT", "GITHUB_OUTPUT"), "at", encoding="utf-8") as file:
            file.write("API_REMAINING=0")
        return
    os.makedirs("pr", exist_ok=True)
    os.makedirs("author", exist_ok=True)
    os.makedirs("_includes", exist_ok=True)

    thead = ["PR",
             "Author",
             "Reference",
             "Build Number",
             "Config",
             "profile",
             "build",
             "test",
             "date"]

    index = "index.md"

    with html_table("build_log_table.html", thead) as build_log_table:

        append_to_file("This page lists all the ongoing pull requests on conan-center-index.\\\n", index)
        url = "{{ site.url }}/conan-center-pr-status/author/author_handle"
        append_to_file(f"You can filter by author by going to [{url}]({url}).\\\n", index)
        url = "{{ site.url }}/conan-center-pr-status/pr/pr_number"
        append_to_file(f"You can view a specific PR by going to [{url}]({url}).\n\n", index)
        url = "{{ site.url }}/conan-center-pr-status/build_log_table"
        append_to_file(f"You can view all the jobs in tabular view by going to [{url}]({url}).\n\n", index)

        in_progress_jobs: Dict[str, List[List[str]]] = {}

        for pr in tqdm(prs):
            command = ["gh", "pr", "view", str(pr['number']), "--json", "number,author,labels,statusCheckRollup,url", "--repo", "conan-io/conan-center-index"]
            output = subprocess.check_output(command)
            pr = json.loads(output)
            md, table = process_pr(pr)
            html = ""
            for line in table:
                html += "<tr>"
                for cell in line:
                    html += f"<td>{cell}</td>" if cell else "<td/>"
                html += "</tr>"

            with open(f"_includes/{pr['number']}.md", "w", encoding="latin_1") as text_file:
                text_file.write(md)
            with html_table(f"pr/{pr['number']}_table.html", thead) as pr_table:
                pr_table.write(html)
            md = "{% include " + str(pr['number']) + ".md %}\n"
            append_to_file(md, f"pr/{pr['number']}.md")
            append_to_file(md, index)
            append_to_file(md, f"author/{pr['author']['login']}.md")
            if all(label["name"] not in ["User-approval pending"] for label in pr['labels']) and \
                all(check.get("context", "") != "continuous-integration/jenkins/pr-merge" or check.get("state", "") not in ["ERROR", "SUCCESS"]
                    for check in pr["statusCheckRollup"] or []):
                append_to_file(md, "in_progress.md")
                build_log_table.write(html)

                for line in table:
                    job: Dict[str, str] = {}
                    for i, header in enumerate(thead):
                        job[header] = line[i]
                    if job["Config"] not in in_progress_jobs:
                        in_progress_jobs[job["Config"]] = []
                    if job["test"]:
                        continue
                    in_progress_jobs[job["Config"]].append([
                        job["PR"],
                        job["Reference"],
                        job["profile"],
                        job["date"]])

    in_progress_jobs_file = "in_progress_jobs.md"
    for config, jobs in in_progress_jobs.items():
        append_to_file(f"\n# {config}\n", in_progress_jobs_file)
        append_to_file(f"Number of builds in progress: {len(jobs)}\n\n", in_progress_jobs_file)
        if jobs:
            append_to_file("| PR | Reference | profile | date |\n", in_progress_jobs_file)
            append_to_file("| - | - | - | - |\n", in_progress_jobs_file)
            for j in jobs:
                append_to_file(f"| {' | '.join(j)} |\n", in_progress_jobs_file)


if __name__ == '__main__':
    main()
