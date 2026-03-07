#!/usr/bin/env python3
"""
Deployment Verification Script
===============================
Verifies that all security fixes are working correctly.

Usage:
    python verify_deployment.py http://localhost:5000
    python verify_deployment.py https://your-production-domain.com
"""
import sys
import requests
import json
from typing import Tuple

# Colors for terminal output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'


def print_header(text: str):
    """Print a section header."""
    print(f"\n{BLUE}{'=' * 70}{RESET}")
    print(f"{BLUE}{text}{RESET}")
    print(f"{BLUE}{'=' * 70}{RESET}\n")


def print_test(name: str, passed: bool, message: str = ""):
    """Print test result."""
    status = f"{GREEN}✓ PASS{RESET}" if passed else f"{RED}✗ FAIL{RESET}"
    print(f"{status} - {name}")
    if message:
        print(f"       {message}")


def test_health_check(base_url: str) -> Tuple[bool, str]:
    """Test basic health check endpoint."""
    try:
        response = requests.get(f"{base_url}/health", timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data.get('status') == 'healthy':
                return True, "Health check passed"
        return False, f"Status: {response.status_code}"
    except Exception as e:
        return False, str(e)


def test_readiness_check(base_url: str) -> Tuple[bool, str]:
    """Test readiness check endpoint."""
    try:
        response = requests.get(f"{base_url}/health/ready", timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get('status') == 'ready':
                checks = data.get('checks', {})
                failed = [k for k, v in checks.items() if v.get('status') != 'healthy']
                if failed:
                    return False, f"Failed checks: {', '.join(failed)}"
                return True, "All dependencies healthy"
        return False, f"Status: {response.status_code}"
    except Exception as e:
        return False, str(e)


def test_rate_limiting(base_url: str) -> Tuple[bool, str]:
    """Test rate limiting on login endpoint."""
    try:
        # Make 6 login attempts (limit is 5)
        for i in range(6):
            response = requests.post(
                f"{base_url}/admin/login",
                data={'username': 'test', 'password': 'wrong'},
                timeout=5,
                allow_redirects=False
            )
            
            # 6th attempt should be rate limited
            if i == 5:
                if response.status_code == 429:
                    return True, "Rate limiting working (blocked at 6th attempt)"
                else:
                    return False, f"Expected 429, got {response.status_code}"
        
        return False, "Rate limiting not triggered"
    except Exception as e:
        return False, str(e)


def test_input_validation(base_url: str) -> Tuple[bool, str]:
    """Test input validation on OTP endpoint."""
    try:
        # Test with invalid mobile number
        response = requests.post(
            f"{base_url}/api/chat/send-otp",
            json={'mobile': '123'},  # Invalid: too short
            timeout=5
        )
        
        if response.status_code == 400:
            data = response.json()
            if 'Invalid mobile number' in data.get('message', ''):
                return True, "Input validation working"
        
        return False, f"Expected 400 with validation error, got {response.status_code}"
    except Exception as e:
        return False, str(e)


def test_metrics_endpoint(base_url: str) -> Tuple[bool, str]:
    """Test metrics endpoint."""
    try:
        response = requests.get(f"{base_url}/health/metrics", timeout=5)
        if response.status_code == 200:
            data = response.json()
            if 'database' in data and 'rate_limiter' in data:
                return True, f"Metrics available (DB: {data['database'].get('total_voters', 0)} voters)"
        return False, f"Status: {response.status_code}"
    except Exception as e:
        return False, str(e)


def test_admin_no_default_creds(base_url: str) -> Tuple[bool, str]:
    """Test that default credentials don't work."""
    try:
        # Try old default credentials
        response = requests.post(
            f"{base_url}/admin/login",
            data={'username': 'admin', 'password': 'admin'},
            timeout=5,
            allow_redirects=False
        )
        
        # Should fail (either 302 redirect to login with error, or direct error)
        if response.status_code in [302, 401, 403]:
            return True, "Default credentials rejected"
        
        return False, f"Default credentials might still work (status: {response.status_code})"
    except Exception as e:
        return False, str(e)


def main():
    """Run all verification tests."""
    if len(sys.argv) < 2:
        print(f"{RED}Error: Base URL required{RESET}")
        print(f"Usage: python verify_deployment.py <base_url>")
        print(f"Example: python verify_deployment.py http://localhost:5000")
        sys.exit(1)
    
    base_url = sys.argv[1].rstrip('/')
    
    print_header("DEPLOYMENT VERIFICATION - Security Fixes v4.1")
    print(f"Testing: {base_url}\n")
    
    # Run tests
    tests = [
        ("Health Check Endpoint", test_health_check),
        ("Readiness Check (Dependencies)", test_readiness_check),
        ("Rate Limiting", test_rate_limiting),
        ("Input Validation", test_input_validation),
        ("Metrics Endpoint", test_metrics_endpoint),
        ("Default Credentials Disabled", test_admin_no_default_creds),
    ]
    
    results = []
    for name, test_func in tests:
        passed, message = test_func(base_url)
        print_test(name, passed, message)
        results.append((name, passed))
    
    # Summary
    print_header("SUMMARY")
    passed_count = sum(1 for _, passed in results if passed)
    total_count = len(results)
    
    if passed_count == total_count:
        print(f"{GREEN}✓ All tests passed ({passed_count}/{total_count}){RESET}")
        print(f"\n{GREEN}Deployment verification successful!{RESET}")
        sys.exit(0)
    else:
        print(f"{YELLOW}⚠ {passed_count}/{total_count} tests passed{RESET}")
        print(f"\n{RED}Some tests failed. Please review the output above.{RESET}")
        sys.exit(1)


if __name__ == '__main__':
    main()
