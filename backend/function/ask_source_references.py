"""Request-local source references; never expose entry identifiers to the model."""
import re
import uuid


def valid_source_entry_id(value):
    return isinstance(value, str) and bool(re.fullmatch(r"entry_[A-Za-z0-9_-]{1,160}", value))


def register_source(registry, entry_id):
    if registry is None or not valid_source_entry_id(entry_id):
        return {}
    reference = "source_" + uuid.uuid4().hex
    registry[reference] = entry_id
    return {"sourceRef": reference}


def source_reference(value):
    ref = value.get("sourceRef")
    if isinstance(ref, str) and re.fullmatch(r"source_[a-f0-9]{32}", ref):
        return {"sourceRef": ref}
    return {}


def resolve_answer_sources(answer, registry):
    for evidence in answer.get("evidence", []):
        reference = evidence.pop("sourceRef", None)
        entry_id = (registry or {}).get(reference)
        if valid_source_entry_id(entry_id):
            evidence["sourceEntryId"] = entry_id
    return answer
