"""Official EODHD Python library, vendored at upstream commit f98e69d (MIT, see LICENSE).

https://github.com/EodHistoricalData/EODHD-APIs-Python-Financial-Library/tree/f98e69d797140ca03f16fa7451046d84aab9643e
Only the REST client is kept: eodhdgraphs.py (matplotlib) and websocketclient.py
(websockets) are dropped, and absolute `eodhd.` imports point at this package.
PyPI rejects direct-URL dependencies, and PyPI's own `eodhd` (1.0.32) predates
the typed errors, timeouts and fundamentals `filter` this extension relies on.
"""

from openbq_eodhd._vendor.eodhd.apiclient import APIClient, ScannerClient
from openbq_eodhd._vendor.eodhd.errors import (
    EODHDConnectionError,
    EODHDError,
    EODHDHTTPError,
    EODHDTimeoutError,
)

__version__ = "1.4.0"
