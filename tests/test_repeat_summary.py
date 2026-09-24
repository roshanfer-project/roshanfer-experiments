"""Exit status and repeat summary for failed deployment and load-generator runs."""

import io
from contextlib import redirect_stdout

from exec.executor import _failure_reason, _print_repeat_summary, _repeat_outcome
from exec.runner import _salient_output_line


def test_deployment_and_connection_refused_fail_the_run():
    deploy = _repeat_outcome(
        "latency-and-rate-vs-time-one-service-roshanfer",
        "unit",
        0,
        "error",
        {"error": "Deployment failed for roshanfer: CalledProcessError"},
    )
    refused = _repeat_outcome(
        "latency-and-rate-vs-time-one-service-roshanfer",
        "unit",
        1,
        "error",
        {"error_f1": "RWG failed on gen0 code=1: dial tcp 10.0.0.1:80: connection refused"},
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = _print_repeat_summary([deploy, refused], [])
    text = buf.getvalue()
    assert code == 1
    assert "Successful repeats: 0" in text
    assert "Failed repeats: 2" in text
    assert "repeat_000" in text and "Deployment failed" in text
    assert "repeat_001" in text and "connection refused" in text
    assert "Overall: failed" in text
    assert "Overall: success" not in text


def test_all_success_exits_zero():
    ok = _repeat_outcome("exp", "unit", 0, "success", {})
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = _print_repeat_summary([ok], [])
    assert code == 0
    assert "Successful repeats: 1" in buf.getvalue()
    assert "Overall: success" in buf.getvalue()


def test_build_failure_without_repeats_is_failure():
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = _print_repeat_summary([], [("roshanfer", "build failed: boom")])
    assert code == 1
    assert "failed system: roshanfer" in buf.getvalue()


def test_salient_line_prefers_connection_refused():
    stderr = "warning: retry\ndial tcp 10.0.0.8:8080: connection refused\n"
    assert "connection refused" in _salient_output_line(stderr)


def test_failure_reason_from_exception():
    assert _failure_reason({"exception": "not enough generator hosts"}) == "not enough generator hosts"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all passed")
