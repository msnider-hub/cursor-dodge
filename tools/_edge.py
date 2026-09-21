"""Launching headless Edge, the same way for all four tools here.

Every load gets its own throwaway profile directory. Sharing one per scenario was
the difference between a suite that always finishes and a suite that now and then
hung for ten minutes on a page that normally takes a second — a warm profile can
sit there doing Edge housekeeping instead of running the page. A load that times
out anyway is retried once, and the profile is deleted either way.
"""
import os, shutil, subprocess, tempfile

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

FLAGS = [
    "--headless=new", "--disable-gpu", "--no-sandbox", "--mute-audio",
    "--no-first-run", "--disable-extensions", "--disable-sync",
    "--disable-background-networking", "--disable-component-update",
    "--dump-dom",
]


def dump(path, timeout=600, tries=2, extra=()):
    """Load a local HTML file and return the DOM Edge printed on exit."""
    failure = None
    for _ in range(tries):
        profile = tempfile.mkdtemp(prefix="cd_edge_")
        try:
            return subprocess.run(
                [EDGE] + FLAGS + list(extra) +
                ["--user-data-dir=" + profile,
                 "file:///" + os.path.abspath(path).replace("\\", "/")],
                capture_output=True, text=True, timeout=timeout).stdout
        except subprocess.TimeoutExpired as e:
            failure = e
        finally:
            shutil.rmtree(profile, ignore_errors=True)
    raise failure
