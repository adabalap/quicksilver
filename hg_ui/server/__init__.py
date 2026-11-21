"""Quicksilver web server, split by area.

Import order matters only in that `core` comes first (it creates the Flask `app`);
importing a route module registers its routes. Dependencies run one way:
core <- highlights/fts/notes_common/settings <- dbinit/trash/actions/attachments/search ...
(no cycles — `python3 tools/check_imports.py`-style checks live in structure_test.py).
"""
MODULES = ("core", "highlights", "fts", "dbinit", "notes_common", "settings", "search",
           "trash", "routes_notes", "actions", "followups", "bills", "captures", "saved", "attachments", "importexport", "ask", "story",
           "insights", "home", "dashboard", "system", "cli")
