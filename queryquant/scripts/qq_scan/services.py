"""Find the services under a scan root and the Java files and Liquibase master that belong to each.

A service (module) is a directory with `src/main/java`, or with a Liquibase master at
`src/main/resources/migrations.xml` / `changelog.xml`. Its Java files are `src/main/java/**/*.java`.
Always skipped: build output (`bin`, `build`, `target`, `out`), `node_modules`, dot-directories,
`src/test`, `src/scenarioTest`, Groovy files, and anything the config's ignore globs match.

Naming: the first directory under the root that holds the module (`services/billing/billing-service`
scanned from `services` is "billing"). When the root itself is one project (it has a
`settings.gradle`, `build.gradle` or `src/`), its own folder name is used. Two modules under one name
become "name/module".
"""
import logging
import os

log = logging.getLogger("qq_scan.services")

SKIP_DIRS = {"bin", "build", "target", "out", "node_modules", "__pycache__"}
SKIP_PATHS = ("src/test", "src/scenarioTest")
MASTER_NAMES = ("migrations.xml", "changelog.xml")


class Service:
    """One module: name, absolute root, Liquibase master (absolute or None), Java files (absolute)."""

    def __init__(self, name, root, master, java_files):
        self.name = name
        self.root = root
        self.master = master
        self.java_files = java_files

    def __repr__(self):
        return "Service(%s, %s, master=%s, %d java files)" % (self.name, self.root, self.master,
                                                             len(self.java_files))


def _rel(root, path):
    return os.path.relpath(path, root).replace(os.sep, "/")


def _skip_dir(root, dirpath, name, config):
    if name in SKIP_DIRS or name.startswith("."):
        return True
    rel = _rel(root, os.path.join(dirpath, name))
    if any(rel == p or rel.endswith("/" + p) for p in SKIP_PATHS):
        return True
    return config.is_ignored(rel + "/")


def _find_master(module):
    resources = os.path.join(module, "src", "main", "resources")
    for name in MASTER_NAMES:
        candidate = os.path.join(resources, name)
        if os.path.isfile(candidate):
            return candidate
    return None


def _java_files(root, module, config):
    base = os.path.join(module, "src", "main", "java")
    found = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if not _skip_dir(root, dirpath, d, config))
        for fn in sorted(filenames):
            path = os.path.join(dirpath, fn)
            rel = _rel(root, path)
            if not fn.endswith(".java"):
                if fn.endswith(".groovy"):
                    log.debug("skip groovy file %s", rel)
                continue
            if config.is_ignored(rel):
                continue
            log.debug("file considered: %s", rel)
            found.append(path)
    return found


def find_services(root, config, only=None):
    """Return Service objects under `root`, sorted by name. `only` keeps one service by name."""
    root = os.path.abspath(root)
    log.debug("find_services enter root=%s only=%s", root, only)
    modules = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not _skip_dir(root, dirpath, d, config))
        if "src" in dirnames:
            has_java = os.path.isdir(os.path.join(dirpath, "src", "main", "java"))
            master = _find_master(dirpath)
            if has_java or master:
                modules.append((dirpath, master))
                log.debug("module found: %s (java=%s, master=%s)", _rel(root, dirpath) or ".", has_java,
                          master and _rel(root, master))
            # A module's own src tree never holds further modules.
            dirnames.remove("src")
    root_is_project = any(os.path.exists(os.path.join(root, f))
                          for f in ("settings.gradle", "settings.gradle.kts", "build.gradle", "src"))
    groups = {}
    for module, master in modules:
        rel = _rel(root, module)
        top = os.path.basename(root) if (rel == "." or root_is_project) else rel.split("/")[0]
        groups.setdefault(top, []).append((module, master))
    services = []
    for top, members in sorted(groups.items()):
        for module, master in members:
            name = top if len(members) == 1 else "%s/%s" % (top, os.path.basename(module))
            if only and only not in (name, top, os.path.basename(module)):
                log.debug("skip service %s (--service %s)", name, only)
                continue
            svc = Service(name, module, master, _java_files(root, module, config))
            log.debug("service: %r", svc)
            services.append(svc)
    log.debug("find_services exit: %d services", len(services))
    return services
