"""In-process transport to the existing simulator request API."""
import uuid,json,gzip
from pathlib import Path

class LocalPlannerClient:
    """In-process transport to the same token/ownership/validation dispatcher."""
    def __init__(self, platform, journal=None):
        self.platform, self.journal = platform, Path(journal) if journal is not None else None
        self.token = platform.token()
        self.writer_id, self.connection_id = uuid.uuid4().hex, uuid.uuid4().hex

    def call(self, method, args=None, *, mutating=False):
        reply = self.platform.request(method, args, token=self.token if mutating else None,
            request_id=uuid.uuid4().hex if mutating else None,
            writer_id=self.writer_id if mutating else None, connection_id=self.connection_id)
        self.token = reply["token"]
        if self.journal is None:return reply
        opener=gzip.open if self.journal.suffix==".gz" else open
        kwargs={"compresslevel":1} if self.journal.suffix==".gz" else {}
        with opener(self.journal,"at",encoding="utf-8",**kwargs) as stream:
            stream.write(json.dumps({"method": method, "args": args or {}, "reply": reply},
                                    allow_nan=False, separators=(",", ":")) + "\n")
        return reply


