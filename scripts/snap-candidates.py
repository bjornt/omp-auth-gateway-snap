#!/usr/bin/env python3
"""Discover and recheck explicit latest/candidate revisions in the public Store."""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


SNAP = "omp-auth-gateway"
ARCHITECTURES = ("amd64", "arm64")
RUNNERS = {"amd64": "ubuntu-26.04", "arm64": "ubuntu-26.04-arm"}
UPSTREAM_ARCHITECTURES = {"amd64": "x64", "arm64": "arm64"}


def channels():
    request = urllib.request.Request(
        f"https://api.snapcraft.io/v2/snaps/info/{SNAP}",
        headers={"Snap-Device-Series": "16", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        entries = json.load(response)["channel-map"]
    if not isinstance(entries, list):
        raise ValueError("Store channel-map is not an array")

    revisions = {"candidate": {}, "stable": {}}
    for entry in entries:
        channel = entry["channel"]
        architecture = channel["architecture"]
        risk = channel["risk"]
        if (
            architecture not in ARCHITECTURES
            or channel["track"] != "latest"
            or risk not in revisions
            or channel["name"] not in (risk, f"latest/{risk}")
        ):
            continue
        revision = entry["revision"]
        if isinstance(revision, bool) or not str(revision).isdigit() or int(revision) <= 0:
            raise ValueError(f"Invalid {architecture} {risk} revision: {revision!r}")
        if architecture in revisions[risk]:
            raise ValueError(f"Duplicate latest/{risk} entry for {architecture}")
        revisions[risk][architecture] = str(revision)
    return revisions


def discover(force):
    revisions = channels()
    candidates = revisions["candidate"]
    complete = all(architecture in candidates for architecture in ARCHITECTURES)
    run = complete and (force or candidates != revisions["stable"])
    if not complete:
        missing = ", ".join(arch for arch in ARCHITECTURES if arch not in candidates)
        print(f"No promotion: latest/candidate is absent for {missing}.")
    elif not run:
        print("No promotion: both candidate revisions are already latest/stable.")
    else:
        print(f"Testing latest/candidate revisions: {json.dumps(candidates, sort_keys=True)}")

    matrix = {
        "include": [
            {
                "architecture": arch,
                "runner": RUNNERS[arch],
                "upstream_architecture": UPSTREAM_ARCHITECTURES[arch],
                "revision": candidates[arch],
            }
            for arch in ARCHITECTURES
        ] if run else []
    }
    outputs = {
        "run": str(run).lower(),
        "matrix": json.dumps(matrix, separators=(",", ":")),
        "amd64": candidates.get("amd64", ""),
        "arm64": candidates.get("arm64", ""),
    }
    destination = os.environ.get("GITHUB_OUTPUT")
    if destination:
        with open(destination, "a", encoding="utf-8") as output:
            for name, value in outputs.items():
                output.write(f"{name}={value}\n")
    else:
        print(json.dumps(outputs, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    discovery = commands.add_parser("discover")
    discovery.add_argument("--force", action="store_true", help="Retest already-stable candidates")
    verification = commands.add_parser("verify")
    for architecture in ARCHITECTURES:
        verification.add_argument(f"--{architecture}", required=True)
    args = parser.parse_args()

    if args.command == "discover":
        discover(args.force)
        return

    tested = {arch: getattr(args, arch) for arch in ARCHITECTURES}
    candidates = channels()["candidate"]
    if candidates != tested:
        raise ValueError(
            "Candidate revisions changed or disappeared; refusing stale promotion. "
            f"Tested: {json.dumps(tested, sort_keys=True)}; "
            f"current: {json.dumps(candidates, sort_keys=True)}"
        )
    print(f"Candidate revisions still match tested revisions: {json.dumps(tested, sort_keys=True)}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError) as error:
        print(f"Snap candidate discovery failed: {error}", file=sys.stderr)
        sys.exit(1)
