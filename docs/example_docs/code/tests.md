# Testing

## Overview

Writing effective tests for your code is a crucial part of the programming process. It is the best way to ensure that changes you make to your codebase throughout the development process do not break the core functionality of your code. This may be your first time writing tests, but trust me that it is essential.

## Pytest

Put any unit tests in the `/tests` folder. A sample test (i.e. `/tests/sample/examples/test_sample.py`) is included as a representative example.

!!! Note

    All your testing scripts should start with `test_` in the filename.

When you installed the package with the `[dev]` extras, you installed everything you need to run your unit tests. To run the unit tests locally, run `pytest .` in the base directory. It will let you know if any tests fail and what the reason is for each failure.

## Code Coverage

Code coverage is handled directly within the GitHub Actions workflow. The workflow generates HTML coverage reports and XML reports that can be downloaded as artifacts from each CI run.

When a test run completes, GitHub will store the coverage reports which you can access by:

1. Going to the Actions tab in your GitHub repository
2. Clicking on the specific workflow run
3. Scrolling down to the Artifacts section
4. Downloading the "coverage-report-X.Y" artifact (where X.Y is the Python version)

The coverage reports help you identify which parts of your code are not being tested, allowing you to improve your test suite over time.
