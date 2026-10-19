"""Bounded, UI-thread cache of already loaded relational object metadata.

No driver, filesystem, or credential access. Completion is a presentation hint;
the signed table/owner APIs remain authoritative for every database operation.
"""

from collections import OrderedDict
from dataclasses import dataclass

from tools.data_center.object_tree import TreeTarget, VerifiedTableRef


MAX_COMPLETION_TABLES = 2000
MAX_COMPLETION_COLUMNS = 1024
MAX_COMPLETION_BYTES = 2 * 1024 * 1024
_PROVIDERS = {"mysql", "oracle", "oceanbase_mysql", "oceanbase_oracle"}


@dataclass(frozen=True)
class _Entry:
    ref: VerifiedTableRef
    parent: str
    columns: tuple[str, ...] = ()

    @property
    def key(self):
        return self.ref.database, self.ref.schema, self.ref.raw_name

    @property
    def size(self):
        return sum(len(value.encode("utf-8")) for value in (*self.key, *self.columns))


class CompletionCatalog:
    def __init__(self, *, allow_unseeded=True):
        self.target = None
        self._unseeded = allow_unseeded
        self._allow_unlisted = allow_unseeded
        self.generation = -1
        self._entries = OrderedDict()
        self._epochs = OrderedDict()
        self._bytes = 0

    def reset(self, target=None, generation=-1):
        self._unseeded = False
        self.target = target if isinstance(target, TreeTarget) and target.provider in _PROVIDERS else None
        self.generation = generation
        self._entries.clear()
        self._epochs.clear()
        self._bytes = 0

    def _remove(self, key):
        self._bytes -= self._entries.pop(key).size

    def _put(self, entry):
        if entry.key in self._entries:
            self._remove(entry.key)
        if entry.size > MAX_COMPLETION_BYTES:
            return False
        self._entries[entry.key] = entry
        self._bytes += entry.size
        while len(self._entries) > MAX_COMPLETION_TABLES or self._bytes > MAX_COMPLETION_BYTES:
            self._remove(next(iter(self._entries)))
        return True

    def accept_page(self, page, refs, request_cursor=None):
        """Consume accepted directory results with raw names resolved in memory."""
        if page.target != self.target or page.generation != self.generation:
            return False
        parent = page.parent_ref
        previous = self._epochs.get(parent, -1)
        if page.refresh_epoch < previous:
            return False
        if not request_cursor or page.refresh_epoch > previous:
            for key, entry in tuple(self._entries.items()):
                if entry.parent == parent:
                    self._remove(key)
        self._epochs[parent] = page.refresh_epoch
        self._epochs.move_to_end(parent)
        while len(self._epochs) > MAX_COMPLETION_TABLES:
            old_parent, _ = self._epochs.popitem(last=False)
            for key, entry in tuple(self._entries.items()):
                if entry.parent == old_parent:
                    self._remove(key)
        for ref in refs:
            if (isinstance(ref, VerifiedTableRef) and ref.target == self.target
                    and ref.tree_generation == self.generation and ref.refresh_epoch == page.refresh_epoch):
                self._put(_Entry(ref, parent))
        return True

    def learn_table(self, metadata):
        ref = metadata.signed_ref
        if not isinstance(ref, VerifiedTableRef):
            return False
        if self.target is None and self._unseeded:
            self.reset(ref.target, ref.tree_generation)
        if ref.target != self.target or ref.tree_generation != self.generation:
            return False
        key = ref.database, ref.schema, ref.raw_name
        existing = self._entries.get(key)
        if existing is None and not self._allow_unlisted:
            return False  # Refreshed/evicted nodes cannot reappear via a late result.
        if existing and existing.ref != ref:
            return False  # A table result captured before a directory refresh.
        target = metadata.target
        mysql = ref.target.provider.endswith("mysql")
        scope_ok = (target.schema in {"", target.database} and ref.schema in {"", target.database}
                    if mysql else target.schema == ref.schema)
        if (target.connection_id != ref.target.connection_id
                or target.profile_revision != ref.target.profile_revision
                or target.database != ref.database or not scope_ok):
            return False
        names = tuple(column.name for column in metadata.columns[:MAX_COMPLETION_COLUMNS])
        self._put(_Entry(ref, existing.parent if existing else "", names))
        return True

    def snapshot(self, target, provider):
        """Project the legacy editor DTO for one immutable manual tab target."""
        result = {"dialect": provider, "objects": []}
        if (self.target is None or self.target.provider != provider
                or self.target.connection_id != target.connection_id
                or self.target.profile_revision != target.profile_revision):
            return result
        for entry in self._entries.values():
            ref = entry.ref
            if (ref.database != target.database
                    or provider.endswith("oracle") and ref.schema != target.schema):
                continue
            owner = ref.schema if provider.endswith("oracle") else ref.database
            result["objects"].append({
                "name": ref.raw_name, "owner": owner,
                "columns": [{"name": name} for name in entry.columns],
            })
        return result
