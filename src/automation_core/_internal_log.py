import logging

# Logger for the library's own operational messages (e.g. "notification dispatch failed").
# Never exposed to callers; lives under a separate namespace so projects can filter it out.
logger = logging.getLogger("automation_core._internal")
