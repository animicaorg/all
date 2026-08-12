# hello-api — the minimal Animica Python Cloud function.
#
# The whole runtime ABI in one file: a module-level `main` that takes the JSON request
# and returns JSON-serializable data. No capabilities, no context object needed.
#
#   POST /api/cloud/v1/fn/{owner}/hello-api   {"name": "Ada"}
#   ->   {"greeting": "Hello, Ada!", ...}


def main(request):
    name = "world"
    if isinstance(request, dict) and request.get("name"):
        name = str(request["name"])[:80]
    return {
        "greeting": f"Hello, {name}!",
        "echo": request,
        "runtime": "python3.12",
    }
