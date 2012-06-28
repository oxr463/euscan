from __future__ import print_function

import os
import sys
from datetime import datetime

import portage
from portage.dbapi import porttree

import gentoolkit.pprinter as pp
from gentoolkit.query import Query
from gentoolkit.package import Package

from euscan import CONFIG, BLACKLIST_PACKAGES
from euscan import handlers, helpers, output
from euscan.ebuild import package_from_ebuild


def filter_versions(cp, versions):
    filtered = {}

    for url, version, handler, confidence in versions:

        # Try to keep the most specific urls (determinted by the length)
        if version in filtered and len(url) < len(filtered[version]):
            continue

        # Remove blacklisted versions
        if helpers.version_blacklisted(cp, version):
            continue

        filtered[version] = {
            "url": url,
            "handler": handler,
            "confidence": confidence
        }

    return [
        (cp, filtered[version]["url"], version, filtered[version]["handler"],
         filtered[version]["confidence"])
        for version in filtered
    ]


def scan_upstream_urls(cpv, urls, on_progress):
    versions = []

    progress_available = 70
    num_urls = sum([len(urls[fn]) for fn in urls])
    progress_increment = progress_available / num_urls

    for filename in urls:
        for url in urls[filename]:

            if on_progress and progress_available > 0:
                on_progress(increment=progress_increment)
                progress_available -= progress_increment

            if not CONFIG['quiet'] and not CONFIG['format']:
                pp.uprint()
            output.einfo("SRC_URI is '%s'" % url)

            if '://' not in url:
                output.einfo("Invalid url '%s'" % url)
                continue

            # Try normal scan
            if CONFIG["scan-dir"]:
                try:
                    versions.extend(handlers.scan(cpv, url))
                except Exception as e:
                    output.ewarn("Handler failed: [%s] %s"
                            % (e.__class__.__name__, e.message))

            if versions and CONFIG['oneshot']:
                break

            # Brute Force
            if CONFIG["brute-force"] > 0:
                versions.extend(handlers.brute_force(cpv, url))

            if versions and CONFIG['oneshot']:
                break

    cp, ver, rev = portage.pkgsplit(cpv)

    result = filter_versions(cp, versions)

    if on_progress and progress_available > 0:
        on_progress(increment=progress_available)

    return result


# gentoolkit stores PORTDB, so even if we modify it to add an overlay
# it will still use the old dbapi
def reload_gentoolkit():
    from gentoolkit import dbapi
    import gentoolkit.package
    import gentoolkit.query

    PORTDB = portage.db[portage.root]["porttree"].dbapi
    dbapi.PORTDB = PORTDB

    if hasattr(dbapi, 'PORTDB'):
        dbapi.PORTDB = PORTDB
    if hasattr(gentoolkit.package, 'PORTDB'):
        gentoolkit.package.PORTDB = PORTDB
    if hasattr(gentoolkit.query, 'PORTDB'):
        gentoolkit.query.PORTDB = PORTDB


def scan_upstream(query, on_progress=None):
    """
    Scans the upstream searching new versions for the given query
    """

    matches = []

    if query.endswith(".ebuild"):
        cpv = package_from_ebuild(query)
        if cpv:
            reload_gentoolkit()
            matches = [Package(cpv)]
    else:
        matches = Query(query).find(
            include_masked=True,
            in_installed=False
        )

    if not matches:
        output.ewarn(
            pp.warn("No package matching '%s'" % pp.pkgquery(query))
        )
        return None

    matches = sorted(matches)
    pkg = matches.pop()

    while '9999' in pkg.version and len(matches):
        pkg = matches.pop()

    if not pkg:
        output.ewarn(
            pp.warn("Package '%s' only have a dev version (9999)"
                    % pp.pkgquery(pkg.cp))
        )
        return None

    # useful data only for formatted output
    start_time = datetime.now()
    output.metadata("datetime", start_time.isoformat(), show=False)
    output.metadata("cp", pkg.cp, show=False)
    output.metadata("cpv", pkg.cpv, show=False)

    if on_progress:
        on_progress(increment=10)

    if pkg.cp in BLACKLIST_PACKAGES:
        output.ewarn(
            pp.warn("Package '%s' is blacklisted" % pp.pkgquery(pkg.cp))
        )
        return None

    if not CONFIG['quiet']:
        if not CONFIG['format']:
            pp.uprint(
                " * %s [%s]" % (pp.cpv(pkg.cpv), pp.section(pkg.repo_name()))
            )
            pp.uprint()
        else:
            output.metadata("overlay", pp.section(pkg.repo_name()))

        ebuild_path = pkg.ebuild_path()
        if ebuild_path:
            output.metadata(
                "ebuild", pp.path(os.path.normpath(ebuild_path))
            )

        output.metadata("repository", pkg.repo_name())
        output.metadata("homepage", pkg.environment("HOMEPAGE"))
        output.metadata("description", pkg.environment("DESCRIPTION"))

    cpv = pkg.cpv
    metadata = {
        "EAPI": portage.settings["EAPI"],
        "SRC_URI": pkg.environment("SRC_URI", False),
    }
    use = frozenset(portage.settings["PORTAGE_USE"].split())
    try:
        alist = porttree._parse_uri_map(cpv, metadata, use=use)
        aalist = porttree._parse_uri_map(cpv, metadata)
    except Exception as e:
        output.ewarn(pp.warn("%s\n" % str(e)))
        output.ewarn(
            pp.warn("Invalid SRC_URI for '%s'" % pp.pkgquery(cpv))
        )
        return None

    if "mirror" in portage.settings.features:
        urls = aalist
    else:
        urls = alist

    # output scan time for formatted output
    scan_time = (datetime.now() - start_time).total_seconds()
    output.metadata("scan_time", scan_time, show=False)

    result = scan_upstream_urls(pkg.cpv, urls, on_progress)

    if on_progress:
        on_progress(increment=10)

    if len(result) > 0:
        if not (CONFIG['format'] or CONFIG['quiet']):
            print("\n", file=sys.stderr)

        for cp, url, version, handler, confidence in result:
            output.result(cp, version, url, handler, confidence)

    return result
