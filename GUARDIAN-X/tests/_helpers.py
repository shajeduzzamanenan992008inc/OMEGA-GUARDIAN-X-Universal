def _commit_responses_one_blob(existing=None, blob_sha="1" * 40, new_commit="9" * 40):
    tree_entries = [
        {"path": path, "type": "blob", "sha": sha} for path, sha in (existing or {}).items()
    ]
    return [
        ref(BASE),
        response(payload={"sha": BASE, "tree": {"sha": TREE}}),
        response(payload={"sha": TREE, "tree": tree_entries, "truncated": False}),
        response(status=201, payload={"sha": blob_sha}),
        response(status=201, payload={"sha": "8" * 40}),
        response(status=201, payload={"sha": new_commit}),
        response(payload={"ref": "refs/heads/feat/x", "object": {"sha": new_commit}}),
    ]
