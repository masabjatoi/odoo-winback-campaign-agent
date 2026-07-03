import xmlrpc.client
import config
import socket

# Set default socket timeout to prevent XML-RPC calls from hanging indefinitely
socket.setdefaulttimeout(getattr(config, 'ODOO_SOCKET_TIMEOUT', 90))

class OdooClient:
    def __init__(self):
        self._url = config.ODOO_URL
        self._db = config.ODOO_DB
        self._username = config.ODOO_USERNAME
        self._password = config.ODOO_API_KEY
        self._uid = None
        self._models = None

    def authenticate(self):
        """Authenticates with Odoo and establishes the XML-RPC connection."""
        common = xmlrpc.client.ServerProxy(f"{self._url}/xmlrpc/2/common", allow_none=True)
        self._uid = common.authenticate(self._db, self._username, self._password, {})
        if not self._uid:
            raise PermissionError(
                "Odoo authentication failed. "
                "Check ODOO_URL, ODOO_DB, ODOO_USERNAME and ODOO_API_KEY in your .env file."
            )
        self._models = xmlrpc.client.ServerProxy(f"{self._url}/xmlrpc/2/object", allow_none=True)
        print(f"[Odoo] Authenticated as UID {self._uid}")

    def _do_execute(self, model: str, method: str, args: list, kwargs: dict):
        # Always use a fresh ServerProxy to prevent connection reuse timeouts (e.g. 'Idle' / 'Request-sent')
        models = xmlrpc.client.ServerProxy(f"{self._url}/xmlrpc/2/object", allow_none=True)
        return models.execute_kw(
            self._db, self._uid, self._password,
            model, method, args, kwargs or {}
        )

    def execute(self, model: str, method: str, args: list, kwargs: dict = None):
        """Generic execute command for tools to use."""
        if not self._uid:
            self.authenticate()
        
        kwargs = kwargs or {}
        try:
            return self._do_execute(model, method, args, kwargs)
        except Exception as e:
            err_str = str(e).lower()
            # Avoid false positives by ignoring errors with traceback (Python crashes/bugs)
            if "traceback" not in err_str:
                auth_keywords = ["access denied", "session", "unauthorized", "invalid uid"]
                if any(kw in err_str for kw in auth_keywords):
                    print(f"[Odoo] Auth-related error detected ({e}). Attempting re-authentication...")
                    self.authenticate()
                    return self._do_execute(model, method, args, kwargs)
            raise e

    def execute_kw(self, db, uid, password, model, method, args, kwargs=None):
        """Wrapper to match raw XML-RPC ServerProxy execute_kw signature."""
        return self.execute(model, method, args, kwargs)
