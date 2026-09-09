"""CI-only smoke checks against a container with synthetic data and a dummy token."""
import argparse
import json
import time
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:18000")
    args = parser.parse_args()
    token = "ci-smoke-token"

    def request(path, authenticated=False, body=None):
        headers = {"Content-Type": "application/json"}
        if authenticated:
            headers["X-Dashboard-Token"] = token
        req = urllib.request.Request(args.url + path, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.load(response)

    for attempt in range(30):
        try:
            assert request("/api/health")["ok"]
            break
        except (OSError, AssertionError):
            if attempt == 29:
                raise
            time.sleep(1)
    assert request("/api/auth/status")["configured"] is True
    try:
        request("/api/metrics?background=1")
        raise AssertionError("Unauthenticated metrics should not be accessible")
    except urllib.error.HTTPError as exc:
        assert exc.code == 401
    with urllib.request.urlopen(args.url + "/") as response:
        html = response.read().decode()
        assert 'data-channel-drill="SmartPush"' in html
    request("/api/sync", authenticated=True, body={"range": "1d", "background": True})
    for attempt in range(30):
        data = request("/api/metrics?range=1d&background=1", authenticated=True)
        if not data.get("pending") and not data["refresh"]["running"]:
            assert data["source"]["mode"] == "sample"
            assert not data["source"]["errors"]
            break
        time.sleep(1)
    else:
        raise AssertionError("Background sample query never completed")
    print("Container smoke passed: health, auth, static, background snapshot; synthetic data only.")


if __name__ == "__main__":
    main()
