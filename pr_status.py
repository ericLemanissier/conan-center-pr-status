# pylint: disable = invalid-name,too-many-branches, too-many-locals, too-many-statements too-few-public-methods, redefined-outer-name

import re
from datetime import datetime
import subprocess
import json
import os
import textwrap
from typing import Dict, Any, List, Tuple, Generator
import xml.etree.ElementTree
import requests

f_regex = re.compile(r"^(\d+)(-(.+))?$")

session = requests.Session()


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


def process_pr(pr: Dict[str, Any]) -> Tuple[str, List[List[str]]]: # noqa: MC0001
    pr_number = pr["number"]
    last_stamp = None

    table: List[List[str]] = []

    root_url = f"https://c3i.jfrog.io/c3i/misc/logs/pr/{pr_number}"

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
            if config == "configs":
                for entry in iterate_folder(current_path):
                    if not entry.is_dir:
                        continue
                    process_config(entry.path, entry.name)
            else:
                process_config(current_path, config)

        md = f"\n# {package_name} [#{pr_number}]({pr['url']}): {status}\n\n"
        if tags:
            md += f"labels: {tags}\n\n"
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
    return md, table


def append_to_file(content: str, filename: str) -> None:
    file_exists = os.path.isfile(filename)
    with open(filename, "a", encoding="latin_1") as text_file:
        if not file_exists:
            text_file.write("page generated on {{ site.time | date_to_xmlschema }}\n\n")
        text_file.write(content)


if __name__ == '__main__':
    command = ["gh", "pr", "list", "--json", "number", "--repo", "conan-io/conan-center-index", "--limit", "2000"]
    output = subprocess.check_output(command)
    prs = json.loads(output)
    os.makedirs("pr", exist_ok=True)
    os.makedirs("author", exist_ok=True)
    os.makedirs("_includes", exist_ok=True)

    html_file = open("table.html", "wt", encoding="latin_1")  # pylint: disable=consider-using-with

    thead = textwrap.dedent("""
        <tr>
            <th>PR</th>
            <th>Author</th>
            <th>Reference</th>
            <th>Build Number</th>
            <th>Config</th>
            <th>profile</th>
            <th>build</th>
            <th>test</th>
            <th>date</th>
        </tr>""")

    html_file.write(textwrap.dedent("""\
        <!DOCTYPE html>
        <html lang="en">

        <head>
            <title>ConanCenter - summary</title>
            <link rel="stylesheet" type="text/css" href="https://cdn.datatables.net/1.12.1/css/jquery.dataTables.min.css"/>
            <style>
                tr td {
                    white-space: nowrap;
                }
            </style>
        </head>

        <body>
            <script src="https://code.jquery.com/jquery-3.6.0.slim.min.js"
                    crossorigin="anonymous"></script>
            <script type="text/javascript" src="https://cdn.datatables.net/1.12.1/js/jquery.dataTables.min.js"></script>
            <script>
                $(document).ready( function () {

                    // Setup - add a text input to each footer cell
                    $('#summary tfoot th').each(function () {
                        var title = $(this).text();
                        $(this).html('<input type="text" placeholder="Filter ' + title + '" style="width:100%"/>');
                    });

                    $('#summary').DataTable({
                        scrollX: true,
                        scrollY: '80vh',
                        scrollCollapse: true,
                        paging: false,
                        order: [[8, 'desc']],
                        initComplete: function () {
                            // Apply the search
                            this.api()
                                .columns()
                                .every(function () {
                                    var that = this;

                                    $('input', this.footer()).on('keyup change clear', function () {
                                        if (that.search() !== this.value) {
                                            that.search(this.value).draw();
                                        }
                                    });
                                });
                        },
                    });
                } );
            </script>
            <table id="summary" class="stripe hover order-column row-border compact nowrap" style="width:100%">
            """))
    html_file.write(f"<thead>{thead}</thead><tbody>")

    append_to_file("This page lists all the ongoing pull requests on conan-center-index.\\\n", "index.md")
    url = "{{ site.url }}/conan-center-pr-status/author/author_handle"
    append_to_file(f"You can filter by author by going to [{url}]({url}).\\\n", "index.md")
    url = "{{ site.url }}/conan-center-pr-status/pr/pr_number"
    append_to_file(f"You can view a specific PR by going to [{url}]({url}).\n\n", "index.md")
    url = "{{ site.url }}/conan-center-pr-status/table"
    append_to_file(f"You can view all the jobs in tabular view by going to [{url}]({url}).\n\n", "index.md")

    for pr in prs:
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
        html_file.write(html)

        print(md)
        with open(f"_includes/{pr['number']}.md", "w", encoding="latin_1") as text_file:
            text_file.write(md)
        md = "{% include " + str(pr['number']) + ".md %}\n"
        append_to_file(md, f"pr/{pr['number']}.md")
        append_to_file(md, "index.md")
        append_to_file(md, f"author/{pr['author']['login']}.md")
        if all(label["name"] not in ["User-approval pending", "Unexpected Error"] for label in pr['labels']) and \
           all(check.get("context", "") != "continuous-integration/jenkins/pr-merge" or check.get("state", "") not in ["ERROR", "SUCCESS"] for check in pr["statusCheckRollup"] or []):
            append_to_file(md, "in_progress.md")
    html_file.write(f"</tbody><tfoot>{thead}</tfoot></table></body></html>")
