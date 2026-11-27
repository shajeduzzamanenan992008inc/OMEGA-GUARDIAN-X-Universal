from ogx.platform.transport import HttpResponse
import json

BASE = "a" * 40
TREE = "c" * 40

def ref(sha: str) -> HttpResponse:
    return response(payload={"ref": "refs/heads/main", "object": {"sha": sha}})


def response(status: int = 200, payload=None, body: bytes | None = None) -> HttpResponse:
    if body is None:
        body = json.dumps({} if payload is None else payload).encode()
    return HttpResponse(status=status, headers={}, body=body, url="")


def _commit_responses_one_blob(existing=None, blob_sha="1" * 40, new_commit="9" * 40):
    tree_entries = [
        {"path": path, "type": "blob", "sha": sha} for path, sha in (existing or {}).items()
    ]
    # create_commit needs:
    # 1. _branch_sha - GET /git/ref/heads/branch -> ref(BASE)
    # 2. GET /git/commits/{parent} -> commit data with tree
    # 3. _tree_blob_shas - GET /git/trees/{ref} -> tree entries
    # 4. POST /git/blobs -> blob SHA
    # 5. POST /git/trees -> tree SHA
    # 6. POST /git/commits -> commit SHA
    # update_reference needs:
    # 7. _branch_sha - GET /git/ref/heads/branch -> ref(BASE)
    # 8. PATCH /git/refs/heads/branch -> updated ref
    return [
        # For _branch_sha in create_commit
        ref(BASE),
        # For GET /git/commits/{parent}
        response(payload={"sha": BASE, "tree": {"sha": TREE}}),
        # For _tree_blob_shas
        response(payload={"sha": TREE, "tree": tree_entries, "truncated": False}),
        # For POST /git/blobs
        response(status=201, payload={"sha": blob_sha}),
        # For POST /git/trees
        response(status=201, payload={"sha": "8" * 40}),
        # For POST /git/commits
        response(status=201, payload={"sha": new_commit}),
        # For _branch_sha in update_reference
        ref(BASE),
        # For PATCH /git/refs/heads/branch
        response(payload={"ref": "refs/heads/feat/x", "object": {"sha": new_commit}}),
    ]
