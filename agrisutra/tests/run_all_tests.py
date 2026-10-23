"""Test runner script for AgriSutra test suite."""

import sys
import unittest
import os
import time

sys_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if sys_root not in sys.path:
    sys.path.insert(0, sys_root)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

def run():
    start_time = time.time()
    loader = unittest.TestLoader()
    suite = loader.discover(os.path.dirname(__file__), pattern="test_*.py")
    
    print("\n" + "=" * 65)
    print("  🌱 AgriSutra Test Suite & Adversarial Stress Benchmark")
    print("=" * 65)

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    elapsed = time.time() - start_time

    print("\n" + "-" * 65)
    print(f"  Tests run: {result.testsRun} in {elapsed:.3f}s")
    if result.wasSuccessful():
        print("  🎉 ALL TESTS PASSED! System is fully resilient & hardened.")
    else:
        print(f"  ❌ Failures: {len(result.failures)} | Errors: {len(result.errors)}")
    print("=" * 65 + "\n")

    return 0 if result.wasSuccessful() else 1

if __name__ == "__main__":
    sys.exit(run())
